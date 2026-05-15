"""Tennis bid-imbalance signal (A3).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Top-of-book bid-size skew as a settlement-direction predictor. Validated
on crypto observers (Phase 1 bid_imbalance_v1, pre-revision 18/26=69%
WR). Tennis adds 21+ book eu consensus + Pinnacle confirm.

Mandatory Phase 0 gate: replay must show top-decile WR >= baseline + 6pp
on full universe before paper-twin enable. Guards against
bid_imbalance_v2 selection-bias trap (memory/bid_imbalance_v2_2026-05-15).

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_bid_imbalance_v1")


class SportsTennisBidImbalanceV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_bid_imbalance_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_bid_imbalance_v1"
    BADGE_LABEL = "A3"
    BADGE_COLOR = "#34d399"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    # Hard blocklist — OBSERVATION_ONLY_SPORTS subtraction only removes
    # ATP/WTA from the observation set; team sports were never in that set
    # so without this they'd flow through. Targets: ATP, WTA only.
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        "UFC",
    })

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 1440.0  # intraday only
    MIN_MTS_AT_ENTRY = 60.0
    MIN_DEPTH_USDC = 20.0

    IMBALANCE_YES_THRESHOLD = 0.65
    IMBALANCE_NO_THRESHOLD = 0.35
    YES_PRICE_BAND = (0.50, 0.85)
    NO_PRICE_BAND = (0.15, 0.50)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        mts = getattr(self, "_last_mts", None)
        if mts is None or mts < self.MIN_MTS_AT_ENTRY:
            return None
        if mts > self.MAX_MTS_AT_ENTRY:
            return None

        pin = self._last_pinnacle_yes_prob
        if pin is None or self._last_pinnacle_age_s is None:
            return None

        try:
            yb, _ya, nb, _na = self._client.get_top_of_book_sizes(ticker)
        except (AttributeError, Exception) as e:
            logger.debug(f"[{self.STRATEGY_NAME}] {ticker}: orderbook fetch failed: {e}")
            return None
        if (yb + nb) <= 0:
            return None
        imbalance = yb / (yb + nb)

        if imbalance > self.IMBALANCE_YES_THRESHOLD:
            lo, hi = self.YES_PRICE_BAND
            if lo <= kalshi_yes <= hi and pin > kalshi_yes:
                return {
                    "side": "yes",
                    "price": kalshi_yes,
                    "edge": imbalance,
                    "strategy": self.STRATEGY_NAME,
                    "ticker": ticker,
                }

        if imbalance < self.IMBALANCE_NO_THRESHOLD:
            lo, hi = self.NO_PRICE_BAND
            if lo <= kalshi_yes <= hi and pin < kalshi_yes:
                return {
                    "side": "no",
                    "price": kalshi_no,
                    "edge": 1.0 - imbalance,
                    "strategy": self.STRATEGY_NAME,
                    "ticker": ticker,
                }

        return None

    def _get_trade_size(self, signal: dict) -> float:
        return min(3.0, self._order_manager.bankroll * 0.02)
