"""Sports Underdog V3.5 — V3.4's (NBA, pre) cell promoted to live trading.

Coexists with V3.4 paper on the same cell — V3.4 keeps recording paper for the
matrix evaluation, V3.5 places live + paper-twin entries on every same signal.

Live promotion rationale (2026-04-21 4D matrix, snapshot 2026-04-22):
  (NBA, pre) us: +34% ROI vs eu +38% (n=33 each) — essentially tied;
  promoting the us side here, eu side stays in V3.1 live.
"""
import logging

from strategies.sports_underdog_v34 import SportsUnderdogV34Strategy

logger = logging.getLogger("sports_underdog_v35")


class SportsUnderdogV35Strategy(SportsUnderdogV34Strategy):
    """Live-promoted subset of V3.4 underdog — only (NBA, pre) us, 35¢+ floor."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v35_tickers.json"
    STRATEGY_NAME = "sports_underdog_v35"
    # Depth gate scaled to base 8: peak stake ~$3.40 → 3× buffer = $10.
    MIN_DEPTH_USDC = 10.0
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
    # Explicit override of v31-inherited legacy gates (see longshot_v35 note).
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    # 35¢ floor (2026-04-30). Game-level analysis showed underdog_v35 NBA pre
    # us bleeds in <35¢ buckets (n=8 games at -180% to -430%) but retains
    # alpha at 35¢+ (3 games -14%, 2 games +331%). Same finding that drove
    # sports_underdog_v36_playoff (eu region, +135% observer at 35¢ floor).
    # Effectively the us-region twin of v36; v35 still gates by ALLOWED_CELLS
    # and runs without PLAYOFF_ONLY (would also fire NBA regular season when
    # that returns, with the alpha presumed regime-independent at this band).
    MIN_ENTRY_PRICE = 0.35

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 4→8 on 2026-04-25. Live n=6 with +48% ROI; modest raise.
        return min(8.0, self._order_manager.bankroll * 0.02)
