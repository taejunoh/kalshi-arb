"""Sports Soccer Draw V1 (renamed 2026-05-13 from sports_draw_v38_soccer)
— soccer 3-way Draw side, EPL+LAL+MLS 0.25-0.30. Renamed 2026-05-13.

Built 2026-05-11 from observer analysis of 71 settled TIE tickers across
MLS/EPL/LAL/UCL/BUN since 2026-05-02. Sister strategy to v37_soccer
(which trades TEAM side); v38 trades the DRAW side.

Per-league × band TIE ROI (game-level, observer last-pre snapshot,
YES bet = "draw happens"):

  League  0.20-0.25            0.25-0.30
  EPL     n=5  +70% drop-1 +4%   n=6  +72% drop-1 +37%  ★
  LAL     n=3 -100%               n=7  +51% drop-1 +18%  ★
  MLS     n=6  +39% drop-1 -17%  n=19 +13% drop-1  -1%  (season-long carrier)
  UCL     n=1 -100%               (n=0)
  BUN     n=5 -100%               n=5  -30%

Combined EPL+LAL+MLS 0.25-0.30: n=32, ROI ~+32% (weighted), drop-1 ~+10-15%.
Combined non-BUN 0.20-0.30: n=47 ROI +27%, drop-top-3 +9%, drop-top-5 -4%.

Cell chosen: EPL+LAL+MLS × 0.25-0.30 (MLS re-added 2026-05-11 after
initial EPL+LAL only launch — see below).

- BUN excluded: n=12 ROI -67% across both bands, structural Draw bleed
- UCL excluded: n=1 with loss; UCL knockouts may have extra time/PKs that
  resolve "draws," structurally different from group-stage
- 0.20-0.25 excluded: LAL n=3 catastrophic, MLS drop-1 -17%, fragile

MLS re-added rationale (intra-session pivot, before any v38 fire):
- EPL season ends 5/24, LaLiga ends 5/25, UCL Final 5/31
- EPL+LAL-only gate gives v38 ~2 weeks of fires then 11 weeks dormant
  until 2026-08 season restart — too short to reach promotion n=25
- MLS season runs March-November → carries v38 through summer
- MLS 0.25-0.30 +13% on n=19 IS positive even if drop-1 -0.8% break-even
  — provides volume to test the broader Draw mispricing thesis
- Weighted blend on n=32 (EPL+LAL+MLS): ROI ~+32%, more robust than
  cherry-picking the small-n top performers

Top-of-book ask depth p25=$1954 / median=$4445 — $15 gate trivially passes.

Promotion gate: n_games >= 25 + ROI >= +30% + drop-top-3 > 0. Soccer
season cadence ~2 EPL games/week + ~2 LAL games/week → ~16 games/4wk →
n=25 in ~6 weeks (~2026-06-22). Eval 2026-06-22.

LIVE at launch (size 8 — matches v37) per user decision 2026-05-11.
Initial size 12 was reduced to 8 after MLS re-add lowered weighted ROI
from ~+60% (EPL+LAL only) to ~+32% (with MLS). Same size as v37 ensures
per-trade edge × size profile is comparable.
"""
import logging
from typing import Optional

from strategies.sports_soccer_underdog_v1 import SportsSoccerUnderdogV1Strategy

logger = logging.getLogger("sports_soccer_draw_v1")


class SportsSoccerDrawV1Strategy(SportsSoccerUnderdogV1Strategy):
    """Soccer 3-way DRAW side, EPL+LAL+MLS, 0.25-0.30 YES band."""

    TRADED_TICKERS_FILE = "data/sports_soccer_draw_v1_tickers.json"
    STRATEGY_NAME = "sports_soccer_draw_v1"

    # Allowed leagues: EPL + LaLiga + MLS. v37 inheritance gives us
    # OBSERVATION_ONLY_SPORTS=frozenset() (override of parent gate) and
    # PLAYOFF_BLOCKLIST=frozenset(). BUN+UCL excluded based on Draw-side
    # band analysis (BUN -67% structural bleed, UCL n=1 + knockout regime).
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
        "BUN", "UCL",
    })

    # Draw band is different from TEAM band — TIE prices cluster 0.22-0.30,
    # alpha is in the upper half (0.25-0.30). 0.20-0.25 includes LAL n=3
    # all-loss catastrophe.
    MIN_ENTRY_PRICE = 0.25
    MAX_ENTRY_PRICE = 0.30

    # Same depth gate as v37 (trivially passes — TIE depth median $4400).
    MIN_DEPTH_USDC = 15.0

    def _get_trade_size(self, signal: dict) -> float:
        # Launch size 8 (matches v37). Reduced from initial 12 (2026-05-11
        # same session, pre-first-fire) after MLS re-add: weighted ROI dropped
        # from EPL+LAL-only ~+60% to combined ~+32%, so smaller size matches
        # the thinner per-trade edge. Scale up if n=25 sustains ROI >= +20%.
        return min(8.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on TIE-suffix tickers only, YES side (bet draw happens).

        Inverse of v37_soccer which skips TIE; v38 ONLY fires on TIE.
        NO side bets "draw doesn't happen" which is just "anything but draw"
        — that's a weaker fade with no obvious mispricing signal, so YES only.
        """
        if not ticker.endswith("-TIE"):
            return None

        price = kalshi_yes
        if not (self.MIN_ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
            return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT
        fee_pct = min(fee, price, 1 - price) / price
        cost = price * (1 + fee_pct)
        payoff = 1.0 / cost
        if payoff < 2.0:
            return None

        return {
            "side": "yes",
            "price": price,
            "edge": payoff,
            "strategy": "soccer_draw_v1",
        }
