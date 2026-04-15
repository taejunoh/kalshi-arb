"""Sports Longshot V3.1 — V3 longshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_longshot import SportsLongshotStrategy

logger = logging.getLogger("sports_longshot_v31")


class SportsLongshotV31Strategy(SportsLongshotStrategy):
    """Same longshot logic (5-25c, 3x+ payoff) but `regions=eu` so the
    consensus includes Pinnacle + Matchbook + EU sharp books instead of
    US retail. A/B vs V3 isolates the effect of the bookmaker pool.
    """

    TRADED_TICKERS_FILE = "data/sports_longshot_v31_tickers.json"
    STRATEGY_NAME = "sports_longshot_v31"
    REGION_OVERRIDE = "eu"
    MIN_DEPTH_USDC = 5.0
