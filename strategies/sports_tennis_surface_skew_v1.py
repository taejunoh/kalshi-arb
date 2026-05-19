"""Tennis surface-skew Pinnacle-vs-eu (C).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Pinnacle prices surface specialization (clay/grass) more aggressively
than other books. When Pinnacle diverges from eu consensus on a clay
or grass match, that gap is Pinnacle's surface-aware lean. We follow
it before Kalshi adjusts.

Distinct from A1 (which uses Kalshi-Pinnacle gap). Hard-court excluded
as surface-neutral.

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy, TENNIS_SLUG_META

logger = logging.getLogger("sports_tennis_surface_skew_v1")


class SportsTennisSurfaceSkewV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_surface_skew_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_surface_skew_v1"
    BADGE_LABEL = "C"
    BADGE_COLOR = "#fbbf24"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    INDIVIDUAL_SPORTS_OPT_IN = True

    # Hard blocklist — OBSERVATION_ONLY_SPORTS subtraction only removes
    # ATP/WTA from the observation set; team sports were never in that set
    # so without this they'd flow through. Targets: ATP, WTA only.
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        "UFC",
    })

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0
    MIN_DEPTH_USDC = 30.0
    MIN_ENTRY_PRICE = 0.20
    MAX_ENTRY_PRICE = 0.80

    ELIGIBLE_SURFACES = frozenset({"clay", "grass"})
    # 2026-05-19: relaxed from 0.04 to 0.03. Backtest on 55 settled clay markets
    # showed thr=0.04 fires 0 (too strict); thr=0.03 fires n=2 WR 100% PnL +36%.
    # n is tiny — paper-only result, re-evaluate at 6/1 promotion eval.
    MIN_SURFACE_SKEW = 0.03
    MAX_PINNACLE_AGE_S = 600

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        slug = getattr(self, "_last_tournament_slug", None)
        if slug is None:
            return None
        meta = TENNIS_SLUG_META.get(slug)
        if meta is None or meta.get("surface") not in self.ELIGIBLE_SURFACES:
            return None

        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None

        skew = pin - fair_prob
        if abs(skew) < self.MIN_SURFACE_SKEW:
            return None

        if skew > 0:
            side, price = "yes", kalshi_yes
        else:
            side, price = "no", kalshi_no

        if not (self.MIN_ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
            return None

        return {
            "side": side,
            "price": price,
            "edge": abs(skew),
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
