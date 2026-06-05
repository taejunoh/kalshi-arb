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
    # 2026-05-31: tightened 0.35 -> 0.29 (<= check + cent prices = strict <0.30).
    # Fine by-band drill + adversarial verify: the bleed is the 0.30-0.35 band
    # (n=54, WR 14.8%, -$121.76; price==0.30 alone n=19/-$38.74), while 0.25-0.29
    # is +EV (n=46, WR 43.5% vs 28.4% breakeven = +15pp margin, profit spread
    # across 11 days, ATP +$50.62 & WTA +$46.89 both +EV, survived leave-best-day-
    # out / variance / mirror refutations; NO mirror inflation here - 117 distinct
    # games). Matches the longshot/midshot cheap-underdog dead-zone pattern.
    # CAVEAT: single Roland Garros clay regime, n=46<50 -> PAPER ONLY. Re-validate
    # on grass (Wimbledon ~6/30: grass n>=50, WR margin >=+8pp, multi-day) before
    # any LIVE step. Do NOT re-add 0.35-0.40 (+EV but n=17 noise = barbell overfit);
    # 0.30-inclusive is worse (drags in the price==0.30 loser cohort).
    MAX_ENTRY_PRICE = 0.29

    # TU1 signal is price-band only — Pinnacle is used as a sanity veto, not
    # as a freshness-driven signal source. Unlike A1/C (which require fresh
    # Pinnacle ticks to fire), TU1 should accept any non-null Pinnacle value
    # as veto input. 2026-05-19 first-fire check showed 35 candidates and
    # 0 fires because inherited MAX_PINNACLE_AGE_S=600 rejected everything
    # after the Pinnacle hash stopped ticking (~30 min post-deploy). Removed.

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        # Pinnacle must exist for the veto check, but freshness is NOT
        # required — see class comment above.
        pin = self._last_pinnacle_yes_prob
        if pin is None:
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
        return min(4.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)
