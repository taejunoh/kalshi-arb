"""Sports MLS Fade-Favorite V1 — MLS reg-season fade-favorite TEAM BUY NO.

Built 2026-05-15 from cross-sport observer scan (n=1214 settled snapshots).

Signal: MLS TEAM tickers where Kalshi prices the favorite team at yes_ask
0.55-0.70 — buy NO at no_ask 0.30-0.45 to fade the favorite. In 3-way
soccer markets, the NO outcome of a team-ticker = "other team wins OR draw",
which captures both the upset and the draw probability the market
underweights at this band.

Observer ROI by sport at no_ask 0.30-0.45 BUY NO, mts~60 (5/15 scan):
  ATP   n=5   WR 60.0%  ROI +37.85%  (too small)
  EPL   n=7   WR 57.1%  ROI +29.63%  (too small, kept observer-only)
  MLS   n=14  WR 57.1%  ROI +18.57%  ← THIS STRATEGY
  NBA   n=34  WR 47.1%  ROI  +2.85%  (covered by playoff_underdog_v1)
  WNBA  n=10  WR 40.0%  ROI  -2.84%
  KBO   n=34  WR 44.1%  ROI  -9.82%
  NPB   n=36  WR 41.7%  ROI -12.42%
  MLB  n=161  WR 41.6%  ROI -13.88%
  NHL   n=24  WR 33.3%  ROI -31.91%  (playoff fade-fav structurally dead)
  LAL   n=7   WR 14.3%  ROI -71.43%  (other soccer leagues NOT eligible)

Cross-band sanity (MLS-specific):
  yes_ask 0.50-0.59 BUY_NO  n=13  WR 61.5%  ROI +16.38%
  yes_ask 0.55-0.65 BUY_NO  (subset of above, sweet spot)
  side-symmetry: same MLS sample BUY_YES at fav side = strong loss (≈-30%)
  → asymmetric signal consistent with "fade overpriced fav" thesis.

Uncovered before this strategy:
  - sports_soccer_underdog_v1: TEAM 20-29c BUY YES (dog side, different band)
  - sports_soccer_draw_v1:     TIE 25-30c BUY YES (draw outcome)
  - This strategy:             TEAM no_ask 30-45c BUY NO (fade-fav side)

Caveats / risk:
  - Small n=14 sample, all post 2026-05-02 observer launch
  - Slate concentration risk (likely few MLS gameweeks)
  - 3-way market mechanics: NO outcome combines "other team wins" +
    "draw" — analysis cannot disentangle which contributes ROI
  - EPL n=7 showed same direction (+29%) but n too small to extend
  - LaLiga showed opposite direction (-71%) at TEAM fade-fav — do NOT
    extend without per-league re-validation

Paper-twin only at launch — no LIVE_STRATEGIES env entry. Promotion gate:
  n_games >= 25 AND ROI >= +10% AND drop-top-3 > 0 by ~6/22.

Region: REGION_OVERRIDE='eu' (observer signal measured against Kalshi raw
prices; consensus region not load-bearing). Set arbitrarily.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_mls_fade_favorite_v1")


class SportsMLSFadeFavoriteV1Strategy(SportsUnderdogV31Strategy):
    """MLS TEAM markets — fade-favorite BUY NO at no_ask 0.30-0.45."""

    TRADED_TICKERS_FILE = "data/sports_mls_fade_favorite_v1_tickers.json"
    STRATEGY_NAME = "sports_mls_fade_favorite_v1"
    BADGE_LABEL = "V1"
    BADGE_COLOR = "#10b981"  # emerald
    REGION_OVERRIDE = "eu"

    # MLS pre-game only — cell-matrix gate subsumes legacy SPORT_BLOCKLIST.
    ALLOWED_CELLS = frozenset({("MLS", "pre")})

    # Override v31 NBA/NHL playoff blocklist — soccer doesn't apply.
    PLAYOFF_BLOCKLIST = frozenset()

    # OBSERVATION_ONLY_SPORTS still blocks MLS at parent default. Override
    # so MLS tickers reach evaluate(). Same pattern as sports_soccer_underdog_v1.
    OBSERVATION_ONLY_SPORTS = frozenset()

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 1440.0  # 24h pre-game cap (matches v37 baseline window)

    # Price band: no_ask in 0.30-0.45 corresponds to yes_ask 0.55-0.70 fav side.
    # Note: parent base evaluate uses kalshi_no <= 0.40 hard ceiling; our
    # custom evaluate() below replaces that with a 0.45 cap matching observer.
    MIN_ENTRY_PRICE = 0.30
    MAX_ENTRY_PRICE = 0.45

    # Depth gates — mirror soccer_underdog_v1 tiers (same league pool).
    MIN_DEPTH_USDC = 5.0
    MAX_DEPTH_USDC = 10000.0
    # Override v31 $2k-10k dead-zone — soccer data shows that band is sweet
    # spot, not dead. Same rationale as sports_soccer_underdog_v1.
    DEPTH_DEAD_ZONE = None

    def _get_trade_size(self, signal: dict) -> float:
        """Conservative Phase 0 sizing — cap 4 contracts.

        Smaller than soccer_underdog_v1 (cap 24 at sweet-spot tier) because
        observer n=14 is thin. Scale up post-promotion only.
        """
        return min(4.0, self._order_manager.bankroll * 0.01)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire BUY NO when this ticker's team is the favorite (yes_ask
        0.55-0.70) and no_ask is in our fade band (0.30-0.45).

        Does NOT use fair_prob — observer signal measured on Kalshi raw
        prices, and adding a consensus gate would reduce n by ~30%
        (Kalshi-only-overpriced cases dropped). Re-evaluate at promotion.

        Skips -TIE tickers (3-way draw outcome handled by soccer_draw_v1).
        """
        if ticker.endswith("-TIE"):
            return None

        # Favorite must be priced 0.55-0.70 on YES side (the team this
        # ticker represents). Below 0.55 the team isn't favored; above
        # 0.70 the no_ask is too thin to clear payoff>=2.0.
        if not (0.55 <= kalshi_yes <= 0.70):
            return None

        if not (self.MIN_ENTRY_PRICE <= kalshi_no <= self.MAX_ENTRY_PRICE):
            return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT
        fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
        cost = kalshi_no * (1 + fee_pct)
        payoff = 1.0 / cost
        if payoff < 2.0:
            return None

        return {
            "side": "no",
            "price": kalshi_no,
            "edge": payoff,
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }
