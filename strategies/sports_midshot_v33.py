"""Sports Midshot V3.3 — V3.1 + per-game aggregate-stake cap (paper-only)."""
import logging

from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v33")


class SportsMidshotV33Strategy(SportsMidshotV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_midshot_v33_tickers.json"
    STRATEGY_NAME = "sports_midshot_v33"
    PER_GAME_CAP_USDC = 12.0  # scaled with base size 3→4 on 2026-04-20
