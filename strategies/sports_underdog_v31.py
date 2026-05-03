"""Sports Underdog V3.1 — V3 underdog logic against EU sharp-book consensus."""
import logging

from strategies.sports_underdog import SportsUnderdogStrategy

logger = logging.getLogger("sports_underdog_v31")


class SportsUnderdogV31Strategy(SportsUnderdogStrategy):
    """Same underdog logic (fade consensus favorite > 60%, 2x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v31_tickers.json"
    STRATEGY_NAME = "sports_underdog_v31"
    REGION_OVERRIDE = "eu"
    # Depth gate scaled to size 30 (2026-05-02 second pass). At max underdog
    # entry price ~60¢, peak stake = 30 × $0.60 = $18 → $30 keeps ~1.67×
    # cushion (matches the prior buffer ratio).
    MIN_DEPTH_USDC = 30.0
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
    # 2026-05-03 NHL added — n=44 LIVE -12% (PnL -$25). Bleed concentrated
    # in 25-30c (-100% n=4 NO), 35-40c NO (-55% n=11). The 35-45c+50-55c
    # sweet spot is ~break-even (-4.5% n=33) — that band is now picked up
    # by sports_underdog_v36_nhl_playoff (pure price-band gate, paper-twin
    # baseline +33%). v32 has its own PLAYOFF_BLOCKLIST.
    PLAYOFF_BLOCKLIST = frozenset({"NBA", "NHL"})

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 25→30 on 2026-05-02 (second pass, 1.2x more, total 1.5x vs
        # original 20). Live n=224, ROI 17.6% all-time and 14.7% last-7d are
        # consistent — alpha is mature and the prior 1.25x bump didn't
        # destabilize anything. Modest extra bump to harvest the larger
        # capital base before bankroll constrains us further.
        return min(30.0, self._order_manager.bankroll * 0.02)
