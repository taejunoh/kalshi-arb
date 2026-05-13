"""Sports Longshot V3.2 — V3.1 + edge-scaled position sizing.

Paper-only experiment: scale stake by the signal's payoff ratio. Hypothesis
is that higher-payoff signals carry (at least proportionally) higher EV, so
a Kelly-ish multiplier should lift ROI vs V3.1's fixed-size entries. Shares
V3.1's 20¢ dead-zone filter and EU region.
"""
import logging

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy

logger = logging.getLogger("sports_longshot_v32")

# Longshot's payoff threshold (3.0x). Scale 1.0× at threshold up to 2.0× at
# double the threshold (6.0x payoff). Below threshold the signal wouldn't
# fire, but clamp at 0.5× for safety if evaluate logic ever loosens.
_PAYOFF_THRESHOLD = 3.0


class SportsLongshotV32Strategy(SportsLongshotV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_longshot_v32_tickers.json"
    STRATEGY_NAME = "sports_longshot_v32"
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)

    def _get_trade_size(self, signal: dict) -> float:
        base = super()._get_trade_size(signal)
        payoff = float(signal.get("edge", _PAYOFF_THRESHOLD))
        multiplier = min(2.0, max(0.5, payoff / _PAYOFF_THRESHOLD))
        return base * multiplier
