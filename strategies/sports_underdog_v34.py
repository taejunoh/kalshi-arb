"""Sports Underdog V3.4 — V3.1 logic against US retail consensus (paper-only)."""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v34")


class SportsUnderdogV34Strategy(SportsUnderdogV31Strategy):
    """Underdog V3.1 with region=us. MLB underdog live us +38% (n=43) vs
    eu +3% (n=136) was a 34pp hidden-alpha gap discovered in the 2026-04-21
    region × phase × sport matrix. V3.1 PRE_GAME_ONLY inherited — this v34
    currently won't capture MLB underdog live since it's gated pre-game.
    Kept consistent for now; if pre-game restriction proves net-negative,
    consider a `_live_us` fork targeted at MLB underdog live specifically."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v34_tickers.json"
    STRATEGY_NAME = "sports_underdog_v34"
    REGION_OVERRIDE = "us"
