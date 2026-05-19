"""Tennis underdog band trader (paper).

Plain price-band underdog signal — mirrors the team-sport `sports_underdog_v31`
pattern. Hypothesis: Kalshi tennis retail flow overprices favorites, leaving
mild underdogs (0.25-0.35 yes_ask) systematically underpriced.

Observer backtest 2026-05-19 across 55 settled ATP/WTA markets (Italian Open
tail + early French Open / Hamburg / Strasbourg):
  ya 0.25-0.35 BUY YES → n=8, WR 37.5% (vs implied ~30%), PnL/n +6.3%
  ya 0.85-0.95 BUY YES → n=7, WR 71.4% (vs implied ~90%), PnL/n -21.0%

The asymmetry (underdog WR > implied, chalk WR < implied) matches the
team-sport longshot/underdog patterns. Small n — paper-twin only at launch,
re-evaluate at 6/1 promotion eval after French Open R1 + Hamburg
accumulate ~100+ settled outcomes.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_underdog_v1")


class SportsTennisUnderdogV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_underdog_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_underdog_v1"
    BADGE_LABEL = "TU1"
    BADGE_COLOR = "#fb923c"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    INDIVIDUAL_SPORTS_OPT_IN = True

    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        "UFC",
    })

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0  # 3 days
    MIN_DEPTH_USDC = 30.0
    MIN_ENTRY_PRICE = 0.25
    MAX_ENTRY_PRICE = 0.35

    # Pinnacle sanity gate: don't fire when Pinnacle thinks the underdog is
    # even MORE of a longshot than Kalshi has it. (Strong-favorite chalk
    # should still go to A2/surface_skew, not here.) None = age-only check.
    MAX_PINNACLE_AGE_S = 600

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        # Tennis Pinnacle freshness gate — same shape as A1/A2/C.
        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None

        # Price band gate. Base class already enforces MIN/MAX_ENTRY_PRICE
        # downstream, but recheck explicitly so evaluate is self-contained
        # for tests and for clarity.
        if not (self.MIN_ENTRY_PRICE <= kalshi_yes <= self.MAX_ENTRY_PRICE):
            return None

        # Pinnacle veto: skip when Pinnacle's yes_prob is materially BELOW
        # Kalshi (Pinnacle thinks the underdog is even less likely). This
        # filters out the cases where the price-band signal disagrees with
        # the sharpest line — sample-size protection until 6/1 eval.
        if pin < kalshi_yes - 0.05:
            return None

        return {
            "side": "yes",
            "price": kalshi_yes,
            "edge": pin - kalshi_yes,
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        # Conservative paper sizing — matches v31 family base ($4 micro) so
        # any live promotion has a familiar reference point.
        return min(4.0, self._order_manager.bankroll * 0.02)
