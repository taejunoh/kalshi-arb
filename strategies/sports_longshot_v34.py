"""Sports Longshot V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (revised 2026-04-26):
  (NBA, pre): us — kept for paper observation. Original matrix justification
    (V3 baseline +20.5% n=23) was regular-season paper; live v35 deployment
    is NBA playoffs, where the underdog 5-25c band has shifted negative
    (-58% n=18 across v34+v35 post-fix). Regime mismatch confirmed.
  (MLB, pre): REMOVED 2026-04-26 — never fired in 5 days because MLB
    regular-season pre-game lower-ask is structurally above 25c (no 80%+
    favorites in MLB regular schedule). Original matrix +148% n=3 was a
    single LAD-COL game evening, not a credible cell.
"""
import logging

from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v34")


class SportsLongshotV34Strategy(SportsLongshotV31Strategy):
    """Longshot V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_longshot_v34_tickers.json"
    STRATEGY_NAME = "sports_longshot_v34"
    REGION_OVERRIDE = "us"
    # Defensive override — v31 raised to base 12 / depth 15 on 2026-04-25;
    # v34/v35 stay at base 4 / depth 5 until their own paper alpha justifies.
    MIN_DEPTH_USDC = 5.0
    ALLOWED_CELLS = frozenset({("NBA", "pre")})

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
