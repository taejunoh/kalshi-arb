"""UFC live-phase fade-Pinnacle (paper-only Phase 0/1).

Built 2026-07-02 from the honest ask-fill grid that refuted the broad
live_fade thesis (see memory tennis_live_fade_paper_pnl_artifact_2026-07-02).
UFC was the only SPORT whose live fade cohort stayed positive at honest ask
fills across every gate combination:

  UFC live, ask>=0.85, gap>=0.05: n=33 WR 100% ROI +5.8%
    (0.90 -> +2.4% n32, 0.93 -> +1.3% n30 — positive everywhere, widest
     cell kept for flow; halves 6A +0.69 (n13) / 6B +1.13 (n20))

Mechanism guess: mid-fight momentum (knockdown/submission attempt) moves
Kalshi instantly while Pinnacle's live MMA feed lags — and unlike tennis,
a near-finished fight rarely "comes back" (ref stoppage momentum).

Caveats -> reliability ★2, PAPER-ONLY:
  - n=33 all-win: Wilson-LB 89.6% ~= BE-WR at the ~0.90 avg price. Unproven.
  - Event-sparse (cards on weekends, ~30 fires/month live-phase).
  - 1-of-few positive sports after a cross-sport sweep = selection risk.
Promotion bar: n>=50 paper-settled AND pooled Wilson-LB > BE.

BOOKKEEPING (the live_fade lesson): _get_limit_price returns signal["price"]
(the observed ask). The parent default books fair-based prices that never
existed on the book for fade-style entries. DO NOT remove this override.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_ufc_live_fade_v1")


class SportsUfcLiveFadeV1Strategy(SportsTraderStrategy):
    """BUY the UFC in-play heavy favorite (ask>=0.85) when fresh Pinnacle
    disagrees by >=5pp."""

    TRADED_TICKERS_FILE = "data/sports_ufc_live_fade_v1_tickers.json"
    STRATEGY_NAME = "sports_ufc_live_fade_v1"
    BADGE_LABEL = "ULF"
    BADGE_COLOR = "#ef4444"

    # UFC tickers ride the individual-sport path (KXUFCFIGHT) — opt in and
    # remove UFC from the observation-only set (same pattern as
    # sports_ufc_chalk_fade_v1).
    INDIVIDUAL_SPORTS_OPT_IN = True
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"UFC"}
    )

    # Cell matrix: UFC live only — subsumes PRE_GAME_ONLY / SPORT_BLOCKLIST
    # (ATP/WTA flow through the individual-sport opt-in but die here).
    ALLOWED_CELLS = frozenset({("UFC", "live")})
    CELL_PRICE_BANDS = {("UFC", "live"): [(0.85, 0.99)]}

    # One fire per fight (mirror legs are one bet economically).
    PAIR_FIRE_DEDUP = True

    # 2026-07-02 review finding: default 0.05 junk-quote floor blocks
    # tight-book favorites >= ~0.96 before evaluate() (0/29 first-crossings
    # in the 30d replay — negligible today, but the band nominally reaches
    # 0.99). Floor 0.01 keeps the forward cohort == replay cohort. See
    # sports_kbo_live_fade_tight_v1 for the full mechanism.
    MIRROR_ASK_FLOOR = 0.01

    # Same thresholds the grid tested (the tennis_live_fade launch params —
    # negative for tennis at honest fills, positive for UFC).
    MIN_FAVORITE_PRICE = 0.85
    MIN_PIN_GAP = 0.05
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
        """Fire when one side is a >=0.85 favorite AND fresh Pinnacle prices
        that side >=5pp lower. BUY the favorite at its ask."""
        pin = self._last_pinnacle_yes_prob
        pin_age = self._last_pinnacle_age_s

        if pin is None or pin_age is None or pin_age > self.MAX_PIN_AGE_S:
            return None

        if kalshi_yes >= self.MIN_FAVORITE_PRICE:
            gap = kalshi_yes - pin
            if gap >= self.MIN_PIN_GAP:
                return {"side": "yes", "price": kalshi_yes,
                        "edge": gap, "strategy": "ufc_live_fade",
                        "ticker": ticker}

        if kalshi_no >= self.MIN_FAVORITE_PRICE:
            gap = kalshi_no - (1.0 - pin)
            if gap >= self.MIN_PIN_GAP:
                return {"side": "no", "price": kalshi_no,
                        "edge": gap, "strategy": "ufc_live_fade",
                        "ticker": ticker}

        return None
