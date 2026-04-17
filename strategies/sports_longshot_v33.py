"""Sports Longshot V3.3 — V3.1 + per-game aggregate-stake cap (paper-only).

Paper-only experiment: cap aggregate stake at $9 per game, shared across
all V3.3 siblings (longshot/midshot/underdog) via OrderManager._v33_game_
spend. Paper analysis 2026-04-17 found v31 fires median 6 trades per game
with 75% intra-game outcome correlation; the $9 cap simulation kept 55%
of trade volume at +23.1% ROI vs +19.7% uncapped (capital-efficiency win).
"""
import logging

from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v33")


class SportsLongshotV33Strategy(SportsLongshotV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_longshot_v33_tickers.json"
    STRATEGY_NAME = "sports_longshot_v33"
    PER_GAME_CAP_USDC = 9.0
