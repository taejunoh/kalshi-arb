"""Sports Midshot V3.4 — V3.1 logic against US retail consensus (paper-only)."""
import logging

from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v34")


class SportsMidshotV34Strategy(SportsMidshotV31Strategy):
    """Midshot V3.1 with region=us. NBA midshot pre-game us +46% vs eu +13%
    (n=36 vs 28) was the headline mispricing that motivated the v34 fork.
    All other v31 gates inherited unchanged, incl. NHL blocklist."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v34_tickers.json"
    STRATEGY_NAME = "sports_midshot_v34"
    REGION_OVERRIDE = "us"
