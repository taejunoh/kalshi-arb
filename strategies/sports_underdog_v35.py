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
    """Live-promoted subset of V3.4 underdog — only (NBA, pre) us, regular-season-only."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v35_tickers.json"
    STRATEGY_NAME = "sports_underdog_v35"
    BADGE_LABEL = "V3.5"
    BADGE_COLOR = "#818cf8"
    # Depth gate scaled to base 8: peak stake ~$3.40 → 3× buffer = $10.
    MIN_DEPTH_USDC = 10.0
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
    # Explicit override of v31-inherited legacy gates (see longshot_v35 note).
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    # Floor reverted to inherited 0.20 (2026-04-30): v35 now NBA-regular-only
    # via PLAYOFF_BLOCKLIST cascade from v31. The 35¢+ alpha is a playoff
    # regime finding; sports_playoff_underdog_v1_us captures it specifically.
    # V3 baseline regular-season NBA pre us +236% n=12 used the full price
    # band, so 0.20 default is the right floor when NBA returns to regular.

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 4→8 on 2026-04-25. Live n=6 with +48% ROI; modest raise.
        return min(8.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)
