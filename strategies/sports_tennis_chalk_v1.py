"""Tennis pre-game chalk-buy (paper-only Phase 0/1).

Built 2026-05-26 after observer-based counterfactual revealed a structural
favorite-longshot bias in ATP/WTA pre-game markets:

  v31 "cheap-underdog" buy on ATP/WTA = strongly -EV
  → MIRROR (buy the FAVORITE side instead) = +EV

Observer backtest (35d, pre-game, has_pinnacle, MAX_PIN_GAP≤0.05):
  ATP longshot MIRROR: n=42, WR 95.2%, EV +$0.053/$1 contract
  WTA midshot MIRROR:  n=79, WR 84.8%, EV +$0.039/$1 contract
  Combined n=121, blended ~+$0.044/$1.

THESIS: In pre-game tennis (ATP/WTA), heavy favorites win MORE often than
implied probability suggests — opposite of the basketball/baseball
"underdog upset" pattern that v31 family exploits. When Kalshi prices a
side at 0.65-0.95 AND Pinnacle agrees (pin-agreement gate), buying the
favorite captures the structural mispricing.

This is the PRE-GAME complement to sports_tennis_live_fade_v1 (which
exploits live Kalshi-vs-stale-Pinnacle gaps ≥5pp at ya≥0.85). Different
mechanism, both target tennis favorite mispricing.

CAVEATS:
  - Edge per trade is small (~4% of position) — slippage / TTL sensitivity
    high. Paper-only at launch.
  - n=121 over 35d ≈ 3.5 fires/day; at $4 paper size that's ~$15/day
    notional. Real $/week target: $5-15 net.
  - Possible regime-dependence: if tennis surface/season changes shift
    favorite-longshot bias, edge could compress to 0.

Risk profile: small edge, moderately high WR (85-95%) → variance is the
risk. One big loss can wipe a week of gains.

PROMOTION GATE (paper→LIVE):
  - Forward 14d WR ≥ 80% AND n ≥ 30
  - Forward 14d net positive
  - At least one ATP and WTA tournament cycle observed
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_chalk_v1")


class SportsTennisChalkV1Strategy(SportsTraderStrategy):
    """BUY the favorite side in pre-game ATP/WTA when Pinnacle agrees.

    Targets the inverse of v31 cheap-underdog bias which is structurally
    -EV in tennis. Paper-only at launch.
    """

    TRADED_TICKERS_FILE = "data/sports_tennis_chalk_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_chalk_v1"
    BADGE_LABEL = "TPC"
    BADGE_COLOR = "#22c55e"

    # Tennis-specific opt-in (matches existing tennis_*_v1 strategies).
    INDIVIDUAL_SPORTS_OPT_IN = True
    # Override parent OBSERVATION_ONLY_SPORTS — parent treats ATP/WTA as
    # observation-only (no trading). We're explicitly trading tennis, so
    # subtract ATP+WTA. Without this override, evaluate() is never called.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )
    # Block team sports — tennis-only.
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC"})
    # No playoff carve-out — tennis has tournaments not playoffs.
    PLAYOFF_BLOCKLIST = frozenset()
    # Pre-game only.
    PRE_GAME_ONLY = True

    # Favorite-buy price band. Backtest +EV centred at ya 0.65-0.85.
    # Below 0.55: signal too weak; above 0.95: dominated by tennis_live_fade
    # in live phase, pre-game thin cohort.
    MIN_FAVORITE_PRICE = 0.55
    MAX_FAVORITE_PRICE = 0.95

    # Require Pinnacle agreement on which side is favored. If Pinnacle's
    # yes_prob is far from Kalshi's implied, the signal is too noisy —
    # the strategy needs both books to align on "this side is favored".
    MAX_PIN_GAP = 0.05

    # Pinnacle minimum confidence on the favored side. <0.60 means even
    # Pinnacle is uncertain which side is favored; skip.
    MIN_PINNACLE_FAVORITE_PROB = 0.60

    # Max Pinnacle staleness (s). Match other v31/live-fade strategies.
    MAX_PIN_AGE_S = 600

    # Small paper size — edge is thin. Scale to $20 only after forward
    # validates n≥30 with WR≥80% AND net positive.
    BASE_SIZE_USDC = 4.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire when YES or NO is priced as moderate-to-heavy favorite AND
        Pinnacle agrees on which side is favored.

        Pick the FAVORITE side (higher price). Edge comes from structural
        tennis favorite-longshot bias; favorites win more often than the
        market implies in pre-game ATP/WTA.
        """
        pin = self._last_pinnacle_yes_prob
        pin_age = self._last_pinnacle_age_s

        # Required: fresh Pinnacle data
        if pin is None or pin_age is None or pin_age > self.MAX_PIN_AGE_S:
            return None

        # Determine which side is favored by Kalshi
        if kalshi_yes is None or kalshi_no is None:
            return None

        if kalshi_yes >= kalshi_no:
            fav_side, fav_price, pin_fav = "yes", kalshi_yes, pin
        else:
            fav_side, fav_price, pin_fav = "no", kalshi_no, 1.0 - pin

        # Favorite price must be in our band
        if not (self.MIN_FAVORITE_PRICE <= fav_price <= self.MAX_FAVORITE_PRICE):
            return None

        # Pinnacle must also see this side as favored (confidence ≥ MIN)
        if pin_fav < self.MIN_PINNACLE_FAVORITE_PROB:
            return None

        # Pinnacle must agree with Kalshi within MAX_PIN_GAP
        gap = abs(fav_price - pin_fav)
        if gap > self.MAX_PIN_GAP:
            return None

        return {"side": fav_side, "price": fav_price,
                "edge": pin_fav - fav_price, "strategy": "tennis_chalk",
                "ticker": ticker}
