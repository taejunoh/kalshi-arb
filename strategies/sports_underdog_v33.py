"""Sports Underdog V3.3 — V3.1 + per-game aggregate-stake cap (paper-only)."""
import logging

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v33")


class SportsUnderdogV33Strategy(SportsUnderdogV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_underdog_v33_tickers.json"
    STRATEGY_NAME = "sports_underdog_v33"
    BADGE_LABEL = "V3.3"
    BADGE_COLOR = "#f59e0b"
    PER_GAME_CAP_USDC = 12.0  # scaled with base size 3→4 on 2026-04-20
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
