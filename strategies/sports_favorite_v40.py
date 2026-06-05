"""Sports Favorite V4.0 — slight-favorite mispricing (MLB + NPB pre-game).

First non-underdog/non-longshot/non-midshot/non-draw strategy. Bets the
consensus FAVORITE side at prices where book underprices slight edges.

Observer scan 2026-05-12 (last_pre snapshot × ticker_settlements):

  Sport × band   n    WR     ROI   verdict
  MLB 0.50-0.55  65   58%    +8%   ★ confirmed
  NPB 0.55-0.65  28   ~60%   +13%  ★ added for breadth
  KBO 0.55-0.65  18   ~50%   +1%   excluded (break-even)
  MLB > 0.55     ~190 ~       -2% to -15%  excluded (priced fair-to-high)
  Other sports     small n / negative / playoff-only

Mechanism (hypothesis):
  - Baseball-style sports → ~50/50 games dominate
  - Books price slight favorites at "fair" or below to attract underdog action
  - True WR exceeds priced WR by 3-8pp → positive EV at 50-65c

Sister/inverse to v31 underdog (which fades consensus favorite when YES
side is at 30-39c). v40 buys when YES side IS the slight favorite at
50-65c. Same universe, opposite end of the price spectrum, with the
underdog drain band (0.39-0.50) skipped because v31 already handles
those near-coinflip mispricings on the other side.

Drop-top robustness check (MLB 0.50-0.55, n=92 as of 5/12):
  drop-top-0: +4.5%
  drop-top-3: +1.8%
  drop-top-5: -0.2%  ← break-even after dropping top 5
  → fragile. Alpha concentrated in 5-10 lucky winners.
  → launch paper-only at size 4 until n=120+ and drop-5 > 0.

Per-sport price gates (sport-specific because mispricing band differs):
  MLB:  YES @ 0.50-0.55 + eu_yes_prob > 0.50
  NPB:  YES @ 0.55-0.65 + eu_yes_prob > 0.55

Both sides (YES/NO) checked per ticker — fire NO if the NO side is
the consensus favorite at its sport's band.

PAPER-ONLY at launch. Not in LIVE_STRATEGIES env. Promotion gate:
  n_games >= 120 + ROI >= +5% + drop-top-5 > 0 by ~2026-06-09 (4 weeks).
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_favorite_v40")


class SportsFavoriteV40Strategy(SportsTraderStrategy):
    """Slight-favorite YES/NO bets on MLB 0.50-0.55 and NPB 0.55-0.65."""

    TRADED_TICKERS_FILE = "data/sports_favorite_v40_tickers.json"
    STRATEGY_NAME = "sports_favorite_v40"
    BADGE_LABEL = "V4.0"
    BADGE_COLOR = "#c084fc"
    REGION_OVERRIDE = "eu"  # observer used eu_yes_prob; matches data

    # Cell matrix — MLB pre + NPB pre only.
    ALLOWED_CELLS = frozenset({("MLB", "pre"), ("NPB", "pre")})

    # Override parent OBSERVATION_ONLY_SPORTS so NPB tickers reach evaluate().
    # Pattern from v37_soccer (which had the same gate-bypass need for soccer).
    OBSERVATION_ONLY_SPORTS = frozenset()

    # ALLOWED_CELLS subsumes these; explicit override prevents reactivation.
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()

    # Depth gate $30 — at size 4 (stake $4), keeps ~7.5× cushion at peak
    # cost (0.65 NPB × 4 / 0.65 ≈ 6 contracts × 0.65 = $4 → $30/$4 = 7.5×).
    MIN_DEPTH_USDC = 30.0

    # Per-sport price band — sport-specific because MLB and NPB have
    # different mispricing zones.
    MLB_BAND = (0.50, 0.55)
    MLB_FAIR_MIN = 0.50
    NPB_BAND = (0.55, 0.65)
    NPB_FAIR_MIN = 0.55

    def _get_trade_size(self, signal: dict) -> float:
        # Launch size 4 (paper-only). Conservative because drop-top-5
        # break-even on MLB cohort. Scale to 8 only after n=120 + drop-5 > 0.
        return min(4.0, self._order_manager.bankroll * 0.02)

    def _band_for_ticker(self, ticker: str) -> Optional[tuple]:
        if "MLBGAME" in ticker:
            return self.MLB_BAND, self.MLB_FAIR_MIN
        if "NPBGAME" in ticker:
            return self.NPB_BAND, self.NPB_FAIR_MIN
        return None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on YES (consensus favorite YES team) or NO (consensus
        favorite NO team) when price is in sport-specific slight-favorite
        band.

        Pair-fire dedup (2026-05-12): Kalshi issues 2 paired tickers per
        game (one per team perspective). At slight-favorite prices both
        tickers' in-band side often fires on the SAME outcome — e.g.,
        DETNYM-NYM yes @ 0.53 ("NYM win") AND DETNYM-DET no @ 0.53 ("DET
        loses" = "NYM win"). Without this guard, every game gets 2x
        effective size on the same bet. Original docstring claim that
        "pair-fire avoided naturally" was wrong — that only holds within
        one ticker (yes+no sum to 1), not across paired tickers."""
        # Pair-fire dedup at game level — skip if any sibling ticker
        # from same game already fired this session/window.
        game_key = ticker.rsplit("-", 1)[0]
        for traded in self._traded_tickers:
            if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                return None

        cfg = self._band_for_ticker(ticker)
        if cfg is None:
            return None
        (band_lo, band_hi), fair_min = cfg

        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # YES side fire: YES team is consensus favorite, kalshi_yes in band
        if fair_prob > fair_min and band_lo <= kalshi_yes <= band_hi:
            fee_pct = self._gate_fee_pct(kalshi_yes)
            payoff = 1.0 / (kalshi_yes * (1 + fee_pct))
            return {
                "side": "yes", "price": kalshi_yes,
                "edge": payoff, "strategy": "favorite_v40",
            }

        # NO side fire: NO team is consensus favorite (fair < 1-fair_min),
        # kalshi_no in band
        if fair_prob < (1 - fair_min) and band_lo <= kalshi_no <= band_hi:
            fee_pct = self._gate_fee_pct(kalshi_no)
            payoff = 1.0 / (kalshi_no * (1 + fee_pct))
            return {
                "side": "no", "price": kalshi_no,
                "edge": payoff, "strategy": "favorite_v40",
            }

        return None
