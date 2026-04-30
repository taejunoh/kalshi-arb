"""Sports Underdog V3.1 — V3 underdog logic against EU sharp-book consensus."""
import logging

from strategies.sports_underdog import SportsUnderdogStrategy

logger = logging.getLogger("sports_underdog_v31")


class SportsUnderdogV31Strategy(SportsUnderdogStrategy):
    """Same underdog logic (fade consensus favorite > 60%, 2x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v31_tickers.json"
    STRATEGY_NAME = "sports_underdog_v31"
    REGION_OVERRIDE = "eu"
    # Depth gate scaled to base 20: peak stake ≈ $8.50 (price=0.40 × v32 mult 1.065)
    # → 3× buffer = $25. Same buffer ratio that the previous $5 gate had at base 4.
    MIN_DEPTH_USDC = 25.0
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:   n=8,  0% win, ROI −100%
    #   10–15¢: n=8,  0% win, ROI −100%
    #   15–20¢: n=11, 0% win, ROI −100%
    #   ≥20¢:   ROI positive (+95% at 20-25¢, +46% at 30-35¢)
    # Largest sub-20¢ drain of any v31 variant (−$80). 20¢ simple floor.
    MIN_ENTRY_PRICE = 0.20
    # Pre-game-only (2026-04-21). Phase split shows live-game v31 entries
    # systematically lose (-13% ROI live, paper+live n=176) while pre-game
    # wins (+20% ROI, n=116 — strongest sample among v31 trio).
    PRE_GAME_ONLY = True
    # NBA playoff suspend (2026-04-30). Game-level analysis: NBA pre eu
    # underdog -36% n=32 in playoff regime; V3 baseline pre-playoff was
    # +236% n=12 (regime mismatch confirmed). The 35¢+ alpha sub-cell of
    # NBA playoff is captured by sports_underdog_v36_playoff (eu) and
    # sports_underdog_v36_us (us), both override this to empty.
    # Cascades to v33, v34, v35 — all show same NBA playoff bleed pattern.
    # v32 already has its own explicit PLAYOFF_BLOCKLIST. Auto-resumes when
    # NBA returns to regular season (~2026-10).
    PLAYOFF_BLOCKLIST = frozenset({"NBA"})

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 4→20 on 2026-04-25. Underdog v31 LIVE near break-even (n=153,
        # ROI -2.5%) and v32 paper +143% over 7d window justify capital scale-up
        # on this base only. Other underdog variants (v34/v35) still on 4.
        return min(20.0, self._order_manager.bankroll * 0.02)
