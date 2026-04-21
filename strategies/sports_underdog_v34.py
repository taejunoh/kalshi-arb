"""Sports Underdog V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (from 2026-04-21 4D matrix):
  (MLB, live): us +38% vs eu +3%   (n=43 vs 136) 🔥 HIDDEN ALPHA — the big one
  (NBA, pre):  us +34% vs eu +38%  (n=33 each) — essentially tied, capture both
  (NHL, pre):  us +11% vs eu +11%  (n=30 vs 27) — tied, capture both
MLB underdog pre: eu +14% beats us +0.4% → leave to V3.1.
"""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v34")


class SportsUnderdogV34Strategy(SportsUnderdogV31Strategy):
    """Underdog V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v34_tickers.json"
    STRATEGY_NAME = "sports_underdog_v34"
    REGION_OVERRIDE = "us"
    ALLOWED_CELLS = frozenset({
        ("MLB", "live"),  # hidden alpha cell (+38% us vs +3% eu)
        ("NBA", "pre"),
        ("NHL", "pre"),
    })
