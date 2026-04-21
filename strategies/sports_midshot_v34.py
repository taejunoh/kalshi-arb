"""Sports Midshot V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (from 2026-04-21 4D matrix):
  (NBA, pre):  us +46% vs eu +13% (n=36 vs 28) — headline region gap
  (MLB, live): us +20% vs eu +9%  (n=68 vs 186) — us-preferred live cell
MLB midshot pre is eu-preferred (+94% vs +65%); leave to V3.1. NHL midshot
fails in both regions (matrix) — excluded entirely.
"""
import logging

from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v34")


class SportsMidshotV34Strategy(SportsMidshotV31Strategy):
    """Midshot V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v34_tickers.json"
    STRATEGY_NAME = "sports_midshot_v34"
    REGION_OVERRIDE = "us"
    ALLOWED_CELLS = frozenset({("NBA", "pre"), ("MLB", "live")})
