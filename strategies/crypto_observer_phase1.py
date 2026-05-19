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

    def _size_usdc_for(self, side: str, price: float, market: dict
                       ) -> Optional[float]:
        """Hook for depth-aware sizing. Default is the strategy's fixed
        SIZE_USDC. Subclasses may clamp by top-of-book contract depth on
        the side being bought; return None to skip the fire entirely
        (e.g., book too thin to fill a meaningful position)."""
        return self.SIZE_USDC

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
                age_s = (datetime.now(timezone.utc) - ts).total_seconds()
                logger.debug(
                    f"[{self.STRATEGY_NAME}] SKIP sig={sig_id} ticker={sig.get('market_id')} "
                    f"reason=signal_aged_out age_s={age_s:.1f} freshness_window_s={self.SIGNAL_FRESHNESS_S}"
                )
                self._processed.add(sig_id)
                continue
            ticker = sig["market_id"]
            market = self._scanner.get_market(ticker)
            if market is None:
                # Market dropped from scanner cache between signal and now.
                logger.debug(
                    f"[{self.STRATEGY_NAME}] SKIP sig={sig_id} ticker={ticker} "
                    f"reason=no_market_in_scanner"
                )
                self._processed.add(sig_id)
                continue
            choice = self._select_side_and_price(sig, market)
            if choice is None:
                logger.debug(
                    f"[{self.STRATEGY_NAME}] SKIP sig={sig_id} ticker={ticker} "
                    f"reason=select_side_returned_none "
                    f"ya={market.get('yes_ask_dollars')} "
                    f"no_ask={market.get('no_ask_dollars')} "
                    f"pred={sig.get('predicted_outcome')}"
                )
                self._processed.add(sig_id)
                continue
            side, price = choice
            size_usdc = self._size_usdc_for(side, price, market)
            if size_usdc is None or size_usdc <= 0:
                # Complementarity: no_ask depth = yes_bid qty; yes_ask depth = yes_ask qty.
                depth_key = "yes_bid_size_fp" if side == "no" else "yes_ask_size_fp"
                logger.debug(
                    f"[{self.STRATEGY_NAME}] SKIP sig={sig_id} ticker={ticker} "
                    f"reason=size_returned_none "
                    f"side={side} price={price:.3f} "
                    f"depth_{depth_key}={market.get(depth_key)}"
                )
                self._processed.add(sig_id)
                continue
            try:
                await self._order_manager.market_order(
                    ticker=ticker, side=side, size_usdc=size_usdc,
                    price=price, strategy=self.STRATEGY_NAME, market_id=ticker,
                )
                logger.info(
                    f"[{self.STRATEGY_NAME}] FIRE {ticker} {side} "
                    f"size=${size_usdc:.2f} price={price:.3f} "
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
        self._min_ya = config.DERIBIT_TAIL_FADE_MIN_YA
        self._min_fire_contracts = config.DERIBIT_TAIL_FADE_MIN_FIRE_CONTRACTS

    def _select_side_and_price(self, sig, market):
        ya = _safe_float(market.get("yes_ask_dollars"))
        no_ask = _safe_float(market.get("no_ask_dollars"))
        if ya is None or no_ask is None:
            return None
        if ya <= 0 or no_ask <= 0 or no_ask >= 1:
            return None
        # ya band gate — outside [MIN, MAX], the payout math inverts (high
        # end) or signal mis-calibrates (low end). See config comment.
        if ya < self._min_ya or ya > self._max_ya:
            return None
        # Observer predicts "no" — we BUY NO to express that.
        if sig["predicted_outcome"] != "no":
            return None
        return ("no", no_ask)

    def _size_usdc_for(self, side, price, market):
        # 2026-05-18 FOK reject (KXETH-26MAY1816-B2100 at no_ask=0.95 needed
        # 21 contracts of resting volume, book had <21). Clamp our order to
        # what's actually fillable at top-of-book, skip if depth is below
        # MIN_FIRE_CONTRACTS (avoids dust trades that aren't worth the slot
        # in trade history / dedup state).
        # Kalshi API only returns yes_*_size_fp fields; no_ask_size is the
        # complementary qty (yes_bid at price (1-no_ask)). See kalshi_client.py
        # get_top_of_book_sizes. 2026-05-19: bug fix — was reading non-existent
        # no_ask_size_fp which always returned None → strategy silent-skipped
        # every in-band signal.
        depth = _safe_float(market.get("yes_bid_size_fp"))
        if depth is None or depth <= 0:
            return None
        if price <= 0:
            return None
        target_count = self.SIZE_USDC / price
        if depth >= target_count:
            return self.SIZE_USDC
        if depth < self._min_fire_contracts:
            return None
        return depth * price


class BidImbalanceV1Strategy(_Phase1Base):
    """Follows top-of-book bid/ask size imbalance. Buys the predicted side
    at current ask.

    Cohort evidence revised 2026-05-15: full Phase 0 cohort (n=266
    settled, vs 26 used at 5/14 promotion) shows original direction
    WR=36.1% overall — losing. Paper trader hit n=83 / WR 30% / ROI
    -37.6%. Band breakdown reveals the rich end (ya≥0.50) is where
    the original direction still wins: $0.50+ paper had 12/14 = 85.7% WR
    on the ≥$0.70 sub-band. MIN_PRICE filter keeps only those fires;
    ya<0.60 zone is handled by the flipped sibling
    `BidImbalanceFadeV1Strategy`.
    """
    STRATEGY_NAME = "bid_imbalance_v1"
    OBSERVER_NAME = "bid_imbalance"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.BID_IMBALANCE_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.BID_IMBALANCE_SIZE_USDC
        self._min_price = config.BID_IMBALANCE_MIN_PRICE

    def _select_side_and_price(self, sig, market):
        pred = sig["predicted_outcome"]
        if pred not in ("yes", "no"):
            return None
        ask_key = "yes_ask_dollars" if pred == "yes" else "no_ask_dollars"
        price = _safe_float(market.get(ask_key))
        if price is None or price <= 0 or price >= 1:
            return None
        if price < self._min_price:
            return None
        return (pred, price)


class OpenNoTouchV1Strategy(_Phase1Base):
    """Inverse of open_widening — BUY NO on ATM±1% buckets at event open.

    2026-05-15 re-analysis (Phase 0 n=928 settled, no_ask=1-yb + Kalshi
    fee): the original "+30% ROI" promotion used a symmetric payoff
    model that overstated EV. Corrected band breakdown:

        ya<0.10   n=190  WR 93%  ROI  -4.6%
        0.10-0.20 n=438  WR 88%  ROI  -4.5%
        0.20-0.30 n=109  WR 86%  ROI  -3.2%
        0.30-0.45 n= 38  WR 87%  ROI +10.1%   ← only positive slice
        0.45-0.55 n= 29  WR 72%  ROI  -8.6%
        ≥0.55      n=124  WR 45%  ROI -42%

    Narrowed to band D + per-event cap=1 with center-of-window strike
    selector. ya is derived from no_ask (ya = 1 - no_ask). yes_ask is
    NOT used: crypto spreads make yes_ask + no_ask < 1, so reading
    yes_ask_dollars lets tail strikes (no_ask > 0.80) slip past a
    "ya>=0.30" gate.
    """
    STRATEGY_NAME = "open_no_touch_v1"
    OBSERVER_NAME = "open_no_touch"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.OPEN_NO_TOUCH_SIZE_USDC
        self._min_ya = config.OPEN_NO_TOUCH_MIN_YA
        self._max_ya = config.OPEN_NO_TOUCH_MAX_YA
        self._target_ya = (self._min_ya + self._max_ya) / 2.0
        self._max_fires_per_event = config.OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT
        # event_key -> fire count (in-memory, resets on restart).
        self._fires_per_event: dict[str, int] = {}

    def _event_key(self, sig) -> str:
        ek = sig.get("event_key") or ""
        if ek:
            return ek
        parts = sig.get("market_id", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 3 else ""

    def _select_side_and_price(self, sig, market):
        # Base-class abstract method. This subclass overrides scan_and_execute
        # to pick the band-center strike per event, so this path is unused.
        return None

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
        # Per scan: collect eligible signals, then pick the one closest to
        # the band center for each event. Cap enforced via _fires_per_event.
        best_per_event: dict[str, tuple[float, dict, float]] = {}

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
                self._processed.add(sig_id)
                continue
            if sig.get("predicted_outcome") != "no":
                self._processed.add(sig_id)
                continue
            ticker = sig.get("market_id", "")
            market = self._scanner.get_market(ticker)
            if market is None:
                self._processed.add(sig_id)
                continue
            no_ask = _safe_float(market.get("no_ask_dollars"))
            if no_ask is None or no_ask <= 0 or no_ask >= 1:
                self._processed.add(sig_id)
                continue
            ya = 1.0 - no_ask
            if ya < self._min_ya or ya >= self._max_ya:
                self._processed.add(sig_id)
                continue
            ek = self._event_key(sig)
            if ek and self._fires_per_event.get(ek, 0) >= self._max_fires_per_event:
                self._processed.add(sig_id)
                continue
            score = abs(ya - self._target_ya)
            current = best_per_event.get(ek)
            if current is None or score < current[0]:
                best_per_event[ek] = (score, sig, no_ask)

        for ek, (_, sig, no_ask) in best_per_event.items():
            ticker = sig["market_id"]
            try:
                await self._order_manager.market_order(
                    ticker=ticker, side="no", size_usdc=self.SIZE_USDC,
                    price=no_ask, strategy=self.STRATEGY_NAME, market_id=ticker,
                )
                logger.info(
                    f"[{self.STRATEGY_NAME}] FIRE {ticker} no "
                    f"size=${self.SIZE_USDC:.0f} price={no_ask:.3f} "
                    f"ya={1-no_ask:.3f} signal_id={sig['id']} event={ek}"
                )
                if ek:
                    self._fires_per_event[ek] = self._fires_per_event.get(ek, 0) + 1
            except Exception as e:
                logger.error(f"{self.STRATEGY_NAME}: market_order failed: {e}")
            self._processed.add(sig["id"])

        if len(self._processed) > 500:
            self._processed = set(list(self._processed)[-300:])
        if len(self._fires_per_event) > 200:
            for k in list(self._fires_per_event.keys())[:100]:
                del self._fires_per_event[k]


class BidImbalanceFadeV1Strategy(_Phase1Base):
    """Conditional-flip of bid_imbalance for the ya<0.60 fade zone.

    Sources signals from the `bid_imbalance_fade` observer, which delegates
    to BidImbalanceObserver's trigger, restricts to ya < 0.60, and flips
    the predicted side. Trader buys the (already flipped) predicted side
    at current ask.

    Phase 0 cohort (2026-05-15, n=266 settled bid_imbalance signals)
    showed original WR 36.1% overall but full-flip WR 63.9%. Sub-cohort
    with ya<0.60 (n=141, excluding the na-feature legacy rows) flips at
    83% WR — the alpha is concentrated in the cheap end, where bid
    stacking on cheap YES is a retail-longshot contrarian indicator.
    The rich end (ya≥0.60) is left to BidImbalanceV1Strategy with its
    MIN_PRICE gate.
    """
    STRATEGY_NAME = "bid_imbalance_fade_v1"
    OBSERVER_NAME = "bid_imbalance_fade"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.BID_IMBALANCE_FADE_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.BID_IMBALANCE_FADE_SIZE_USDC

    def _select_side_and_price(self, sig, market):
        pred = sig["predicted_outcome"]
        if pred not in ("yes", "no"):
            return None
        ask_key = "yes_ask_dollars" if pred == "yes" else "no_ask_dollars"
        price = _safe_float(market.get(ask_key))
        if price is None or price <= 0 or price >= 1:
            return None
        return (pred, price)


class LateCloseTailV1Strategy(_Phase1Base):
    """Paper trader for the late_close_tail observer.

    Buys NO at no_ask on mid-OTM strikes (ya 0.05-0.30) within 10 min of
    close. Same execution shape as DeribitTailFadeV1Strategy (depth-aware
    sizing, MIN_FIRE_CONTRACTS floor) to avoid FOK rejects on thin books.
    Paper-only at launch — Phase 2 live promotion gated on n ≥ 100 settled
    + ROI ≥ +5%.
    """
    STRATEGY_NAME = "late_close_tail_v1"
    OBSERVER_NAME = "late_close_tail"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.LATE_CLOSE_TAIL_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.LATE_CLOSE_TAIL_SIZE_USDC
        self._min_ya = config.LATE_CLOSE_TAIL_MIN_YA
        self._max_ya = config.LATE_CLOSE_TAIL_MAX_YA
        self._min_fire_contracts = config.LATE_CLOSE_TAIL_MIN_FIRE_CONTRACTS

    def _select_side_and_price(self, sig, market):
        if sig["predicted_outcome"] != "no":
            return None
        ya = _safe_float(market.get("yes_ask_dollars"))
        no_ask = _safe_float(market.get("no_ask_dollars"))
        if ya is None or no_ask is None:
            return None
        if no_ask <= 0 or no_ask >= 1:
            return None
        if ya < self._min_ya or ya > self._max_ya:
            return None
        return ("no", no_ask)

    def _size_usdc_for(self, side, price, market):
        # Kalshi API only returns yes_*_size_fp fields; no_ask_size is the
        # complementary qty (yes_bid at price (1-no_ask)). See kalshi_client.py
        # get_top_of_book_sizes. 2026-05-19: bug fix — was reading non-existent
        # no_ask_size_fp which always returned None → strategy silent-skipped
        # every in-band signal.
        depth = _safe_float(market.get("yes_bid_size_fp"))
        if depth is None or depth <= 0 or price <= 0:
            return None
        target_count = self.SIZE_USDC / price
        if depth >= target_count:
            return self.SIZE_USDC
        if depth < self._min_fire_contracts:
            return None
        return depth * price


class DeribitAtmFadeV1Strategy(_Phase1Base):
    """Paper trader for the deribit_atm_skew observer.

    Fades the ATM bucket when Kalshi yes_ask exceeds the Deribit-IV
    lognormal probability by ≥ 1.5×. Buys NO at no_ask. Mirrors the
    DeribitTailFadeV1Strategy execution shape (depth-aware sizing) since
    ATM books are not guaranteed thicker than tails near close.
    """
    STRATEGY_NAME = "deribit_atm_fade_v1"
    OBSERVER_NAME = "deribit_atm_skew"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        super().__init__(config, order_manager, market_scanner, kalshi_client)
        self.SIGNAL_FRESHNESS_S = config.DERIBIT_ATM_FADE_SIGNAL_FRESHNESS_S
        self.SIZE_USDC = config.DERIBIT_ATM_FADE_SIZE_USDC
        self._min_ya = config.DERIBIT_ATM_FADE_MIN_YA
        self._max_ya = config.DERIBIT_ATM_FADE_MAX_YA
        self._min_fire_contracts = config.DERIBIT_ATM_FADE_MIN_FIRE_CONTRACTS

    def _select_side_and_price(self, sig, market):
        if sig["predicted_outcome"] != "no":
            return None
        ya = _safe_float(market.get("yes_ask_dollars"))
        no_ask = _safe_float(market.get("no_ask_dollars"))
        if ya is None or no_ask is None:
            return None
        if no_ask <= 0 or no_ask >= 1:
            return None
        if ya < self._min_ya or ya > self._max_ya:
            return None
        return ("no", no_ask)

    def _size_usdc_for(self, side, price, market):
        # Kalshi API only returns yes_*_size_fp fields; no_ask_size is the
        # complementary qty (yes_bid at price (1-no_ask)). See kalshi_client.py
        # get_top_of_book_sizes. 2026-05-19: bug fix — was reading non-existent
        # no_ask_size_fp which always returned None → strategy silent-skipped
        # every in-band signal.
        depth = _safe_float(market.get("yes_bid_size_fp"))
        if depth is None or depth <= 0 or price <= 0:
            return None
        target_count = self.SIZE_USDC / price
        if depth >= target_count:
            return self.SIZE_USDC
        if depth < self._min_fire_contracts:
            return None
        return depth * price


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
