"""Tennis live-phase fade-Pinnacle (paper-only Phase 0/1).

Built 2026-05-25 after grid search of 14d odds_observations × ticker_settlements
revealed a 100% WR pattern in live-phase tennis when:

  - Kalshi yes_ask >= 0.85 (heavy favorite, near-decided match)
  - |kalshi_yes_ask - pinnacle_yes_prob| >= 0.05
  - BUY the side Kalshi prices as favorite (i.e., fade Pinnacle)

Grid sim 14d (gap >= 0.05, side ≥0.85):
  ATP yes_fav: n=59, WR 100%, +$3.5 ($0.06/trade)
  ATP no_fav:  n=57, WR 100%, +$5.2 ($0.09/trade)
  WTA yes_fav: n=49, WR 100%, +$1.5 ($0.03/trade)
  WTA no_fav:  n=44, WR 100%, +$1.8 ($0.04/trade)
  Total: n=209, +$12 / 14d

THESIS: in live tennis, Kalshi prices update on game state faster than
Pinnacle propagates new odds. When Kalshi shows ≥0.85 + Pinnacle still
disagrees by ≥5pp, the game is near-decided per Kalshi. Pinnacle data is
"stale" relative to current set/game score.

CAVEAT: simulation used last pre-settle observation, which has selection
bias toward near-decided states. Real trader fires on earlier obs where
state may be less decided. **Paper-only at launch — measure forward WR
vs sim WR. Promote only if forward WR ≥ 90% at n ≥ 30.**

Risk profile: tiny edge per trade (~$0.05 at $4 size), high WR → variance
risk one big loss could erase weeks of gains. Caps:
  size $4 (smallest tier; if forward validates, scale to $20)
  pin_age <= 600s (matches sim filter)
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_live_fade_v1")


class SportsTennisLiveFadeV1Strategy(SportsTraderStrategy):
    """BUY the Kalshi heavy-favorite side when Pinnacle disagrees by ≥5pp.

    Live phase only. Tennis-only via INDIVIDUAL_SPORTS_OPT_IN.
    """

    TRADED_TICKERS_FILE = "data/sports_tennis_live_fade_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_live_fade_v1"
    BADGE_LABEL = "TLF"
    BADGE_COLOR = "#f97316"

    # Tennis-specific opt-in (matches existing tennis_*_v1 strategies)
    INDIVIDUAL_SPORTS_OPT_IN = True
    # Override parent OBSERVATION_ONLY_SPORTS — parent treats ATP/WTA as
    # observation-only (no trading), so scan_and_execute silently `continue`s
    # every tennis ticker before evaluate() is ever called. We're explicitly
    # trading tennis, so subtract ATP+WTA. Without this override the strategy
    # never fires (matches tennis_chalk_v1/pinnacle_fade_v1/underdog_v1).
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )
    # Block team sports — we're tennis-specific
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC"})

    # Live phase strategy — explicit override of default PRE_GAME_ONLY=True
    # (set on most v3 family). This trader needs in-play observations.
    PRE_GAME_ONLY = False

    # 2026-07-02: per-event cascade cap (prerequisite for 6% sizing). Real
    # fills showed mirror-leg double-fire: 7/01 MERMED-MER (yes@0.82) AND
    # MERMED-MED (no@0.85) = 2× correlated exposure on ONE match — a single
    # comeback loses both legs at ~0.9× prices. game_key dedup caps fires
    # at 1 per match; cascade-cap > entry-cap on clustered wipes.
    PAIR_FIRE_DEDUP = True

    # Minimum signed gap between Kalshi favorite-price and Pinnacle implied.
    # 0.05 = grid-sim threshold where 100% WR cohort starts. Tighter (0.10)
    # would cut samples by ~50% but preserve quality. Start loose at 0.05.
    MIN_PIN_GAP = 0.05

    # Minimum Kalshi side-price to consider "heavy favorite". 0.85 matches
    # the grid-sim cohort; below this the WR drops sharply.
    MIN_FAVORITE_PRICE = 0.85

    # Max Pinnacle staleness — match grid-sim filter. 600s aligns with how
    # the polymarket arc handles freshness for sharp-book data.
    MAX_PIN_AGE_S = 600

    # 2026-07-01: bankroll fraction 2%→4% and $4 flat cap removed (user call,
    # paper n=156 WR84.6%). 2026-07-02: 4%→6% (user call) paired with
    # PAIR_FIRE_DEDUP above. 2026-07-02 (later): 6%→4% ROLLBACK (user call) —
    # the paper ledger justifying the size-ups was found inflated by
    # fair-price instant-fill bookkeeping + 4 mirror orientation-flipped rows
    # (+$181.5 of +$192.8/30d); honest ask-fill replay puts the rule below BE
    # (see memory tennis_live_fade_paper_pnl_artifact_2026-07-02). Size floats
    # with real free balance; global MAX_SINGLE_TRADE_PCT / MAX_DAILY_LOSS
    # guards still apply upstream. Next re-eval at n>=50 real settled:
    # 2-prop z consistency vs paper-twin (band-matched) -> pool -> pooled
    # Wilson-LB vs BE (NOT live-subset Wilson-LB > BE — unpassable at this
    # price band, see feedback_oos_wilson_lb_over_conservative).
    def _get_trade_size(self, signal: dict) -> float:
        return self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.04

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire when one side is heavy favorite AND Pinnacle disagrees enough.

        BUY the favorite side (fade Pinnacle's "less likely" prediction). Sim
        showed Pinnacle is systematically slow-to-update in live tennis, so
        Kalshi's current price reflects game state better than Pinnacle's
        sticky pre-game prob.
        """
        pin = self._last_pinnacle_yes_prob
        pin_age = self._last_pinnacle_age_s

        # Required: fresh Pinnacle data
        if pin is None or pin_age is None or pin_age > self.MAX_PIN_AGE_S:
            return None

        # YES side is heavy favorite (kalshi_yes >= 0.85), Pinnacle disagrees
        if kalshi_yes >= self.MIN_FAVORITE_PRICE:
            gap = kalshi_yes - pin
            if gap >= self.MIN_PIN_GAP:
                return {"side": "yes", "price": kalshi_yes,
                        "edge": gap, "strategy": "tennis_live_fade",
                        "ticker": ticker}

        # NO side is heavy favorite (kalshi_no >= 0.85), Pinnacle disagrees
        if kalshi_no >= self.MIN_FAVORITE_PRICE:
            pin_no = 1.0 - pin
            gap = kalshi_no - pin_no
            if gap >= self.MIN_PIN_GAP:
                return {"side": "no", "price": kalshi_no,
                        "edge": gap, "strategy": "tennis_live_fade",
                        "ticker": ticker}

        return None
