"""Cross-Venue Polymarket Trader (B1) — Polymarket-anchored Kalshi single-side.

Reads Polymarket gamma-api quotes (cached via 1-min cron in
polymarket_quotes table). For each manual mapping entry, compares
Polymarket implied prob to Kalshi orderbook -> fires single-side
directional Kalshi trade when |deviation| >= MIN_DEVIATION and EV > 0.

Runs in main bot's _strategy_loop (60-sec cadence) — Polymarket
political markets are slow-moving, no separate fast-scan task needed.

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("xvenue_polymarket")


class XVenuePolymarketStrategy:
    STRATEGY_NAME = "xvenue_polymarket"

    def __init__(
        self, config, order_manager, market_scanner, kalshi_client,
        *, mapping,
    ):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._mapping = mapping
        self._recently_fired: dict[str, float] = {}

    def _kalshi_markets_for_prefix(self, prefix: str) -> list[dict]:
        """Return open Kalshi markets whose ticker startswith `prefix`."""
        return [
            m for m in self._scanner.get_all_markets()
            if m.get("ticker", "").startswith(prefix)
        ]

    @staticmethod
    def _apply_outcome_match(yes_prob: float, outcome_match: str) -> float:
        """For 'reversed' mapping, invert the prob to align Kalshi YES semantics."""
        if outcome_match == "reversed":
            return 1.0 - yes_prob
        return yes_prob

    def _is_quote_fresh(
        self, quote_row: dict, now: Optional[datetime] = None,
    ) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        fetched_at_iso = quote_row.get("fetched_at", "")
        if not fetched_at_iso:
            return False
        try:
            fetched_at = datetime.fromisoformat(
                fetched_at_iso.replace("Z", "+00:00")
            )
        except Exception:
            return False
        age_min = (now - fetched_at).total_seconds() / 60.0
        return age_min <= self.config.XVENUE_QUOTE_STALE_MINS

    def _is_quote_volume_ok(self, quote_row: dict) -> bool:
        vol = quote_row.get("volume_24h")
        if vol is None:
            return False
        return float(vol) >= self.config.XVENUE_MIN_POLYMARKET_VOLUME
