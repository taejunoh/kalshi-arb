"""Sports Underdog V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (from 2026-04-21 4D matrix):
  (MLB, live): us +38% vs eu +3%   (n=43 vs 136) 🔥 HIDDEN ALPHA — the big one
  (NBA, pre):  us +34% vs eu +38%  (n=33 each) — essentially tied, capture both
  (NHL, pre):  us +11% vs eu +11%  (n=30 vs 27) — tied, capture both
MLB underdog pre: eu +14% beats us +0.4% → leave to V3.1.
"""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v34")


class SportsUnderdogV34Strategy(SportsUnderdogV31Strategy):
    """Underdog V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v34_tickers.json"
    STRATEGY_NAME = "sports_underdog_v34"
    REGION_OVERRIDE = "us"
    # Restore base-4 sizing (v31 raised to 20 on 2026-04-25 for v31/v32 only;
    # v34/v35 stay at 4 until their own live track record justifies a raise).
    MIN_DEPTH_USDC = 5.0
    ALLOWED_CELLS = frozenset({
        ("MLB", "live"),  # hidden alpha cell (+38% us vs +3% eu)
        ("NBA", "pre"),
        ("NHL", "pre"),
    })

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
