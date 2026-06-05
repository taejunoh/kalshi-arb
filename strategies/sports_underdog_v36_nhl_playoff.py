"""Sports Underdog V3.6 NHL Playoff — NHL-playoff-only with pure price-band gate.

Built 2026-05-03 from observer analysis of NHL playoff games (n=48 settled,
last 30d). Unlike v36_playoff (NBA) which uses a 30¢+ floor with payoff-based
gate, this variant uses a pure price-band gate after observer analysis showed
NHL playoff alpha lives in two narrow bands separated by a toss-up dead zone:

  Per-bucket observer ROI (NHL playoff, last 30d settled, T-1h to T-24h,
  any side at price in band — both YES and NO bets counted independently):
    35-40¢:   n=4   WR 50%   ROI +25%   small but matches direction
    40-45¢:   n=16  WR 62%   ROI +39%   ★ slight-underdog sweet spot
    45-50¢:   n=22  WR 36%   ROI -28%   ✗ DEAD ZONE (toss-up; skip)
    50-55¢:   n=15  WR 73%   ROI +29%   ★ small-favorite signal
    55-60¢:   n=14  WR 50%   ROI -18%   ✗ favorite buy
    60-65¢:   n=14  WR 28%   ROI -57%   ✗ chalk

  Combined gate (35-45¢ + 50-55¢, dead zone excluded): n=35, ROI ~+33%.

EU and US consensus are 100% aligned on this corpus (max diff 1.2¢, 0
disagreements on n=48 favorite identification), so REGION_OVERRIDE='eu' is
arbitrary. Either region would give the same fires.

Magnitude vs NBA v36 baseline:
  NBA v36 paper baseline (35-45¢): +148% ROI on n=17 → realized live ~57%
  NHL v36 candidate baseline (this gate): +33% ROI on n=35
  Realistic realized estimate (paper-to-live regression ~0.4): +5-15% ROI.

Phase: NHL playoff only (PLAYOFF_ONLY=True; auto-resumes regular season).
Sport: NHL only (SPORT_BLOCKLIST blocks NBA/MLB).
Sample size caveat: n=35 is borderline for promotion gate (>=25). Continue
through R2/R3 to validate; +33% baseline could regress on chalk-heavy
later rounds. Live trading starts at small size 8 to limit downside while
validating.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v36_nhl_playoff")


class SportsUnderdogV36NHLPlayoffStrategy(SportsUnderdogV31Strategy):
    """NHL-playoff-only underdog variant with pure price-band gate."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v36_nhl_playoff_tickers.json"
    STRATEGY_NAME = "sports_underdog_v36_nhl_playoff"
    BADGE_LABEL = "V3.6"
    BADGE_COLOR = "#06b6d4"
    REGION_OVERRIDE = "eu"

    # NHL only — block NBA/MLB
    SPORT_BLOCKLIST = frozenset({"NBA", "MLB"})

    # Playoff only — auto-resumes when NHL returns to regular season (~Oct).
    PLAYOFF_ONLY = True
    # Override parent v31's NBA PLAYOFF_BLOCKLIST — this strategy IS the
    # playoff strategy and is NHL-only via SPORT_BLOCKLIST anyway.
    PLAYOFF_BLOCKLIST = frozenset()
    # Pre-game only — matches the data window the +33% ROI baseline was
    # measured on (T-1h to T-24h pre-game). Live-game NHL playoff fires
    # are unvalidated; revisit after pre-game alpha is confirmed.
    PRE_GAME_ONLY = True
    # Max 24h before game (1440 min) — observer baseline was measured on
    # FIRST obs in T-1h to T-24h. v31 underdog post-mortem (2026-05-03)
    # showed the 35-45c+50-55c sweet spot ROI dropped from +33% (observer)
    # to -4.5% (v31 trades) primarily because v31 fired days early at
    # mts=2000-4000 where lines hadn't converged. Capping at 24h keeps
    # entries in the price-discovery sweet spot.
    MAX_MTS_AT_ENTRY = 1440.0

    # Price band: [0.35, 0.55) excluding dead zone [0.45, 0.50).
    MIN_ENTRY_PRICE = 0.35
    MAX_ENTRY_PRICE = 0.55
    ENTRY_PRICE_DEAD_ZONE = (0.45, 0.50)

    # Depth gate: peak stake at price 0.55 × size 8 = $4.40.
    # $20 depth gives ~4.5× cushion — generous for a small-n strategy.
    MIN_DEPTH_USDC = 20.0

    def _get_trade_size(self, signal: dict) -> float:
        # Small starting size — n=35 baseline is borderline. Matches v36_NBA's
        # original size 8 launch. Validate with paper data through R2/R3
        # before any sizing increase.
        return min(8.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Pure price-band gate — fire whichever side is priced in
        [MIN_ENTRY_PRICE, MAX_ENTRY_PRICE) excluding ENTRY_PRICE_DEAD_ZONE.

        Does NOT use fair_prob (consensus) for the gate — observer analysis
        showed alpha is concentrated in raw price bands regardless of which
        side consensus identifies as favorite. EU and US agree 100% on this
        corpus, so the consensus signal would be redundant.
        """
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        dz_lo, dz_hi = self.ENTRY_PRICE_DEAD_ZONE

        def in_band(price: float) -> bool:
            if not (self.MIN_ENTRY_PRICE <= price < self.MAX_ENTRY_PRICE):
                return False
            if dz_lo <= price < dz_hi:
                return False
            return True

        # When both sides pass (rare — would need ya in band AND na in band,
        # only possible if both ~0.40-0.55 with non-trivial spread), pick the
        # higher-payoff side.
        best_signal = None
        best_payoff = 0.0
        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not in_band(price):
                continue
            fee_pct = self._gate_fee_pct(price)
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff > best_payoff:
                best_signal = {"side": side, "price": price,
                               "edge": payoff, "strategy": "v36_nhl_playoff"}
                best_payoff = payoff
        return best_signal
