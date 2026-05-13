"""Sports v37 WNBA — TEAM-only deep-underdog 20-29c band, payoff >= 2.0x.

Built 2026-05-13. Sibling class to the renamed sports_soccer_underdog_v1
(formerly sports_underdog_v37_soccer pre commit de80af9). Same evaluate()
logic (inherited via super()) but SPORT_BLOCKLIST flipped to block soccer
+ other non-WNBA sports while allowing WNBA.

Replay baseline (WNBA observer, n=2 settled games): ROI +118%, WR 50%.
n far below promotion gate (>=25) — first eval 2026-05-23. See spec
e72f1de.
"""
import logging
from typing import Optional

from strategies.sports_soccer_underdog_v1 import SportsSoccerUnderdogV1Strategy

logger = logging.getLogger("sports_underdog_v37")


class SportsUnderdogV37Strategy(SportsSoccerUnderdogV1Strategy):
    """v37 TEAM 20-29c band applied to WNBA only."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v37_tickers.json"
    STRATEGY_NAME = "sports_underdog_v37"

    BADGE_LABEL = "V3.7"
    BADGE_COLOR = "#f97316"
    DISPLAY_SPORTS = ("NBA", "MLB", "NHL", "WNBA")
    # Block everything except WNBA (soccer blocked here too)
    SPORT_BLOCKLIST = frozenset({"NBA", "MLB", "NHL", "NPB", "KBO",
                                  "MLS", "EPL", "LAL", "UCL", "BUN"})

    # Inherit OBSERVATION_ONLY_SPORTS = frozenset() from parent
    # → already allows WNBA + all other sports. Combined with SPORT_BLOCKLIST
    # above, only WNBA reaches evaluate().

    # Override soccer parent's 24h pre-game cap. WNBA observations regularly
    # span >24h (e.g. 2026-05-13: NYP@PDX evaluated at mts=2030, blocked
    # silently — would have fired YES@0.26 in band with payoff 3.2x). Match
    # v36 / v31 family (no cap).
    MAX_MTS_AT_ENTRY = None

    def _get_trade_size(self, signal: dict) -> float:
        # 2026-05-13: override parent's depth-tiered soccer caps (2-24 by tier)
        # with fixed size 12. WNBA depth p50 $567 (tier4 territory) → 12 USDC
        # peak stake at 0.29 = $3.48 → 163× cushion. Sized symmetric with v36.
        return min(12.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float,
                 fair_prob: float, ticker: str = "", **kwargs) -> Optional[dict]:
        """Pair-fire dedup, then delegate to parent evaluate (20-29c TEAM)."""
        if ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
