"""Sports Underdog V3.6 US — us region companion to sports_underdog_v36_playoff (eu).

Built 2026-04-30 alongside v36_playoff to cover the NBA playoff 35¢+ underdog
alpha in both regions. Observer / trades evidence at 35¢+ floor:

  Region    n_games  WR     ROI       Strategy variants
  eu          5      67%    +137-277%  underdog_v31, v32 (LIVE+twin)
  us          12     ~70%   +88-189%   underdog_v34 (paper) + v35 (LIVE+twin)
  Combined    8      62%    +183%      across all v3X (game-level dedup)

Both regions show consistent positive ROI at 35¢+ — eu/us nearly identical.
Two separate strategies (this + v36_playoff) avoid multi-region complexity
in sports_trader's REGION_OVERRIDE design.

Live-promoted at base 8 alongside v36_playoff (eu). PLAYOFF_ONLY=True so
fires only during NBA playoff window per core.season calendar; auto-suspends
for regular season (where v34/v35 paper handle us coverage with 0.20 floor).
"""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v36_us")


class SportsUnderdogV36USStrategy(SportsUnderdogV31Strategy):
    """V3.1 underdog logic restricted to NBA playoff pre-game with 35¢+ floor (us region)."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v36_us_tickers.json"
    STRATEGY_NAME = "sports_underdog_v36_us"
    REGION_OVERRIDE = "us"
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
    PLAYOFF_ONLY = True
    # Floor 0.35 → 0.30 (2026-05-01). Re-analysis with grown observer sample
    # showed 30-35¢ band now strongly positive (+98% us, +118% eu). Trades
    # data agrees: n=7 +279% us. Symmetric with v36_playoff (eu) — both regions
    # confirm 30¢ as the right floor; 35¢ left $200+ alpha on the table.
    MIN_ENTRY_PRICE = 0.30
    # Override v31-inherited gates.
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()  # this IS the playoff strategy
    MIN_DEPTH_USDC = 10.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(8.0, self._order_manager.bankroll * 0.02)
