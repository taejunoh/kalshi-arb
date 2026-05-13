"""Sports Underdog V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (revised 2026-04-26):
  (MLB, pre):  observer forward replay 2026-04-25 showed us +40.5% (n=15) vs
    eu +17.5%. Post-fix observed n=7 +116.6% — strongly positive but is a
    single 4-min backlog burst (3 distinct games), not 7 independent shots.
    Treat n as effectively 4. Kept for accumulation; n>=20 before any
    promotion discussion.
  (NBA, pre):  matrix tied us +34% / eu +38% n=33. Post-fix n=10 +21.5%
    (playoff only). Kept; watch for playoff→regular-season regime shift.
  (NHL, pre):  matrix tied us +11% / eu +11% n=30. Post-fix n=6 -18.1%.
    Watch.
  (MLB, live): REMOVED 2026-04-26. Matrix said +38% n=43 ("hidden alpha");
    post-fix n=85 = -35.5%. Largest cell, decisive disproof of the matrix
    claim. Midshot v34 same cell -23.8% n=85 — consistent failure across
    bases. The "MLB live us alpha" was small-sample artifact, not real.
"""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v34")


class SportsUnderdogV34Strategy(SportsUnderdogV31Strategy):
    """Underdog V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v34_tickers.json"
    STRATEGY_NAME = "sports_underdog_v34"
    BADGE_LABEL = "V3.4"
    BADGE_COLOR = "#14b8a6"
    REGION_OVERRIDE = "us"
    # Restore base-4 sizing (v31 raised to 20 on 2026-04-25 for v31/v32 only;
    # v34/v35 stay at 4 until their own live track record justifies a raise).
    MIN_DEPTH_USDC = 5.0
    ALLOWED_CELLS = frozenset({
        ("MLB", "pre"),   # 2026-04-25: observer forward replay +40.5% n=15
        ("NBA", "pre"),
        ("NHL", "pre"),
    })
    # Explicit override of v31-inherited legacy gates (see longshot_v35 note).
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
