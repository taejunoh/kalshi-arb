"""Sports Consensus-Aligned Live V1 — cross-sport live-phase mid-band with
EU-consensus alignment as the entry gate.

Built 2026-05-21 from sub-slice discovery (sports_strategy_subslice.py).
Cross-sport mechanism (NOT seen in current portfolio):

  When Kalshi ask price ≈ EU consensus (|gap| < 3pp) on a 20-30c team
  during live play, that side wins much more than its implied probability:

  WNBA live YES 0.25-0.30 gap<3pp:  n=8  WR 88%  ROI +206%  d3 +169%
  WNBA live NO  0.25-0.30 gap<3pp:  n=6  WR 83%  ROI +195%  d3 +123%
  WNBA live YES 0.20-0.25 gap<3pp:  n=6  WR 50%  ROI +115%  d3 -107%
  MLS  live NO  0.40-0.50 gap<3pp:  n=26 WR 58%  ROI +20%   d3 +5.6%

(MLS 0.40-0.50 caught in sports_soccer_weekend_v1 Pattern B; this strategy
focuses on the 0.20-0.30 band where both YES and NO sides can carry alpha
when consensus aligns.)

Anti-signal (validates the mechanism):
  WNBA live YES 0.25-0.30 gap_kalshi_lt6+:  n=14 WR 7%   ROI -82%
  WNBA live NO  0.25-0.30 gap_kalshi_gt6+:  n=14 WR 7%   ROI -82%

When Kalshi diverges from EU by >6pp, the divergent side LOSES ~93%.
So consensus-gap < 3pp is the alpha filter, not just a noise check.

Sport coverage:
  WNBA + MLS + NBA live — sports with enough mid-band live signal volume.
  MLB live blocked (n=300+ at this band, -14% ROI — efficient market).
  NHL/NPB/KBO blocked (too thin per-band live data).

Paper-only at launch. SMALL sample on positive side (n=8-26 per cell);
larger anti-signal sample provides directional confidence. Promotion gate:
n >= 25 per sport × side AND ROI >= +25% AND drop-top-3 > 0 by ~6/15.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_consensus_aligned_live_v1")


class SportsConsensusAlignedLiveV1Strategy(SportsUnderdogV31Strategy):
    """Cross-sport (WNBA/MLS/NBA) live-phase 0.20-0.30 with EU-consensus
    alignment gate. Fires on whichever side is in band AND consensus-aligned."""

    TRADED_TICKERS_FILE = "data/sports_consensus_aligned_live_v1_tickers.json"
    STRATEGY_NAME = "sports_consensus_aligned_live_v1"
    BADGE_LABEL = "CAL"
    BADGE_COLOR = "#06b6d4"
    REGION_OVERRIDE = "eu"

    # Whitelist via blocklist — keep WNBA + MLS + NBA only.
    # MLB blocked (efficient at this band — confirmed by n=315 / -14% ROI in
    # sports_strategy_discovery.py output rank 220-221).
    # NHL/NPB/KBO/UFC/soccer-except-MLS blocked due to thin sub-cell sample.
    SPORT_BLOCKLIST = frozenset({
        "MLB", "NHL", "NPB", "KBO", "UFC",
        "EPL", "LAL", "BUN", "UCL",  # soccer-except-MLS
    })

    # Parent v31 sets PLAYOFF_BLOCKLIST = {NBA, NHL} — but NBA live mid-band
    # has separate playoff signal we want to capture, so override empty.
    PLAYOFF_BLOCKLIST = frozenset()

    # Parent v31 has SPORT_BLOCKLIST = {MLB} (just added in 374c300) — our
    # blocklist above is stricter (also blocks MLB) so the cascade is OK.

    # Live-phase included. Parent's PRE_GAME_ONLY=True blocks live → override.
    PRE_GAME_ONLY = False
    MIN_MTS_AT_ENTRY = None
    MAX_MTS_AT_ENTRY = None

    # Wide band — sub-side check inside evaluate (per-side 0.20-0.30 gate).
    MIN_ENTRY_PRICE = 0.20
    MAX_ENTRY_PRICE = 0.30

    # Parent's MAX_ENTRY_PRICE = 0.39 (MLB-reg cliff) doesn't apply to live
    # consensus-aligned mid-band — explicitly override above. Likewise band
    # cliff at 0.40c is a different alpha (caught by sports_soccer_weekend_v1
    # Pattern B), not relevant here.

    # Observation-only override — let WNBA + NBA + MLS reach evaluate.
    OBSERVATION_ONLY_SPORTS = frozenset()

    # Parent's DEPTH_DEAD_ZONE = (2000, 10000) — disable (cross-sport alpha
    # not depth-band-specific per sub-slice).
    DEPTH_DEAD_ZONE = None
    MIN_DEPTH_USDC = 10.0  # require some depth — live markets can be very thin
    MAX_DEPTH_USDC = 50000.0

    # Pair-fire dedup at game level.
    PAIR_FIRE_DEDUP = True

    # Consensus-gap maximum (percentage points). The discovery showed the
    # alpha lives within ±3pp of EU consensus. Stricter than soccer_weekend_v1
    # because the live-phase signal is more sensitive to noise.
    GAP_MAX_PP = 3.0

    def _get_trade_size(self, signal: dict) -> float:
        # Paper-only at launch — size 4. Live-phase markets at 0.20-0.30 have
        # thinner depth than pre-game (especially WNBA where total obs n=86K
        # vs MLB 1.5M). Promotion-gate raises to 8 on validation.
        return min(4.0, self._order_manager.bankroll * 0.005)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on either side in 0.20-0.30 with |Kalshi - EU consensus| < 3pp.

        fair_prob is the EU-consensus YES probability. For NO side we check
        the implied YES (1 - kalshi_no) against fair_prob.

        TIE tickers excluded (in case MLS is mis-tagged). Picks higher-payoff
        side when both qualify (rare — YES + NO sum to ~1).
        """
        if ticker.endswith("-TIE"):
            return None
        if fair_prob is None:
            return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT
        candidates = []

        # YES side check
        if 0.20 <= kalshi_yes <= 0.30:
            gap_pp = abs((kalshi_yes - fair_prob) * 100.0)
            if gap_pp < self.GAP_MAX_PP:
                fee_pct = self.config.gate_fee_pct(kalshi_yes)
                cost = kalshi_yes * (1 + fee_pct)
                payoff = 1.0 / cost
                if payoff >= 2.5:
                    candidates.append({
                        "side": "yes", "price": kalshi_yes,
                        "edge": payoff, "gap_pp": gap_pp,
                        "ticker": ticker,
                        "strategy": "consensus_aligned_live_v1",
                    })

        # NO side check
        if 0.20 <= kalshi_no <= 0.30:
            implied_yes_from_no = 1.0 - kalshi_no
            gap_pp = abs((implied_yes_from_no - fair_prob) * 100.0)
            if gap_pp < self.GAP_MAX_PP:
                fee_pct = self.config.gate_fee_pct(kalshi_no)
                cost = kalshi_no * (1 + fee_pct)
                payoff = 1.0 / cost
                if payoff >= 2.5:
                    candidates.append({
                        "side": "no", "price": kalshi_no,
                        "edge": payoff, "gap_pp": gap_pp,
                        "ticker": ticker,
                        "strategy": "consensus_aligned_live_v1",
                    })

        if not candidates:
            return None
        return max(candidates, key=lambda c: c["edge"])
