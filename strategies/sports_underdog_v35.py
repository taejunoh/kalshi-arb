"""Sports Underdog V3.5 — V3.4's (NBA, pre) cell promoted to live trading.

Coexists with V3.4 paper on the same cell — V3.4 keeps recording paper for the
matrix evaluation, V3.5 places live + paper-twin entries on every same signal.

Live promotion rationale (2026-04-21 4D matrix, snapshot 2026-04-22):
  (NBA, pre) us: +34% ROI vs eu +38% (n=33 each) — essentially tied;
  promoting the us side here, eu side stays in V3.1 live.
"""
import logging

from strategies.sports_underdog_v34 import SportsUnderdogV34Strategy

logger = logging.getLogger("sports_underdog_v35")


class SportsUnderdogV35Strategy(SportsUnderdogV34Strategy):
    """Live-promoted subset of V3.4 underdog — only (NBA, pre) us."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v35_tickers.json"
    STRATEGY_NAME = "sports_underdog_v35"
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
