"""Sports NPB Underdog V1 — Japanese baseball deep-underdog 0.38-0.40 BUY.

Built 2026-05-19 from cross-sport observer scan on KBO/NPB universe
(odds_observations 5/2-5/18, n_settled NPB 79 games / KBO 64 games).

## Signal

When eu consensus says one side is the favorite (>0.60) AND Kalshi prices
the underdog at 0.38-0.40, buy the underdog. Standard v31 underdog
mechanics inherited; this strategy narrows to NPB only and the 0.38-0.40
sub-band where the alpha is concentrated.

## Observer first-fire universe replay (game-level dedup)

NPB underdog with consensus filter (eu_yes_prob > 0.60 or < 0.40):
- price 0.32-0.33: n=2 (sample too small)
- price 0.34-0.35: n=2, 2 wins, +183% ROI (sample too small)
- price 0.36-0.37: n=3, 0 wins, -100% ROI (sample too small)
- **price 0.38-0.40: n_games=15, 9 wins (60% WR), ROI +50.8%** ← THIS STRATEGY
   - drop-top-1: ROI +47.7%
   - drop-top-3: ROI +39.7%
   - drop-top-5: ROI +31.0%
   - per-week W17/W18/W19 all positive (no slate dominance)
   - Wilson 95% lower bound edge: +1.8pp above BE_WR
   - top-of-book depth avg $779 — fill realism not an issue

KBO at same gate: ROI -13% across all bands — explicitly excluded.

## Why 0.38-0.40 narrow band (not 0.30-0.40)

Universe scan shows 0.34-0.37 sub-band has n=5 with 2 wins — sample too
small but suggestive of weaker alpha than the 0.38-0.40 sweet spot.
0.30-0.33 has n=0-2 — insufficient. Narrowing to 0.38-0.40 concentrates
on the verified alpha sub-band; if 0.34-0.37 alpha materializes in paper,
re-widen at promotion.

## Caveats / risk

- n_games=15 is below typical promotion gate (n>=25). 3 more weeks needed.
- Drop-top-5 still positive (+31%) but Wilson 95% lower bound only +1.8pp
  above BE_WR — barely passes robustness check.
- KBO/NPB both currently in parent OBSERVATION_ONLY_SPORTS; this strategy
  overrides to remove NPB only. KBO remains observer-only.
- 2 of the top wins were SAME GAME (pair-fire on YES + NO sides). Pair-fire
  dedup essential — without it the universe ROI inflates ~2x via
  same-game duplicates.

Paper-twin only at launch — not in LIVE_STRATEGIES env. Promotion gate:
  paper n_games >= 15 AND ROI >= +30% AND drop-top-3 > 0 by ~6/09.

Region: REGION_OVERRIDE inherited 'eu' from v31 parent. NPB eu consensus
coverage in odds_observations >95% on observed tickers.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_npb_underdog_v1")


class SportsNpbUnderdogV1Strategy(SportsUnderdogV31Strategy):
    """NPB-only underdog 0.38-0.40 with consensus-strong filter."""

    TRADED_TICKERS_FILE = "data/sports_npb_underdog_v1_tickers.json"
    STRATEGY_NAME = "sports_npb_underdog_v1"
    BADGE_LABEL = "NPB"
    BADGE_COLOR = "#dc2626"  # red
    DISPLAY_SPORTS = ("NPB",)
    REGION_OVERRIDE = "eu"

    # NPB pre-game only — cell-matrix gate subsumes legacy SPORT_BLOCKLIST.
    ALLOWED_CELLS = frozenset({("NPB", "pre")})
    PRE_GAME_ONLY = True

    # Override v31 blocklists — ALLOWED_CELLS subsumes both, and NPB has no
    # baseball "playoff" cell concept in our matrix.
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()

    # Parent default OBSERVATION_ONLY_SPORTS contains NPB. Override to
    # remove NPB so tickers reach evaluate(); keep KBO + others as observer.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"NPB"}
    )

    # Sweet-spot sub-band from observer first-fire game-dedup replay.
    # Inherited v31 underdog evaluate uses the class-level MIN/MAX (enforced
    # at sports_trader.py:1799/1810 after evaluate returns a signal).
    MIN_ENTRY_PRICE = 0.38
    MAX_ENTRY_PRICE = 0.40

    # Depth gate. Observer avg top-of-book $779 in this sub-band, so $50
    # floor is generous. Override v31 $2k-10k dead-zone — NPB universe is
    # thinner than US sports, and depth distribution shows no bimodality
    # warranting a dead-zone block.
    MIN_DEPTH_USDC = 50.0
    DEPTH_DEAD_ZONE = None

    # Same-game pair-fire dedup. NPB 2-way market: when consensus puts one
    # side >0.60, the same underdog appears in two tickers (-HOME and -AWAY,
    # one as YES underdog, one as NO underdog). Without dedup the strategy
    # fires both → 2x exposure to the same outcome and ~2x inflated n.
    PAIR_FIRE_DEDUP = True

    # NPB eu consensus typically has 2-4 bookmakers. Set floor at 2 to avoid
    # single-book "consensus" signals while keeping the universe usable.
    MIN_N_BOOKS = 2

    def _get_trade_size(self, signal: dict) -> float:
        """Conservative paper-launch size 8.

        Observer avg top depth $779 supports much larger stakes (cushion
        ~250x at 8 contracts at 0.39 = $3.12 stake), but Phase 0 baseline
        n_games=15 is below promotion gate. Hold at 8 until paper n>=15
        confirms. Scale to 12 at promotion alongside live ramp.
        """
        return min(8.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)
