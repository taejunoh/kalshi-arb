"""Sports Midshot V3.4 — V3.1 logic + sport-aware US region cells (paper-only).

Cell selection (from 2026-04-21 4D matrix):
  (NBA, pre):  us +46% vs eu +13% (n=36 vs 28) — headline region gap
  (MLB, live): us +20% vs eu +9%  (n=68 vs 186) — us-preferred live cell
  (MLB, pre):  us +65% (n=11, expected Live ROI ~+56% after fee+slip) — eu
    still higher (+94%) so V3.1 takes the eu side, but the uncovered us cell
    is too positive to leave empty. Coexists with V3.1 on the same Kalshi
    tickers; fires only when us consensus produces a midshot signal V3.1's
    eu consensus doesn't.
NHL midshot fails in both regions — excluded entirely.
"""
import logging

from strategies.sports_midshot_v31 import SportsMidshotV31Strategy

logger = logging.getLogger("sports_midshot_v34")


class SportsMidshotV34Strategy(SportsMidshotV31Strategy):
    """Midshot V3.1 logic + region=us + per-cell allow matrix."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v34_tickers.json"
    STRATEGY_NAME = "sports_midshot_v34"
    REGION_OVERRIDE = "us"
    # Defensive override — v31 raised to base 16 / depth 20 on 2026-04-25;
    # v34/v35 stay at base 4 / depth 5 until their own paper alpha justifies.
    MIN_DEPTH_USDC = 5.0
    ALLOWED_CELLS = frozenset({
        ("NBA", "pre"), ("MLB", "live"), ("MLB", "pre"),
    })

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
