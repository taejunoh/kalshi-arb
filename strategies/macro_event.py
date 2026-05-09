"""Macro Event Trader (C1) — pre-event consensus deviation single-fire trader.

Targets KXCPI + KXFEDDECISION buckets. Compares Kalshi implied prob to
external consensus (CME FedWatch + Cleveland Fed Nowcast) cached via
core/macro_consensus.py and the cron in scripts/macro_consensus_cron.py.
Fires single edge-scaled directional trade when |deviation| >= MIN_DEVIATION
and EV after fee is positive, gated by 9 risk checks including
cross-strategy exposure cap with ladder_fed.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("macro_event")

TARGET_SERIES = frozenset({"KXCPI", "KXFEDDECISION"})

# event_key prefix maps from the Kalshi series. Used by
# core/macro_consensus.py.fetch_macro_consensus(event_key, bucket).
SERIES_TO_EVENT_PREFIX = {
    "KXCPI": "CPI",
    "KXFEDDECISION": "FOMC",
}


class MacroEventStrategy:
    STRATEGY_NAME = "macro_event"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        """KXFEDDECISION-26JUN-C25 -> 'FOMC-26JUN'.
        KXCPI-26JUN-T0.3 -> 'CPI-26JUN'."""
        parts = m.get("ticker", "").split("-")
        if len(parts) < 3:
            return ""
        series = parts[0]
        date_part = parts[1]
        prefix = SERIES_TO_EVENT_PREFIX.get(series)
        if not prefix:
            return ""
        return f"{prefix}-{date_part}"

    @staticmethod
    def _extract_bucket(m: dict) -> str:
        return m.get("ticker", "").rsplit("-", 1)[-1]

    def _inside_window(self, m: dict, now: Optional[datetime] = None) -> bool:
        """True if MIN_MINUTES <= time_to_close <= MAX_HOURS."""
        if now is None:
            now = datetime.now(timezone.utc)
        close_iso = m.get("close_time", "")
        if not close_iso:
            return False
        try:
            close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except Exception:
            return False
        delta = close_dt - now
        delta_min = delta.total_seconds() / 60.0
        delta_hr = delta.total_seconds() / 3600.0
        cfg = self.config
        return (delta_min >= cfg.MACRO_EVENT_WINDOW_MIN_MINUTES
                and delta_hr <= cfg.MACRO_EVENT_WINDOW_MAX_HOURS)

    async def scan_and_execute(self):
        """Stub — fully wired in Task 11."""
        return
