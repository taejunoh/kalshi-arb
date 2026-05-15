"""Phase 1 promotions from crypto_observer Phase 0 prevalence scanner.

After 24h of observer data (2026-05-13 → 2026-05-14), 2 of 8 observers
showed promotion-grade signal in cohort analysis:

  - deribit_tail_skew  26/26 = 100% WR across ya bands $0.03-$0.30
                       (vs break-even WR 96%/93%/77% respectively)
  - bid_imbalance      18/26 = 69.2% WR overall, mts<5min 4/4 = 100%

Both promoted to Phase 1 paper traders. Strategy is:
  1. Read fresh observer signals from `crypto_observer_signals` table
     (≤ N seconds old, not yet acted on this process lifetime)
  2. Look up current Kalshi price from scanner-cached market dict
  3. Place paper market_order; OrderManager dedup handles re-fires

No LIVE_STRATEGIES env entry — paper-only at launch. Phase 2 promotion
to live trading gated on n ≥ 100 settled paper trades + ROI ≥ +5%.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("crypto_observer_phase1")


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _Phase1Base:
    """Shared logic — subclass sets STRATEGY_NAME, OBSERVER_NAME, freshness.

    Both subclasses look up the latest signals for their observer in the
    `crypto_observer_signals` table, dedupe by signal id (in-process),
    and place a single paper market_order per fresh signal.
    """
    STRATEGY_NAME: str = ""
    OBSERVER_NAME: str = ""
    SIGNAL_FRESHNESS_S: float = 15.0
    SIZE_USDC: float = 20.0

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._processed: set[int] = set()

    def _select_side_and_price(self, sig: dict, market: dict
                               ) -> Optional[tuple[str, float]]:
        """Translate (signal, current market) → (side, entry_price)."""
        raise NotImplementedError

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            return
        try:
            sigs = await db.fetch_recent_crypto_observer_signals(
                observer=self.OBSERVER_NAME, limit=30
            )
        except Exception as e:
            logger.error(f"{self.STRATEGY_NAME}: fetch signals failed: {e}")
            return
        if not sigs:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.SIGNAL_FRESHNESS_S)
        for sig in sigs:
            sig_id = sig["id"]
            if sig_id in self._processed:
                continue
            try:
                ts = datetime.fromisoformat(sig["ts"])
            except Exception:
                self._processed.add(sig_id)
                continue
            if ts < cutoff:
                # Aged out without action — never retroactively fire.
                self._processed.add(sig_id)
                continue
            ticker = sig["market_id"]
            market = self._scanner.get_market(ticker)
            if market is None:
                # Market dropped from scanner cache between signal and now.
                self._processed.add(sig_id)
                continue
            choice = self._select_side_and_price(sig, market)
            if choice is None:
                self._processed.add(sig_id)
                continue
            side, price = choice
            try:
                await self._order_manager.market_order(
                    ticker=ticker, side=side, size_usdc=self.SIZE_USDC,
                    price=price, strategy=self.STRATEGY_NAME, market_id=ticker,
                )
                logger.info(
                    f"[{self.STRATEGY_NAME}] FIRE {ticker} {side} "
                    f"size=${self.SIZE_USDC:.0f} price={price:.3f} "
                    f"signal_id={sig_id} pred={sig['predicted_outcome']}"
                )
            except Exception as e:
                logger.error(f"{self.STRATEGY_NAME}: market_order failed: {e}")
            self._processed.add(sig_id)

        # Bound memory: drop processed ids older than ~10× freshness window.
        if len(self._processed) > 500:
            self._processed = set(list(self._processed)[-300:])


class DeribitTailFadeV1Strategy(_Phase1Base):
    """Fades tail buckets where Kalshi yes_ask significantly exceeds the
    Deribit-IV lognormal probability. Buys NO at the current no_ask.

    Cohort evidence (24h, n=26 settled): 100% WR across ya bands
    $0.03-$0.30. Per-band EV +$0.83 / +$1.39 / +$5.81 per $20 stake.
    """
    STRATEGY_NAME = "deribit_tail_fade_v1"
    OBSERVER_NAME = "deribit_tail_skew"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.DERIBIT_TAIL_FADE_SIZE_USDC
        self._max_ya = config.DERIBIT_TAIL_FADE_MAX_YA

    def _select_side_and_price(self, sig, market):
        ya = _safe_float(market.get("yes_ask_dollars"))
        no_ask = _safe_float(market.get("no_ask_dollars"))
        if ya is None or no_ask is None:
            return None
        if ya <= 0 or no_ask <= 0 or no_ask >= 1:
            return None
        # Sanity: don't fire when yes_ask drifted above the cohort band.
        if ya > self._max_ya:
            return None
        # Observer predicts "no" — we BUY NO to express that.
        if sig["predicted_outcome"] != "no":
            return None
        return ("no", no_ask)


class BidImbalanceV1Strategy(_Phase1Base):
    """Follows top-of-book bid/ask size imbalance. Buys the predicted side
    at current ask. Cohort evidence (24h, n=26 settled): 69.2% WR
    overall; mts<5min cohort 4/4 = 100% (n small, watching).
    """
    STRATEGY_NAME = "bid_imbalance_v1"
    OBSERVER_NAME = "bid_imbalance"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.BID_IMBALANCE_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.BID_IMBALANCE_SIZE_USDC

    def _select_side_and_price(self, sig, market):
        pred = sig["predicted_outcome"]
        if pred not in ("yes", "no"):
            return None
        ask_key = "yes_ask_dollars" if pred == "yes" else "no_ask_dollars"
        price = _safe_float(market.get(ask_key))
        if price is None or price <= 0 or price >= 1:
            return None
        return (pred, price)


class OpenNoTouchV1Strategy(_Phase1Base):
    """Inverse of open_widening — predicts no-touch on ATM±1% buckets
    within 10min of event open. Cohort evidence (24h, n=325 settled):
    80% WR, +30% paper ROI. Sources signals from the `open_no_touch`
    observer (which emits flipped open_widening predictions) and BUYs
    NO at current no_ask.

    First 37 paper trades hit -10.3% ROI despite 90% Phase 0 WR — fee
    asymmetry on tail buckets (ya < 0.20 → no_ask > 0.80 → max profit
    < $5/$20 stake eaten by fee). 587-signal cohort breakdown shows EV
    concentrated in ya ≥ 0.20 (40% / 180% ROI bands). MIN_YA filter
    drops the tail-bucket fires.
    """
    STRATEGY_NAME = "open_no_touch_v1"
    OBSERVER_NAME = "open_no_touch"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.OPEN_NO_TOUCH_SIZE_USDC
        self._min_ya = config.OPEN_NO_TOUCH_MIN_YA
        self._max_fires_per_event = config.OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT
        # event_key -> fire count (in-memory, resets on restart). All-event
        # ATM±1% triggers correlate on the same close — cap fires/event so
        # 50 trades reflect ~17 independent events instead of ~5.
        self._fires_per_event: dict[str, int] = {}

    def _select_side_and_price(self, sig, market):
        if sig["predicted_outcome"] != "no":
            return None
        # Per-event cap. event_key is on the signal row (DB column), not
        # in features_json; tolerate either source.
        event_key = sig.get("event_key") or ""
        if not event_key:
            parts = sig.get("market_id", "").split("-")
            event_key = "-".join(parts[1:-1]) if len(parts) >= 3 else ""
        if event_key and self._fires_per_event.get(event_key, 0) >= self._max_fires_per_event:
            return None
        ya = _safe_float(market.get("yes_ask_dollars"))
        no_ask = _safe_float(market.get("no_ask_dollars"))
        if ya is None or no_ask is None:
            return None
        if no_ask <= 0 or no_ask >= 1:
            return None
        # Tail buckets (low ya = high no_ask) have payoff/fee asymmetry
        # that kills EV even at 90%+ WR. Restrict to ya ≥ MIN_YA so
        # max-profit-per-trade overcomes Kalshi's per-contract fee.
        if ya < self._min_ya:
            return None
        # All gates passed — optimistically increment counter. If
        # market_order then fails (rare), the counter is slightly over-
        # counted, which only causes us to fire LESS for this event.
        if event_key:
            self._fires_per_event[event_key] = self._fires_per_event.get(event_key, 0) + 1
            # Memory bound: keep dict size manageable across many events.
            if len(self._fires_per_event) > 200:
                # Drop the 100 oldest entries (oldest-by-insertion-order in
                # Python 3.7+ dict).
                for k in list(self._fires_per_event.keys())[:100]:
                    del self._fires_per_event[k]
        return ("no", no_ask)


class AtmHoldV1Strategy(_Phase1Base):
    """Inverse of atm_fade — predicts the ATM bucket holds (resolves YES)
    when ya is in [0.40, 0.65] within 5min of close. Cohort evidence is
    tiny (n=10 pre-fix all 0% on the no-touch direction = 100% if flipped)
    but the post-MAX-fix observer restricts to mid-range ya so the bet is
    "MM is right that ATM is the modal outcome".
    """
    STRATEGY_NAME = "atm_hold_v1"
    OBSERVER_NAME = "atm_hold"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.ATM_HOLD_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.ATM_HOLD_SIZE_USDC

    def _select_side_and_price(self, sig, market):
        if sig["predicted_outcome"] != "yes":
            return None
        ya = _safe_float(market.get("yes_ask_dollars"))
        if ya is None or ya <= 0 or ya >= 1:
            return None
        return ("yes", ya)
