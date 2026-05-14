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
