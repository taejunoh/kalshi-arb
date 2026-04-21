"""Sports Longshot V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (from 2026-04-21 4D matrix, see docs/analysis/strategy-matrix-
2026-04-21.md):
  (MLB, pre): us +148% (n=3, tiny but positive; eu +67%)
All other cells are either eu-preferred, negative in both regions, or have
insufficient data. V3.1 (eu) covers its complementary set.
"""
import logging

from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v34")


class SportsLongshotV34Strategy(SportsLongshotV31Strategy):
    """Longshot V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_longshot_v34_tickers.json"
    STRATEGY_NAME = "sports_longshot_v34"
    REGION_OVERRIDE = "us"
    ALLOWED_CELLS = frozenset({("MLB", "pre")})
