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
    # Paper-settled segmentation by entry price (2026-04-17, n=78 total):
    #   0.10–0.15: n=12, ROI −40.4%    ← dead zone
    #   0.15–0.20: n=17, ROI −63.5%    ← dead zone
    #   0.20–0.25: n=33, ROI +24.9%    ← good
    #   ≥ 0.25:    n=12, ROI +24.0%    ← good
    # The ≤ 20¢ dead zone loses under idealized paper fills — it is a
    # signal-quality problem, not a fee drag. Live is even worse
    # (15-20¢ live ROI −100% over n=10). Gate entries at a 20¢ floor.
    MIN_ENTRY_PRICE = 0.20
