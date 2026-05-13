"""Sports v36 WNBA — slight-favorite YES at 35-55c with fair_prob > 0.55.

Built 2026-05-13 from WNBA observer replay (n=3 settled games, ROI +73%,
WR 100%). Per spec docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md.

This is a NEW strategy class — NOT a port of v36_playoff. v36_playoff is
NBA underdog 30c+ floor; v36 is WNBA chalk-favorite (price < fair).
The "v36" naming follows user spec convention but evaluate() logic is
fresh.

WARNING: n=3 is well below promotion gate (n>=25). All 3 settled fires
were chalk wins — alpha unverified. First eval scheduled 2026-05-23.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v36")


class SportsUnderdogV36Strategy(SportsUnderdogV31Strategy):
    """Bet the slight-favorite side (fair > 0.55) at 0.35-0.55 YES on WNBA pre-game."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v36_tickers.json"
    STRATEGY_NAME = "sports_underdog_v36"
    REGION_OVERRIDE = "eu"

    # WNBA pre-game only
    ALLOWED_CELLS = frozenset({("WNBA", "pre")})
    PLAYOFF_ONLY = False  # WNBA reg season, no playoff calendar entry
    PRE_GAME_ONLY = True

    # Override parent (v31 underdog) blocklists — ALLOWED_CELLS subsumes
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()

    # v31 underdog removed WNBA from OBSERVATION_ONLY in Task 4 — v36
    # inherits that override. Re-apply explicitly for clarity / robustness:
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    # Chalk-favorite price band — matches replay n=3 ROI +73%
    MIN_ENTRY_PRICE = 0.35
    MAX_ENTRY_PRICE = 0.55
    MIN_DEPTH_USDC = 20.0

    # Slight-favorite confidence threshold
    FAIR_PROB_MIN_FAVORITE = 0.55

    def _get_trade_size(self, signal: dict) -> float:
        # 2026-05-13: launched at 8, bumped to 12 same-day per user direction.
        # WNBA depth p50 $567 supports 12 USDC peak stake at 0.55 = $6.60
        # → 86× cushion at tier4 depth.
        return min(12.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float,
                 fair_prob: float, ticker: str = "", **kwargs) -> Optional[dict]:
        """Fire YES if YES side is consensus favorite (fair > 0.55) priced
        below fair in 35-55c band. Mirror logic for NO side. Both sides
        checked; pair-fire dedup prevents double-betting same outcome via
        sibling tickers."""
        # Pair-fire dedup (WNBA-only by ALLOWED_CELLS, unconditional here)
        if ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # YES side: YES team is favorite (fair > 0.55), priced at 0.35-0.55
        if (fair_prob > self.FAIR_PROB_MIN_FAVORITE and
                self.MIN_ENTRY_PRICE <= kalshi_yes <= self.MAX_ENTRY_PRICE):
            fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes
            payoff = 1.0 / (kalshi_yes * (1 + fee_pct))
            return {
                "side": "yes", "price": kalshi_yes,
                "edge": payoff, "strategy": "v36",
            }

        # NO side: NO team is favorite (fair_yes < 0.45 → fair_no > 0.55),
        # priced at 0.35-0.55
        if (fair_prob < (1 - self.FAIR_PROB_MIN_FAVORITE) and
                self.MIN_ENTRY_PRICE <= kalshi_no <= self.MAX_ENTRY_PRICE):
            fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
            payoff = 1.0 / (kalshi_no * (1 + fee_pct))
            return {
                "side": "no", "price": kalshi_no,
                "edge": payoff, "strategy": "v36",
            }

        return None
