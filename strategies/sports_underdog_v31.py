"""Sports Underdog V3.1 — V3 underdog logic against EU sharp-book consensus."""
import logging

from strategies.sports_underdog import SportsUnderdogStrategy

logger = logging.getLogger("sports_underdog_v31")


class SportsUnderdogV31Strategy(SportsUnderdogStrategy):
    """Same underdog logic (fade consensus favorite > 60%, 2x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v31_tickers.json"
    STRATEGY_NAME = "sports_underdog_v31"
    REGION_OVERRIDE = "eu"
    MIN_DEPTH_USDC = 5.0
