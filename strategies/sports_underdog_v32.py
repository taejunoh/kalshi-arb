"""Sports Underdog V3.2 — V3.1 + edge-scaled position sizing (paper-only)."""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v32")

# Underdog's payoff threshold (2.0x). 1.0× at threshold, 2.0× at 4.0x payoff.
_PAYOFF_THRESHOLD = 2.0


class SportsUnderdogV32Strategy(SportsUnderdogV31Strategy):
    TRADED_TICKERS_FILE = "data/sports_underdog_v32_tickers.json"
    STRATEGY_NAME = "sports_underdog_v32"
    # NBA playoff suspend (2026-04-30). Game-level: v32 NBA pre eu -87%
    # n=28 (worse than v31 -36%) — edge-scaled sizing amplifies losses
    # in NBA playoff regime. Same-game analysis shows v32 sizes up on
    # losing games at ~1.1x v31 cost. Auto-resumes when NBA returns to
    # regular season. v32 evaluation continues on MLB/NHL pre cells.
    PLAYOFF_BLOCKLIST = frozenset({"NBA"})

    def _get_trade_size(self, signal: dict) -> float:
        base = super()._get_trade_size(signal)
        payoff = float(signal.get("edge", _PAYOFF_THRESHOLD))
        multiplier = min(2.0, max(0.5, payoff / _PAYOFF_THRESHOLD))
        return base * multiplier
