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

    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        consensus: float,
    ) -> Optional[dict]:
        """Returns trade signal dict or None.

        Signal dict: {'side': 'yes'|'no', 'deviation': float (signed),
                      'entry_price': float, 'ev_per_contract': float}
        Caller still needs to apply window + dedup + depth + exposure gates.
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = consensus - kalshi_yes_mid  # positive ⇒ Kalshi underprices YES
        if abs(deviation) < cfg.MACRO_EVENT_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            # EV = consensus·(1 - entry - fee) + (1-consensus)·(-entry - fee)
            #    = consensus - entry - fee
            ev = consensus - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            # NO wins prob = 1 - consensus
            ev = (1.0 - consensus) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        """Edge-scaled fire size capped by per-ticker maximum."""
        cfg = self.config
        base = cfg.MACRO_EVENT_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.MACRO_EVENT_MIN_DEVIATION))
        return min(base * scale, cfg.MACRO_EVENT_MAX_POSITION_PER_TICKER)

    async def scan_and_execute(self):
        """Stub — fully wired in Task 11."""
        return
