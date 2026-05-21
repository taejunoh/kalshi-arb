"""Sports Soccer Weekend V1 — long-pre soccer weekend-only with two patterns.

Built 2026-05-21 from grid-search discovery (sports_strategy_discovery.py +
sports_strategy_subslice.py) on 6.4M obs / 4,004 settled tickers.

Two patterns share the same wrapper because they both target soccer
long-pre weekend games — different price bands, opposite sides:

  Pattern A (EPL/LAL underdog):
    YES side, price 0.25-0.30, |kalshi-eu_consensus| < 3pp
    EPL Sat/Sun mts>=720: discovery n=10, +87% ROI / d3 -1.2% — robust band
    Sub-cell depth 50-500 +89.8% / d3 +34.7% (sweet spot)

  Pattern B (MLS/LAL/BUN fade-favorite):
    NO side, price 0.40-0.50, |kalshi-eu_consensus| < 6pp
    MLS Sun mts>=720: discovery n=14, +40.7% / d3 +22.9%
    Sub-cell gap 3-6pp: n=7, +70.5% / d3 +46.1% (carries alpha)

Why weekend-only:
  MLS pre 0.20-0.25 mts=180-720: Wed -20% (n=10), Sat +78.7% (n=21, d3 +33.9%),
  Sun +128% (n=6). Midweek soccer is noise; weekend day games carry alpha.

Sister to sports_soccer_underdog_v1 (which targets MLS 0.20-0.24 with
mts<=24h cap). This strategy fills the *long-pre + wider band* gaps soccer_v1
explicitly excludes. No band overlap.

Paper-only at launch. Promotion gate: each pattern n_games >= 25 AND
ROI >= +20% AND drop-top-3 > 0 by ~6/15.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_soccer_weekend_v1")


class SportsSoccerWeekendV1Strategy(SportsUnderdogV31Strategy):
    """Weekend-only soccer long-pre (>=12h) with two patterns: underdog YES
    0.25-0.30 + fade-favorite NO 0.40-0.50, both gated on consensus alignment."""

    TRADED_TICKERS_FILE = "data/sports_soccer_weekend_v1_tickers.json"
    STRATEGY_NAME = "sports_soccer_weekend_v1"
    BADGE_LABEL = "WKD"
    BADGE_COLOR = "#a78bfa"
    REGION_OVERRIDE = "eu"

    # Soccer only — block all non-soccer. Note this lets EPL through (unlike
    # sports_soccer_underdog_v1 which blocks EPL); the EPL 0.25-0.30 long-pre
    # band is exactly the alpha cell this strategy targets.
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO"})

    # Parent v31's PLAYOFF_BLOCKLIST is NBA/NHL — irrelevant to soccer.
    PLAYOFF_BLOCKLIST = frozenset()

    # Override soccer_underdog_v1 / v31 SPORT_BLOCKLIST/OBS-only — let all
    # soccer leagues through this strategy.
    OBSERVATION_ONLY_SPORTS = frozenset()

    # Long pre-game only — alpha lives at mts >= 720 (12h+).
    PRE_GAME_ONLY = True
    MIN_MTS_AT_ENTRY = 720.0   # >=12h before kickoff
    MAX_MTS_AT_ENTRY = None    # no upper cap — discovery showed pre_720+ best

    # Wide price band (covers both patterns; per-side check inside evaluate).
    MIN_ENTRY_PRICE = 0.25
    MAX_ENTRY_PRICE = 0.50

    # Parent's DEPTH_DEAD_ZONE was MLB-specific. Soccer subslice showed sweet
    # spot at 50-500 depth (EPL +89.8% d3 +34.7%). Allow thinner-than-soccer_v1
    # but require minimum.
    MIN_DEPTH_USDC = 5.0
    MAX_DEPTH_USDC = 10000.0
    DEPTH_DEAD_ZONE = None

    # Pair-fire dedup (don't fire on both team-tickers of same game across cycles).
    PAIR_FIRE_DEDUP = True

    # Consensus-gap thresholds (in percentage points).
    # Pattern A (underdog YES): require |gap| < 3pp (tight alignment).
    # Pattern B (fade-fav NO):  require |gap| <= 6pp (broader window — 3-6pp
    #   sub-cell was the alpha carrier in MLS pre data).
    PATTERN_A_GAP_MAX_PP = 3.0
    PATTERN_B_GAP_MAX_PP = 6.0

    def _get_trade_size(self, signal: dict) -> float:
        # Paper-only at launch — size 4. Small relative to soccer_underdog_v1's
        # tier sizing (cap 36 at depth tier5) because:
        # (a) discovery sample n=10-14 per pattern is small, may not generalize.
        # (b) live phase market depth at >=12h pre-game is thinner than 24h pre.
        # Promotion-gate sizes 2x on validation.
        return min(4.0, self._order_manager.bankroll * 0.005)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on weekend (Sat/Sun UTC) with one of two patterns.

        TIE tickers are skipped (soccer 3-way market). Day-of-week computed
        on entry timestamp — UTC weekend covers ~Fri 18:00 ET → Sun 18:00 ET
        which catches most Sat/Sun kickoffs in EU + MLS / LAL / BUN.

        Pattern A: YES 0.25-0.30 with |kalshi_yes - fair_prob| < 3pp
        Pattern B: NO  0.40-0.50 with |(1 - kalshi_no) - fair_prob| < 6pp

        If both qualify, picks the one with higher implied payoff (1/price - 1).
        """
        if ticker.endswith("-TIE"):
            return None

        # UTC weekend check. Saturday=5, Sunday=6.
        dow = datetime.now(timezone.utc).weekday()
        if dow not in (5, 6):
            return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT
        candidates = []

        # Pattern A — underdog YES 0.25-0.30, tight consensus alignment.
        # Payoff floor 2.5 (matches Kalshi fee-adjusted breakeven at p=0.30).
        if 0.25 <= kalshi_yes <= 0.30 and fair_prob is not None:
            gap_pp = abs((kalshi_yes - fair_prob) * 100.0)
            if gap_pp < self.PATTERN_A_GAP_MAX_PP:
                fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes
                cost = kalshi_yes * (1 + fee_pct)
                payoff = 1.0 / cost
                if payoff >= 2.5:
                    candidates.append({
                        "side": "yes", "price": kalshi_yes,
                        "edge": payoff, "pattern": "A_underdog",
                        "gap_pp": gap_pp, "ticker": ticker,
                        "strategy": "soccer_weekend_v1",
                    })

        # Pattern B — fade-favorite NO 0.40-0.50, broader consensus alignment.
        # No raw-payoff floor — discovery alpha lives at WR ≈ 58% on 0.40-0.50,
        # where fee-adjusted payoff is naturally ~1.7-2.1. The consensus-gap
        # filter is the alpha gate; price-band gate is the universe.
        if 0.40 <= kalshi_no <= 0.50 and fair_prob is not None:
            implied_yes_from_no = 1.0 - kalshi_no
            gap_pp = abs((implied_yes_from_no - fair_prob) * 100.0)
            if gap_pp < self.PATTERN_B_GAP_MAX_PP:
                fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
                cost = kalshi_no * (1 + fee_pct)
                payoff = 1.0 / cost
                candidates.append({
                    "side": "no", "price": kalshi_no,
                    "edge": payoff, "pattern": "B_fade_fav",
                    "gap_pp": gap_pp, "ticker": ticker,
                    "strategy": "soccer_weekend_v1",
                })

        if not candidates:
            return None
        # If both qualify, pick higher payoff.
        return max(candidates, key=lambda c: c["edge"])
