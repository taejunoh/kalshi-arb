"""Sports Longshot V3.4 — V3.1 logic against US retail consensus (paper-only).

Sport-aware regional A/B. 2026-04-21 matrix analysis found V3 (us-region)
outperformed V3.1 (eu-region) on 8 of 9 sport × base splits, with the
NBA midshot gap at +51pp (us +31% vs eu -20%) the most striking. V3.4
tests whether reviving the us-region under V3.1's full gate stack
(dead-zone, depth, pre-game-only, $4 base) replicates or even exceeds
V3 baseline performance.
"""
import logging

from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v34")


class SportsLongshotV34Strategy(SportsLongshotV31Strategy):
    """Longshot V3.1 with region=us (not eu). All other v31 gates inherited
    unchanged: dead-zone (0.10,0.20), depth 5.0, pre-game-only, $4 base."""

    TRADED_TICKERS_FILE = "data/sports_longshot_v34_tickers.json"
    STRATEGY_NAME = "sports_longshot_v34"
    REGION_OVERRIDE = "us"
