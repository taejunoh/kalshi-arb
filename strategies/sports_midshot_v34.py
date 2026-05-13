"""Sports Midshot V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (revised 2026-04-26):
  (NBA, pre):  matrix +46% vs eu +13%. Post-fix observed n=10 +26.8% (playoff
    only). Kept; watch for regime shift after NBA playoffs end (~2026-06).
  (MLB, pre):  matrix +65% n=11. Post-fix observed n=4 +41% — too small but
    consistent. Kept; redundant with v31_eu MLB pre live (which is also +41%).
  (MLB, live): REMOVED 2026-04-26. Matrix +20% n=68 did not reproduce —
    post-fix n=85 = -23.8%. Underdog v34 (MLB,live) showed same failure
    (-35.5% n=85). The "MLB live us alpha" matrix finding was small-sample
    luck; large post-fix sample disproves it.
NHL midshot fails in both regions — excluded entirely.
"""
import logging

from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v34")


class SportsMidshotV34Strategy(SportsMidshotV31Strategy):
    """Midshot V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v34_tickers.json"
    STRATEGY_NAME = "sports_midshot_v34"
    BADGE_LABEL = "V3.4"
    BADGE_COLOR = "#14b8a6"
    REGION_OVERRIDE = "us"
    # Defensive override — v31 raised to base 16 / depth 20 on 2026-04-25;
    # v34/v35 stay at base 4 / depth 5 until their own paper alpha justifies.
    MIN_DEPTH_USDC = 5.0
    ALLOWED_CELLS = frozenset({
        ("NBA", "pre"), ("MLB", "pre"),
    })
    # Explicit override of v31-inherited legacy gates. ALLOWED_CELLS
    # supersedes both, but inheritance leaves them set on the class —
    # making them explicit prevents silent reactivation if someone
    # ever clears ALLOWED_CELLS without intending to revert to v31 logic.
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
