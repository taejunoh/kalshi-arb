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
    # Depth gate scaled to base 12: peak stake ~$5.10 → 3× buffer = $15.
    MIN_DEPTH_USDC = 15.0
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:    n=6,  ROI +166%  ← lucky-shot band, keep
    #   10–15¢:  n=22, ROI  −35%  ← dead zone
    #   15–20¢:  n=29, ROI  −78%  ← dead zone
    #   ≥20¢:    n=178, ROI +1.5% ← neutral/positive
    # Originally used MIN_ENTRY_PRICE=0.20 which also blocked <10¢ wins.
    # Dead-zone gate preserves the <10¢ band while still filtering 10–20¢.
    ENTRY_PRICE_DEAD_ZONE = (0.10, 0.20)
    # Pre-game-only gate (2026-04-20). Phase split (paper+live combined):
    #   pre-game:  n=17, ROI +81% (live n=7 +134%, paper n=10 +54%)
    #   live-game: n=226, ROI -18% (live n=101 -30%, paper n=125 -9%)
    # Live-game entries chase Kalshi moves with stale consensus → systematic
    # loss. Mid/underdog v31 show same direction but smaller magnitude — gate
    # restricted to longshot for now (1-week eval before extending).
    PRE_GAME_ONLY = True
    # NHL longshot blocked 2026-04-23 — matrix snapshot 2026-04-22 flagged
    # NHL long pre eu as the active losing cell for v31. With PRE_GAME_ONLY=True
    # the only NHL signals v31 fires on are (NHL, pre eu); blocking the sport
    # fully stops both live trades and the paper twin on NHL.
    SPORT_BLOCKLIST = frozenset({"NHL"})

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 4→12 on 2026-04-25. LIVE −53% (n=140) was the biggest
        # selection-bias victim; SOLO_PAPER post-promo +71% (n=17) suggests
        # the aggression fix should recover most of the gap. Smaller raise
        # than midshot/underdog because the LIVE gap is largest, so fix
        # uncertainty is highest. v34/v35 longshot use their own overrides.
        return min(12.0, self._order_manager.bankroll * 0.02)
