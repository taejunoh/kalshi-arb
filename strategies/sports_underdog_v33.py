"""Sports Underdog V3.3 — V3.1 + per-game aggregate-stake cap (paper-only)."""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v33")


class SportsUnderdogV33Strategy(SportsUnderdogV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_underdog_v33_tickers.json"
    STRATEGY_NAME = "sports_underdog_v33"
    PER_GAME_CAP_USDC = 9.0
