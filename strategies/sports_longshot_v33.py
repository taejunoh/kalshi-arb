"""Sports Longshot V3.3 — V3.1 + per-game aggregate-stake cap (paper-only).

Paper-only experiment: cap aggregate stake at $12 per game (3× base size
of $4), shared across all V3.3 siblings (longshot/midshot/underdog) via
OrderManager._v33_game_spend. Paper analysis 2026-04-17 found v31 fires
median 6 trades per game with 75% intra-game outcome correlation; the
cap simulation (originally $9 at $3 base) kept 55% of trade volume at
+23.1% ROI vs +19.7% uncapped. Scaled to $12 on 2026-04-20 to preserve
the 3-entry cap when base size rose 3→4.
"""
import logging

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v33")


class SportsLongshotV33Strategy(SportsLongshotV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_longshot_v33_tickers.json"
    STRATEGY_NAME = "sports_longshot_v33"
    PER_GAME_CAP_USDC = 12.0
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
