"""Sports Underdog V3.7 Soccer — soccer-only deep-underdog 20-29c TEAM-only.

Built 2026-05-06 from observer analysis of MLS/EPL/LAL/BUN/UCL pre-game data
collected since 2026-05-02. Pure price-band gate; TIE outcomes excluded.

Per-bucket TEAM-only ROI (5 leagues combined, last 5d settled, observer
replay with trades-derived outcomes):
  20-25c:  n=7,  WR 71%, ROI +234%
  25-29c:  n=6,  WR 33%, ROI +18%
  Combined 20-29c: n=13, WR 54%, ROI +120% (drop-top-3 +55%, drop-top-5 -7%)

TIE excluded after the same-window TIE 25-30c bleed across all leagues
(n=18, ROI -20%) — TIE prices around 0.20-0.30 are systematically slight
overpriced vs the ~25% draw probability that's structural to soccer.

Region-agnostic: midshot/underdog evaluate() do not use fair_prob, and
the eu/us consensus gap on this corpus is ~0%. REGION_OVERRIDE='eu' is
arbitrary.

Slate concentration risk: 77% of the n=13 sample is from the 5/02 MLS
slate. Promotion gate is n_games >= 25 + ROI >= +30% + drop-top-3 > 0.
At current 3.2 games/day pace, n=25 is ~4 days out (~5/10).

Paper-twin only at launch — no LIVE_STRATEGIES env entry. Promotion
decision after 4-6 weeks of paper data.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v37_soccer")


class SportsUnderdogV37SoccerStrategy(SportsUnderdogV31Strategy):
    """Soccer-only deep-underdog (20-29c TEAM, payoff>=2x)."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v37_soccer_tickers.json"
    STRATEGY_NAME = "sports_underdog_v37_soccer"
    REGION_OVERRIDE = "eu"  # arbitrary — soccer eu/us consensus aligned

    # Soccer leagues only — block all non-soccer sports.
    # Deny-list approach so future-added leagues (e.g., Liga MX) auto-include.
    SPORT_BLOCKLIST = frozenset({"NBA", "MLB", "NHL", "NPB", "KBO"})

    # Override v31's NBA/NHL playoff blocklist — soccer doesn't have playoff
    # phase the same way; v31's blocklist is irrelevant here.
    PLAYOFF_BLOCKLIST = frozenset()

    # Pre-game only with 24h cap. Same rationale as v36_nhl_playoff:
    # observer baseline measured on T-1h to T-24h FIRST obs.
    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 1440.0

    # Price band: 20-29c (cap of 0.29 means 0.30 is blocked). 30-35c TEAM
    # bucket showed ROI -60.9% n=7 in observer replay; keep clear of it.
    MIN_ENTRY_PRICE = 0.20
    MAX_ENTRY_PRICE = 0.29

    # Depth gate. Peak stake at price 0.29 × size 4 = $1.16, so $20 keeps
    # ~17x cushion — generous for tradeable soccer book sizes (per
    # sport_promotion_thresholds memory: top-of-book p50 >= $20 for soccer).
    MIN_DEPTH_USDC = 20.0

    def _get_trade_size(self, signal: dict) -> float:
        # Small starter — paper-twin only at launch. Matches v36_NHL launch
        # size pattern (n=35 borderline → start at 4 contracts).
        return min(4.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on TEAM side priced 20-29c with payoff >= 2x.

        Skips TIE outcomes — soccer 3-way markets have YES/NO/TIE tickers,
        and the TIE side is identified by ticker suffix '-TIE'. Observer
        analysis showed TIE 25-30c is a consistent bleed (-20% ROI).

        Does NOT use fair_prob (consensus) — 5-day cross-league data shows
        eu/us consensus aligned, and the alpha source is the raw price band
        rather than consensus disagreement.
        """
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # In soccer 3-way markets, the TIE outcome is its own ticker ending
        # with '-TIE'. The team tickers end with the team abbreviation.
        # When a TIE ticker is being evaluated:
        #   - YES = "draw happens" (TIE outcome) → skip
        #   - NO  = "draw doesn't happen" (some team wins) → also skip
        #     (we want to bet on a SPECIFIC team, not "anything but draw")
        if ticker.endswith("-TIE"):
            return None

        best_signal = None
        best_payoff = 0.0

        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (self.MIN_ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
                continue
            fee_pct = min(fee, price, 1 - price) / price
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 2.0 and payoff > best_payoff:
                best_signal = {
                    "side": side,
                    "price": price,
                    "edge": payoff,
                    "strategy": "v37_soccer",
                }
                best_payoff = payoff

        return best_signal
