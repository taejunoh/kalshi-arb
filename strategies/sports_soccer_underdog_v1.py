"""Sports Soccer Underdog V1 (renamed 2026-05-13 from sports_underdog_v37_soccer)
— soccer-only deep-underdog 20-29c TEAM-only. Renamed 2026-05-13.

Built 2026-05-06 from observer analysis of MLS/EPL/LAL/BUN/UCL pre-game data
collected since 2026-05-02. Pure price-band gate; TIE outcomes excluded.

Per-bucket TEAM-only ROI (5 leagues combined, last 5d settled, observer
replay with trades-derived outcomes):
  20-25c:  n=7,  WR 71%, ROI +234%
  25-29c:  n=6,  WR 33%, ROI +18%
  Combined 20-29c: n=13, WR 54%, ROI +120% (drop-top-3 +55%, drop-top-5 -7%)

TIE excluded after the same-window TIE 25-30c bleed across all leagues
(n=18, ROI -20%) — TIE prices around 0.20-0.30 are systematically slight
overpriced vs the ~25% draw probability that's structural to soccer.

Region-agnostic: midshot/underdog evaluate() do not use fair_prob, and
the eu/us consensus gap on this corpus is ~0%. REGION_OVERRIDE='eu' is
arbitrary.

Slate concentration risk: 77% of the n=13 sample is from the 5/02 MLS
slate. Promotion gate is n_games >= 25 + ROI >= +30% + drop-top-3 > 0.
At current 3.2 games/day pace, n=25 is ~4 days out (~5/10).

Paper-twin only at launch — no LIVE_STRATEGIES env entry. Promotion
decision after 4-6 weeks of paper data.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_soccer_underdog_v1")


class SportsSoccerUnderdogV1Strategy(SportsUnderdogV31Strategy):
    """Soccer-only deep-underdog (20-29c TEAM, payoff>=2x)."""

    TRADED_TICKERS_FILE = "data/sports_soccer_underdog_v1_tickers.json"
    STRATEGY_NAME = "sports_soccer_underdog_v1"
    BADGE_LABEL = "V1"
    BADGE_COLOR = "#4ade80"
    REGION_OVERRIDE = "eu"  # arbitrary — soccer eu/us consensus aligned

    # Soccer leagues only — block all non-soccer sports.
    # Deny-list approach so future-added leagues (e.g., Liga MX) auto-include.
    # 2026-05-21 EPL added — paper n=5 / LIVE n=5 / both 100% loss-rate / -$67
    # cohort-decomposition audit (sports_cohort_block_2026-05-21). EPL season
    # also ending ~5/25 so signal volume tails off naturally; block prevents
    # any further fire on season-end games. BUN/LAL/MLS retained (LIVE
    # +$192/+$80/+$80, paper consistent). Re-eval at start of 2026-27 EPL
    # season (~Aug 2026).
    # 2026-05-25: ATP/WTA/UFC added — individual-sport h2h leak surfaced when
    # 5/19 trade audit showed 3× ATP tennis fires (KXATPMATCH-26MAY18CUEDEL,
    # 26MAY19GASKHA, 26MAY18BROCAD) all -$36 wipes despite "soccer-only" name.
    # Parent v31 doesn't block individual sports; deny-list intent was league
    # leagues only.
    SPORT_BLOCKLIST = frozenset({
        "NBA", "WNBA", "MLB", "NHL", "NPB", "KBO", "EPL", "ATP", "WTA", "UFC"
    })

    # Override parent's OBSERVATION_ONLY_SPORTS (which contains soccer leagues)
    # so soccer tickers actually reach evaluate(). v37 is the explicit live
    # promotion of soccer; SPORT_BLOCKLIST above keeps non-soccer blocked.
    # Without this override, the parent gate at sports_trader.py:1127 silently
    # skips every MLS/EPL/LAL/UCL/BUN ticker — verified 2026-05-09 by 0
    # trade rows since 2026-05-06 launch.
    OBSERVATION_ONLY_SPORTS = frozenset()

    # Override v31's NBA/NHL playoff blocklist — soccer doesn't have playoff
    # phase the same way; v31's blocklist is irrelevant here.
    PLAYOFF_BLOCKLIST = frozenset()

    # Pre-game only with 24h cap. Same rationale as v36_nhl_playoff:
    # observer baseline measured on T-1h to T-24h FIRST obs.
    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 1440.0

    # Price band: 20-24c (tightened 2026-05-18 from 0.20-0.29). Live n=40
    # sub-band breakdown showed all alpha lives in 0.20-0.24:
    #   .20-.24: n=22, +$320, ROI +444.7%  ★ entire alpha here
    #   .25-.29: n=18, +$0.34, ROI +0.5%   ← flat, no contribution
    # Live+paper n=80 confirms same split (.20-.24 +444.5% / .25-.29 -0.3%).
    # Observer all-soccer-TEAM (n=282 settled) corroborates: .20-.25 +85.3%
    # alpha (t=4.7σ) vs .25-.30 +1.5% (noise). Cutting .25-.29 drops fire
    # rate ~45% but removes diluting band; expected ROI lift from +223%
    # to ~+445% on retained fires.
    # Lower floor 0.20 retained: .15-.20 observer +45.7% n=22 (t=2.18σ) is
    # fragile (drop-top-3 → -19%, MLS-only WR 62% vs others 7%). Hold
    # for separate Phase 0 evaluation.
    # .30-.35 dead zone (-58% ROI n=29) remains correctly excluded.
    MIN_ENTRY_PRICE = 0.20
    MAX_ENTRY_PRICE = 0.24

    # Depth gates (2026-05-12). Tier-based sizing entry/exit thresholds.
    # MIN $5 enables thin-book entries (observer replay tier1 $15-30 +142%
    # n=3, tier2 $30-100 +68% n=2). MAX $10k blocks thick-book bleed
    # (observer tier $10k+ -100% n=4 — cross-validated against
    # midshot/underdog same pattern).
    MIN_DEPTH_USDC = 5.0
    MAX_DEPTH_USDC = 10000.0
    # Override v31 DEPTH_DEAD_ZONE — soccer data shows $2k-10k is the SWEET
    # SPOT (+115% n=7, observer Plan A tier5), not a dead-zone. v31 cascade
    # would incorrectly block this band.
    DEPTH_DEAD_ZONE = None

    def _get_trade_size(self, signal: dict) -> float:
        """Tier-based cap policy by depth (2026-05-12, tier5 raised 5/18).

        Observer replay (n=34, 4/24+ MLS/EPL/LAL/BUN/UCL) shows depth-tier
        ROI variance: thin (cap 2-4 for cushion), mid (cap 8), sweet spot
        $2000-10k (cap 24, ROI +97% n=4). >$10k blocked at gate.

        2026-05-18 tier5 cap 24→36 (1.5x): live n=18 since 5/12 produced
        +$247 PnL / +57% ROI at cap 24. Cushion 287× → 191× still huge;
        depth/bankroll non-binding. Drop-top-3 fragility (89% of PnL from
        3 hits) means realized EV may not scale 1.5x — accept asymmetric
        variance for final week of EPL/LAL season. Re-eval ~6/01 with
        post-season MLS-only sample to decide cap 36 → 48 or rollback.

        Per-tier cushion ratio kept ≥4× by sizing cap to depth tier:
          tier1 ($15-30, cap 2): stake $0.58, cushion ≥26×
          tier2 ($30-100, cap 4): stake $1.16, cushion ≥26×
          tier3 ($100-500, cap 8): cushion ≥43×
          tier4 ($500-2000, cap 8): cushion ≥215×
          tier5 ($2000-10k, cap 36): stake ~$8.7, cushion ≥191×
        """
        ticker = signal.get("ticker", "")
        side = signal.get("side", "yes")
        depth = None
        if ticker:
            try:
                yd, nd = self._client.get_order_book_depth(ticker)
                depth = yd if side == "yes" else nd
            except Exception:
                pass
        if depth is None:
            cap = 8.0  # fallback to pre-tier cap
        elif depth >= 2000:
            cap = 36.0
        elif depth >= 100:
            cap = 8.0
        elif depth >= 30:
            cap = 4.0
        elif depth >= 15:
            cap = 2.0
        else:
            cap = 2.0  # very thin (5-15) — same as tier1 floor
        return min(cap, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire on TEAM side priced 20-29c with payoff >= 2x.

        Skips TIE outcomes — soccer 3-way markets have YES/NO/TIE tickers,
        and the TIE side is identified by ticker suffix '-TIE'. Observer
        analysis showed TIE 25-30c is a consistent bleed (-20% ROI).

        Does NOT use fair_prob (consensus) — 5-day cross-league data shows
        eu/us consensus aligned, and the alpha source is the raw price band
        rather than consensus disagreement.
        """
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # In soccer 3-way markets, the TIE outcome is its own ticker ending
        # with '-TIE'. The team tickers end with the team abbreviation.
        # When a TIE ticker is being evaluated:
        #   - YES = "draw happens" (TIE outcome) → skip
        #   - NO  = "draw doesn't happen" (some team wins) → also skip
        #     (we want to bet on a SPECIFIC team, not "anything but draw")
        if ticker.endswith("-TIE"):
            return None

        best_signal = None
        best_payoff = 0.0

        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (self.MIN_ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
                continue
            fee_pct = self.config.gate_fee_pct(price)
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 2.0 and payoff > best_payoff:
                best_signal = {
                    "side": side,
                    "price": price,
                    "edge": payoff,
                    "strategy": "soccer_underdog_v1",
                    "ticker": ticker,
                }
                best_payoff = payoff

        return best_signal
