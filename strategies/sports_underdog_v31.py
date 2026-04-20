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
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:   n=8,  0% win, ROI −100%
    #   10–15¢: n=8,  0% win, ROI −100%
    #   15–20¢: n=11, 0% win, ROI −100%
    #   ≥20¢:   ROI positive (+95% at 20-25¢, +46% at 30-35¢)
    # Largest sub-20¢ drain of any v31 variant (−$80). 20¢ simple floor.
    MIN_ENTRY_PRICE = 0.20
