"""Sports Midshot V3.2 — V3.1 + edge-scaled position sizing (paper-only)."""
import logging

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v32")

# Midshot's payoff threshold (2.5x). 1.0× at threshold, 2.0× at 5.0x payoff.
_PAYOFF_THRESHOLD = 2.5


class SportsMidshotV32Strategy(SportsMidshotV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_midshot_v32_tickers.json"
    STRATEGY_NAME = "sports_midshot_v32"
    BADGE_LABEL = "V3.2"
    BADGE_COLOR = "#10b981"
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)

    def _get_trade_size(self, signal: dict) -> float:
        base = super()._get_trade_size(signal)
        payoff = float(signal.get("edge", _PAYOFF_THRESHOLD))
        multiplier = min(2.0, max(0.5, payoff / _PAYOFF_THRESHOLD))
        return base * multiplier
