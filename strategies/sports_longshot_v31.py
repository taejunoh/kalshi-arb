"""Sports Longshot V3.1 — V3 longshot logic against EU sharp-book consensus."""
import logging
from typing import Optional

from strategies.sports_longshot import SportsLongshotStrategy

logger = logging.getLogger("sports_longshot_v31")


class SportsLongshotV31Strategy(SportsLongshotStrategy):
    """Same longshot logic (5-25c, 3x+ payoff) but `regions=eu` so the
    consensus includes Pinnacle + Matchbook + EU sharp books instead of
    US retail. A/B vs V3 isolates the effect of the bookmaker pool.
    """

    TRADED_TICKERS_FILE = "data/sports_longshot_v31_tickers.json"
    STRATEGY_NAME = "sports_longshot_v31"
    BADGE_LABEL = "V3.1"
    BADGE_COLOR = "#ec4899"
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
    # NBA playoff suspend (2026-04-30). NBA pre eu longshot -90% n=25 in
    # playoff (game-level dedup). Same regime mismatch pattern as midshot
    # and underdog families. Cascades to v32, v33, v34 (retired), v35
    # (already paper-demoted) via inheritance. Auto-resumes when NBA
    # returns to regular season ~2026-10. No v36 longshot variant — at
    # 35¢+ floor longshot's price band (5-25¢) collapses to nothing.
    # 2026-05-03 NHL added — n=27 LIVE -27% on NHL playoff. Longshot rarely
    # fires there (playoff prices stay 30-71c, longshot's 5-25c band almost
    # empty) but every fire so far lost. Auto-resumes regular season ~Oct.
    PLAYOFF_BLOCKLIST = frozenset({"NBA", "NHL"})

    # 2026-05-13 (WNBA expansion): override parent OBSERVATION_ONLY_SPORTS
    # to remove WNBA. v31 trio is multi-sport by design — WNBA fits the
    # same longshot 5-25c band logic. NBA/MLB/NHL behavior unchanged.
    OBSERVATION_ONLY_SPORTS = (
        SportsLongshotStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 4→12 on 2026-04-25. LIVE −53% (n=140) was the biggest
        # selection-bias victim; SOLO_PAPER post-promo +71% (n=17) suggests
        # the aggression fix should recover most of the gap. Smaller raise
        # than midshot/underdog because the LIVE gap is largest, so fix
        # uncertainty is highest. v34/v35 longshot use their own overrides.
        return min(12.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """WNBA-only pair-fire dedup at entry (matches v40 d036f07 pattern).
        Non-WNBA tickers fall through to parent logic unchanged."""
        if "WNBAGAME" in ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob,
            line_movement=line_movement, home_adj=home_adj,
            ticker=ticker,
        )
