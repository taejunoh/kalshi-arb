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
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:    n=6,  ROI +166%  ← lucky-shot band, keep
    #   10–15¢:  n=22, ROI  −35%  ← dead zone
    #   15–20¢:  n=29, ROI  −78%  ← dead zone
    #   ≥20¢:    n=178, ROI +1.5% ← neutral/positive
    # Originally used MIN_ENTRY_PRICE=0.20 which also blocked <10¢ wins.
    # Dead-zone gate preserves the <10¢ band while still filtering 10–20¢.
    ENTRY_PRICE_DEAD_ZONE = (0.10, 0.20)
