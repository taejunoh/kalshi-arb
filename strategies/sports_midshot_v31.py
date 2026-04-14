"""Sports Midshot V3.1 — V3 midshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_midshot_v31")


class SportsMidshotV31Strategy(SportsMidshotStrategy):
    """Same midshot logic (5-35c, 2.5x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v31_tickers.json"
    STRATEGY_NAME = "sports_midshot_v31"
    REGION_OVERRIDE = "eu"
