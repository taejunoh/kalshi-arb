"""KBO live-phase fade-Pinnacle, TIGHT corner cell (paper-only Phase 0/1).

Built 2026-07-02 from the honest ask-fill grid that refuted the broad
live_fade thesis (see memory tennis_live_fade_paper_pnl_artifact_2026-07-02).
Across the fav {0.85,0.90,0.93} x gap {0.05,0.08,0.12} grid all cells were
<= BE EXCEPT this corner, which is monotone-supported on BOTH axes:

  KBO live, ask>=0.93, gap>=0.12: n=69 WR 98.6% ROI +2.2% (avg price 0.962)
    gap axis  (fav 0.93): 0.05 -> -1.1%, 0.08 -> -0.1%, 0.12 -> +2.2%
    fav axis  (gap 0.12): 0.85 -> -6.3%, 0.90 -> -1.7%, 0.93 -> +2.2%
    halves: 6A +0.69 (n25) / 6B +1.56 (n41) / 7A -0.82 (n3, one loss)

Corner-cell discipline: 1-of-63 survivor after a grid search = multiple-
comparison risk -> reliability ★2. PAPER-ONLY; exists to validate forward.
Promotion bar: n>=50 paper-settled AND pooled Wilson-LB > BE (~96.4% at the
0.96 avg price) — AND the >=0.90 taker wipe-zone concern
(feedback_slip_fee_wipe_mechanism) must be re-examined before ANY real-money
promotion: at 0.96 one loss erases ~27 wins.

BOOKKEEPING (the live_fade lesson): _get_limit_price returns signal["price"]
(the observed ask). The parent default books fair-based prices that never
existed on the book for fade-style entries. DO NOT remove this override.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_kbo_live_fade_tight_v1")


class SportsKboLiveFadeTightV1Strategy(SportsTraderStrategy):
    """BUY the KBO in-play heavy favorite (ask>=0.93) when fresh Pinnacle
    disagrees by >=12pp."""

    TRADED_TICKERS_FILE = "data/sports_kbo_live_fade_tight_v1_tickers.json"
    STRATEGY_NAME = "sports_kbo_live_fade_tight_v1"
    BADGE_LABEL = "KLF"
    BADGE_COLOR = "#a855f7"

    # KBO is observation-only by default — opt it back in for this strategy.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"KBO"}
    )

    # Cell matrix: KBO live only — subsumes PRE_GAME_ONLY / SPORT_BLOCKLIST.
    # (Tennis/UFC series never reach here: INDIVIDUAL_SPORTS_OPT_IN stays
    # False, so the individual-sport gate drops them pre-cell.)
    ALLOWED_CELLS = frozenset({("KBO", "live")})
    CELL_PRICE_BANDS = {("KBO", "live"): [(0.93, 0.99)]}

    # One fire per game (mirror legs are one bet economically).
    PAIR_FIRE_DEDUP = True

    # 2026-07-02 review finding: the default 0.05 junk-quote floor blocks
    # tight-book heavy favorites (na < 0.05 ⟺ yes_bid >= 0.96) BEFORE
    # evaluate() — 18.4% of this cell's signal rows / ~8% of first-crossing
    # entries in the 30d replay, concentrated in the monotone-BEST deepest
    # sub-band. Observer records those rows, so the replay included them.
    # Floor 0.01 keeps the dust guard while making the forward paper cohort
    # reproduce the replay cohort.
    MIRROR_ASK_FLOOR = 0.01

    # Grid-tested corner: BOTH thresholds are load-bearing (each axis is
    # monotone into this corner and negative outside it). Don't loosen.
    MIN_FAVORITE_PRICE = 0.93
    MIN_PIN_GAP = 0.12
    MAX_PIN_AGE_S = 600

    def _get_trade_size(self, signal: dict) -> float:
        # Paper-only launch tier — $4 cap convention for Phase 0/1 paper.
        return min(4.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.01)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        # Honest booking at the ask the signal saw — see module docstring.
        return signal["price"]

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire when one side is a >=0.93 favorite AND fresh Pinnacle prices
        that side >=12pp lower. BUY the favorite at its ask."""
        pin = self._last_pinnacle_yes_prob
        pin_age = self._last_pinnacle_age_s

        if pin is None or pin_age is None or pin_age > self.MAX_PIN_AGE_S:
            return None

        if kalshi_yes >= self.MIN_FAVORITE_PRICE:
            gap = kalshi_yes - pin
            if gap >= self.MIN_PIN_GAP:
                return {"side": "yes", "price": kalshi_yes,
                        "edge": gap, "strategy": "kbo_live_fade_tight",
                        "ticker": ticker}

        if kalshi_no >= self.MIN_FAVORITE_PRICE:
            gap = kalshi_no - (1.0 - pin)
            if gap >= self.MIN_PIN_GAP:
                return {"side": "no", "price": kalshi_no,
                        "edge": gap, "strategy": "kbo_live_fade_tight",
                        "ticker": ticker}

        return None
