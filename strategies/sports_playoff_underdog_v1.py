"""Sports Underdog V3.6 Playoff — NBA-playoff-only underdog with 35¢ floor.

Built 2026-04-30 from a sub-bucket analysis of NBA playoff underdog cells:

  Per-band observer ROI (game-level dedup, NBA pre playoff, eu region):
    0.20-0.25¢:  10 games, 20% WR,  -178% ROI  (catastrophic)
    0.25-0.30¢:  10 games, 30% WR,  -102% ROI  (bleeding)
    0.30-0.35¢:   8 games, 38% WR,    +5% ROI  (break-even)
    0.35-0.40¢:  10 games, 60% WR,  +138% ROI  ★
    0.40-0.45¢:   7 games, 71% WR,  +160% ROI  ★

  Trade-level confirmation (game-level dedup, n=9 games at 30-40¢):
    +153% ROI, 56% game-WR.

The alpha lives in the 35-45¢ underdog band. Below 35¢ the favorite wins too
reliably in NBA playoffs (deeper underdogs lose more in playoffs than regular
season — small-sample noise theory + best-of-7 means star-driven outcomes).
The 35-40¢ band is "near-coinflip underdog" where Kalshi appears to slightly
underprice the upset chance.

Paper-only. Promotion criteria: n_games ≥ 25 with ROI ≥ +30% post-fix-window.
NBA playoffs end ~2026-06-25 — so window is at most ~8 weeks of accumulation.
"""
import logging

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v36_playoff")


class SportsUnderdogV36PlayoffStrategy(SportsUnderdogV31Strategy):
    """V3.1 underdog logic restricted to NBA playoff pre-game with 35¢ floor."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v36_playoff_tickers.json"
    STRATEGY_NAME = "sports_underdog_v36_playoff"
    REGION_OVERRIDE = "eu"  # observer eu and us nearly identical (+135% vs +134%)
    ALLOWED_CELLS = frozenset({("NBA", "pre")})
    PLAYOFF_ONLY = True
    # Floor 0.35 → 0.30 (2026-05-01). Re-analysis with grown observer sample
    # showed 30-35¢ band (originally n=8 +5.5% ROI break-even) now n=12 +118%
    # eu / +98% us — was alpha, not break-even. Trades data agrees: n=7 +280%
    # eu / +279% us at game level. Both regions confirm 30¢ as the right floor.
    MIN_ENTRY_PRICE = 0.30
    # Override v31's PRE_GAME_ONLY/SPORT_BLOCKLIST since ALLOWED_CELLS subsumes.
    PRE_GAME_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    # Override v31's PLAYOFF_BLOCKLIST — v36 IS the playoff strategy.
    PLAYOFF_BLOCKLIST = frozenset()
    # Override v31's MAX_ENTRY_PRICE=0.39 cap — that cliff is MLB-reg-specific
    # (v31/v32 -25/-29% ROI). The strategy's design rationale (see docstring)
    # explicitly targets the 0.40-0.45¢ band as alpha (+160% ROI observer,
    # n=7 games). Capping at 0.39 would block exactly the band this strategy
    # was built to capture.
    MAX_ENTRY_PRICE = None
    # Live-promoted 2026-04-30. Sizing 8→16 (2x) on 2026-05-02: NBA playoff
    # window finite (~3-4 weeks left) and live n=6 / 67% WR / 57% ROI is
    # consistent with the playoff-mispricing thesis. Aggressive bump to capture
    # more of the time-limited opportunity; if next n=15 checkpoint shows
    # post-change ROI < 50% of pre-change, revert. Depth 10→20 (1.5× the
    # contract bump) keeps 1.92× cushion at top of underdog band (16 × $0.65
    # = $10.40 stake) — matches the underdog_v31 cushion ratio. The earlier
    # $15 left only 1.44× cushion which was too tight on a small-n strategy.
    MIN_DEPTH_USDC = 20.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(16.0, self._order_manager.bankroll * 0.02)
