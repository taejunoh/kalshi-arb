"""Tennis Pinnacle-Kalshi spread arb (A1).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Pinnacle is the sharpest tennis line. Kalshi tennis tickers are illiquid
in the 1-3 day pre-match window, allowing retail drift away from Pinnacle.
We arb the gap when |Kalshi_yes_ask - Pinnacle_yes_prob| >= 0.05 AND eu
consensus confirms Pinnacle is sane (within 3pp).

Paper-twin only at launch — no LIVE_STRATEGIES env entry.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_pinnacle_arb_v1")


class SportsTennisPinnacleArbV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_pinnacle_arb_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_pinnacle_arb_v1"
    BADGE_LABEL = "A1"
    BADGE_COLOR = "#60a5fa"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    # Opt into the individual-sport routing path (yes_sub_title = player name,
    # individual-sport matcher, per-tournament slug). Base class default is
    # False so team-sport strategies never reach KXATPMATCH/WTAMATCH tickers.
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
    MIN_DEPTH_USDC = 50.0
    MIN_ENTRY_PRICE = 0.10
    MAX_ENTRY_PRICE = 0.90

    MIN_KALSHI_PINN_GAP = 0.05
    MAX_EU_PINNACLE_DRIFT = 0.03
    MAX_PINNACLE_AGE_S = 600

    def _size_for_edge(self, edge: float) -> float:
        size = 5.0 + round(edge * 100) * 1.0
        return min(size, 30.0)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None

        # fair_prob IS the eu consensus per sports_trader.py — eu/Pinnacle drift
        # is the gap between the broad-book consensus and Pinnacle's sharper line.
        if abs(fair_prob - pin) > self.MAX_EU_PINNACLE_DRIFT:
            return None

        gap_yes = pin - kalshi_yes
        gap_no = (1.0 - pin) - kalshi_no

        if gap_yes >= self.MIN_KALSHI_PINN_GAP and gap_yes >= gap_no:
            return {
                "side": "yes",
                "price": kalshi_yes,
                "edge": gap_yes,
                "strategy": self.STRATEGY_NAME,
                "ticker": ticker,
            }
        if gap_no >= self.MIN_KALSHI_PINN_GAP:
            return {
                "side": "no",
                "price": kalshi_no,
                "edge": gap_no,
                "strategy": self.STRATEGY_NAME,
                "ticker": ticker,
            }
        return None

    def _get_trade_size(self, signal: dict) -> float:
        return self._size_for_edge(signal.get("edge", 0.0))
