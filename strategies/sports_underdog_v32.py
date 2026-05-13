"""Sports Underdog V3.2 — V3.1 + edge-scaled position sizing (paper-only)."""
import logging

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v32")

# Underdog's payoff threshold (2.0x). 1.0× at threshold, 2.0× at 4.0x payoff.
_PAYOFF_THRESHOLD = 2.0


class SportsUnderdogV32Strategy(SportsUnderdogV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_underdog_v32_tickers.json"
    STRATEGY_NAME = "sports_underdog_v32"
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
    # NBA playoff suspend (2026-04-30). Game-level: v32 NBA pre eu -87%
    # n=28 (worse than v31 -36%) — edge-scaled sizing amplifies losses
    # in NBA playoff regime. Same-game analysis shows v32 sizes up on
    # losing games at ~1.1x v31 cost.
    # 2026-05-03: NHL added — paper n=28 ROI -23%, live n=5 -7% (same
    # direction). NHL playoff regime mismatch confirmed across v31 trio
    # and v32. v36_nhl_playoff picks up the 35-45c+50-55c sweet spot via
    # pure price-band gate with MAX_MTS_AT_ENTRY=1440. Auto-resumes when
    # each sport returns to regular season.
    PLAYOFF_BLOCKLIST = frozenset({"NBA", "NHL"})

    def _get_trade_size(self, signal: dict) -> float:
        base = super()._get_trade_size(signal)
        payoff = float(signal.get("edge", _PAYOFF_THRESHOLD))
        multiplier = min(2.0, max(0.5, payoff / _PAYOFF_THRESHOLD))
        return base * multiplier
