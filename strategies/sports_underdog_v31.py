"""Sports Underdog V3.1 — V3 underdog logic against EU sharp-book consensus."""
import logging

from strategies.sports_underdog import SportsUnderdogStrategy

logger = logging.getLogger("sports_underdog_v31")


class SportsUnderdogV31Strategy(SportsUnderdogStrategy):
    """Same underdog logic (fade consensus favorite > 60%, 2x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v31_tickers.json"
    STRATEGY_NAME = "sports_underdog_v31"
    BADGE_LABEL = "V3.1"
    BADGE_COLOR = "#ec4899"
    REGION_OVERRIDE = "eu"
    # Depth gate scaled to size 30 (2026-05-02 second pass). At max underdog
    # entry price ~60¢, peak stake = 30 × $0.60 = $18 → $30 keeps ~1.67×
    # cushion (matches the prior buffer ratio).
    MIN_DEPTH_USDC = 30.0
    # 2026-05-12: $2k-10k depth dead-zone. Cross-strategy v31 family analysis
    # (n=32 combined) shows consistent −60% ROI in this mid-depth band:
    #   underdog_v31: n=13, ROI −62%
    #   underdog_v32: n=13, ROI −62%
    #   midshot_v31:  n= 6, ROI −59%
    # Surrounding bands stay positive ($500-2k +22%, $10k+ +7% for underdog).
    # Cascades to v32-v36; v37_soccer overrides None (sweet spot at $2k-10k).
    DEPTH_DEAD_ZONE = (2000.0, 10000.0)
    # 2026-05-11: floor raised 0.20 → 0.30 from paired v31↔v32 bucket analysis.
    # Earlier 4/20 calibration (+95% at 20-25¢) has fully reversed in current
    # data — the universe-zero edge finding (memory: v31 underdog calibration
    # 2026-05-06) shows up most sharply in sub-30¢ buckets:
    #   <25¢:    n=24,  ROI −430%
    #   25-30¢:  n=56,  ROI −295%
    #   30-35¢:  n=86,  ROI +169%  ← kept (top-1 dependent, monitor)
    #   35-40¢:  n=282, ROI +9.6%  ← stable bulk
    # Cascades to v32, v33, v34, v35 via inheritance. v36_playoff/v36_us
    # already use 0.30 floor; v36_nhl_playoff (0.35) and v37_soccer (0.20-0.29)
    # have their own bands.
    MIN_ENTRY_PRICE = 0.30
    # Upper cliff at 0.40c (2026-05-03 reg-season analysis): n=62, ROI -25%
    # spread across 23 games (top-1 drop still -14%). 0.39c=+11% n=43.
    # 0.40c sits at payoff=2.0 fee-boundary — fee-adjusted edge ≈ 0, so
    # base evaluate's payoff>=2.0 gate accepts trades with ~no real edge.
    # Counterfactual: full v31 underdog ROI +3.3% → +14.4% with this cap.
    # 2026-05-25: 0.39 → 0.37. Fine-grained 30d audit shows 0.38-0.39 became
    # the new cliff: n=98, WR 36%, net -$453, ROI -23%. The 5/03 +11% n=43
    # at 0.39 has fully reverted. 0.30-0.37 retained as core (n=69, +$284
    # 30d). Cuts 58% of fires but flips strategy from -$169/30d to +$284/30d
    # projected. Re-eval ~6/15 with bigger post-fix sample.
    MAX_ENTRY_PRICE = 0.37
    # Pre-game-only (2026-04-21). Phase split shows live-game v31 entries
    # systematically lose (-13% ROI live, paper+live n=176) while pre-game
    # wins (+20% ROI, n=116 — strongest sample among v31 trio).
    PRE_GAME_ONLY = True
    # NBA playoff suspend (2026-04-30). Game-level analysis: NBA pre eu
    # underdog -36% n=32 in playoff regime; V3 baseline pre-playoff was
    # +236% n=12 (regime mismatch confirmed). The 35¢+ alpha sub-cell of
    # NBA playoff is captured by sports_playoff_underdog_v1 (eu) and
    # sports_playoff_underdog_v1_us (us), both override this to empty.
    # Cascades to v33, v34, v35 — all show same NBA playoff bleed pattern.
    # v32 already has its own explicit PLAYOFF_BLOCKLIST. Auto-resumes when
    # NBA returns to regular season (~2026-10).
    # 2026-05-03 NHL added — n=44 LIVE -12% (PnL -$25). Bleed concentrated
    # in 25-30c (-100% n=4 NO), 35-40c NO (-55% n=11). The 35-45c+50-55c
    # sweet spot is ~break-even (-4.5% n=33) — that band is now picked up
    # by sports_underdog_v36_nhl_playoff (pure price-band gate, paper-twin
    # baseline +33%). v32 has its own PLAYOFF_BLOCKLIST.
    PLAYOFF_BLOCKLIST = frozenset({"NBA", "NHL"})
    # 2026-05-21 MLB-reg block. 60d post-W17 regime-tightening cohort audit:
    #   v31 MLB-reg paper n=251 ROI -6.3%, LIVE n=206 ROI -8.7% (-$284)
    #   v32 MLB-reg paper n=185 ROI -9.4%, LIVE n=111 ROI -11.9% (-$417)
    #   midshot_v31 MLB-reg paper n=155 +4.2%, LIVE n=131 -11.7% (-$163)
    # W15-W16 MLB +EV (early-season pricing inefficiency); from W17 onward
    # market tightened — paper and LIVE both -EV. 60% of LIVE sports capital
    # was allocated here. Cascades to v32 (no SPORT_BLOCKLIST override).
    # playoff_underdog_v1/v1_us + soccer_underdog_v1 + v37 override this to
    # empty/their-own — unaffected. Re-eval ~2026-06-04.
    SPORT_BLOCKLIST = frozenset({"MLB"})
    # 2026-05-25 N4: Pinnacle-agreement gate. 60d data showed extreme-band
    # (|pin-entry|>0.05) bled n=20/-$60. Center band (n=40/+$408) is alpha.
    # Cuts ~33% of fires, projected save ~$30/month.
    MAX_PIN_GAP = 0.05

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 25→30 on 2026-05-02 (second pass, 1.2x more, total 1.5x vs
        # original 20). Live n=224, ROI 17.6% all-time and 14.7% last-7d are
        # consistent — alpha is mature and the prior 1.25x bump didn't
        # destabilize anything. Modest extra bump to harvest the larger
        # capital base before bankroll constrains us further.
        #
        # 2026-06-07 W23 SIZE FREEZE (de-risk, user-approved). WNBA underdog
        # margin is razor-thin (lifetime WR 38.7% vs ~36.4% post-fee breakeven,
        # +2.3pp) and declining (weekly WR 57→50→33→0%, W21-22 pooled 21.4% =
        # −15pp below BE), mirroring the v31 MLB early-season-closes arc. Kept
        # LIVE for one more week (W23) as a confirmation test but the cap is
        # frozen 30→5 so a bankroll recovery cannot scale exposure back up
        # while the edge is under suspicion. Current effective size is already
        # ~$2-3 (bankroll*0.02 off the re-synced ~$274 balance), so this does
        # NOT shrink live fires — it only blocks re-expansion. The W23 tripwire
        # (scripts/underdog_v31_w23_tripwire.py, fires ~6/15) decides
        # demote-to-paper vs lift-freeze. See [[v31_live_book_diagnosis_2026-06-07]].
        return min(5.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    # 2026-05-13 (WNBA expansion): unblock WNBA, add pair-fire dedup
    OBSERVATION_ONLY_SPORTS = (
        SportsUnderdogStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs):
        if "WNBAGAME" in ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
