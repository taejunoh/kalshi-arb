"""UFC heavy-chalk fade (B1).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

MMA single-shot variance — heavy chalk (>= 0.78 implied) historically
underperforms by 3-5pp due to KO/submission risk. We BUY NO when both
Kalshi and Pinnacle price the favorite >= 0.78.

Paper-twin only at launch. Promotion gate relaxed (n>=15) per slow
card cadence (~4-8 trades/month).
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_ufc_chalk_fade_v1")


class SportsUfcChalkFadeV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_ufc_chalk_fade_v1_tickers.json"
    STRATEGY_NAME = "sports_ufc_chalk_fade_v1"
    BADGE_LABEL = "B1"
    BADGE_COLOR = "#f87171"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"UFC"}
    )

    # Hard blocklist — OBSERVATION_ONLY_SPORTS subtraction only removes
    # UFC from the observation set; team sports + tennis were never in that
    # set so without this they'd flow through. Target: UFC only.
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        "ATP", "WTA",
    })

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0
    MIN_DEPTH_USDC = 20.0
    MIN_ENTRY_PRICE = 0.78

    MIN_PINNACLE_CONFIRM = 0.78
    MAX_PINNACLE_AGE_S = 600

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        if kalshi_yes < self.MIN_ENTRY_PRICE:
            return None
        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None
        if pin < self.MIN_PINNACLE_CONFIRM:
            return None
        return {
            "side": "no",
            "price": kalshi_no,
            "edge": (kalshi_yes + pin) / 2.0 - 0.78,
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(2.0, self._order_manager.bankroll * 0.02)
