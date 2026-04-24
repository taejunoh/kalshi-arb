"""Sports Longshot V3.5 — V3.4's (NBA, pre) cell promoted to live trading.

Coexists with V3.4 paper on the same cell — V3.4 keeps recording paper for the
matrix evaluation, V3.5 places live + paper-twin entries on every same signal.

Live promotion rationale (2026-04-21 4D matrix, snapshot 2026-04-22):
  (NBA, pre) us: +20.5% ROI (n=23) — best paper-validated NBA longshot cell.
"""
import logging

from strategies.sports_longshot_v34 import SportsLongshotV34Strategy

logger = logging.getLogger("sports_longshot_v35")


class SportsLongshotV35Strategy(SportsLongshotV34Strategy):
    """Live-promoted subset of V3.4 longshot — only (NBA, pre) us."""

    TRADED_TICKERS_FILE = "data/sports_longshot_v35_tickers.json"
    STRATEGY_NAME = "sports_longshot_v35"
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
