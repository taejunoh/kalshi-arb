"""ATP Grand Slam BO5 chalk-hold (A2).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

BO5 reduces variance for the favorite. Top ATP seeds at -300 win
~85-88% in BO5 vs ~78% in BO3-equivalent lines. Kalshi inherits BO3
mental model — we BUY YES on favorites in early rounds where the seed
advantage is largest.

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import (
    SportsTraderStrategy, TENNIS_SLUG_META, parse_tennis_round,
)

logger = logging.getLogger("sports_atp_gs_bo5_chalk_v1")


class SportsAtpGsBo5ChalkV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_atp_gs_bo5_chalk_v1_tickers.json"
    STRATEGY_NAME = "sports_atp_gs_bo5_chalk_v1"
    BADGE_LABEL = "A2"
    BADGE_COLOR = "#a78bfa"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP"}
    )

    INDIVIDUAL_SPORTS_OPT_IN = True

    # Hard blocklist — OBSERVATION_ONLY_SPORTS subtraction only removes
    # ATP from the observation set; team sports + WTA were never in that
    # set so without this they'd flow through. Target: ATP only.
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        "UFC", "WTA",
    })

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0  # 3 days
    MIN_DEPTH_USDC = 30.0
    MIN_ENTRY_PRICE = 0.70
    MAX_ENTRY_PRICE = 0.85

    ELIGIBLE_ROUNDS = frozenset({"R128", "R64", "R32"})
    MIN_PINNACLE_CONFIRM = 0.72

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        slug = getattr(self, "_last_tournament_slug", None)
        if slug is None:
            return None
        meta = TENNIS_SLUG_META.get(slug)
        if meta is None or meta.get("format") != "BO5" or meta.get("tier") != "GS":
            return None

        # Round info ("Round Of 64", "Quarterfinal", …) lives in the full
        # Kalshi market title, not in yes_sub_title (which carries only the
        # player's name). Stashed alongside the slug by _stash_market_context.
        title = getattr(self, "_last_market_title", None)
        round_code = parse_tennis_round(title or "")
        if round_code not in self.ELIGIBLE_ROUNDS:
            return None

        if not (self.MIN_ENTRY_PRICE <= kalshi_yes <= self.MAX_ENTRY_PRICE):
            return None

        pin = self._last_pinnacle_yes_prob
        if pin is None or pin < self.MIN_PINNACLE_CONFIRM:
            return None

        return {
            "side": "yes",
            "price": kalshi_yes,
            "edge": pin - kalshi_yes,
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(6.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)
