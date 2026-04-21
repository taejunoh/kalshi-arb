"""Sports Midshot V3.1 — V3 midshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_midshot_v31")


class SportsMidshotV31Strategy(SportsMidshotStrategy):
    """Same midshot logic (5-35c, 2.5x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v31_tickers.json"
    STRATEGY_NAME = "sports_midshot_v31"
    REGION_OVERRIDE = "eu"
    MIN_DEPTH_USDC = 5.0
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:    n=9,  0% win, ROI −100%
    #   10–15¢:  n=9,  0% win, ROI −100%
    #   15–20¢:  n=18, ROI −11%
    #   ≥20¢:    ROI positive (+27% at 20-25¢, +27% at 30-35¢)
    # 20¢ simple floor — sub-20¢ loses systematically, no sub-band exception.
    MIN_ENTRY_PRICE = 0.20
    # Pre-game-only (2026-04-21). Phase split shows live-game v31 entries
    # systematically lose (-6% ROI live, paper+live n=252) while pre-game
    # wins (+25% ROI, n=57). Same pattern confirmed on longshot (2026-04-20)
    # and underdog; extending across v31 trio.
    PRE_GAME_ONLY = True
