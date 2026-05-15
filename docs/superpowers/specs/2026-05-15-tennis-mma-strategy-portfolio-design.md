# Tennis/MMA Strategy Portfolio — Design

**Status**: Drafted 2026-05-15
**Author**: collaborative (user + Claude)
**Predecessor**: [2026-05-11 tennis/MMA observer wiring](2026-05-11-tennis-mma-observer-design.md) — observer deployed 5/11, 4-day data accumulation verified 5/15.

## Context

Tennis/MMA observer has been collecting since 2026-05-11. 4-day data snapshot (5/15 morning):

| sport | rows | tickers | days | Pinnacle% | n_books_eu (avg) | depth_avg ($) |
|---|---:|---:|---:|---:|---:|---:|
| ATP | 12,036 | 26 | 3 | 97.2% | 21.5 | 24,980 |
| WTA | 5,711 | 12 | 3 | 99.6% | 21.9 | 24,511 |
| UFC | 45,821 | 40 | 3 | 64.8% | 8.5 | 12,443 |

Original observer plan called for T+14 (5/25) promotion_eval with 4-archetype bucket replay. User chose to design net-new strategies in parallel rather than wait for the bucket-replay output — the goal is a **portfolio of structurally differentiated strategies**, not a single archetype-replay winner.

### Why net-new (not bucket replay)

The 4-archetype replay (longshot/underdog/mid-fav/chalk) tests whether existing price-band buckets transfer to individual sports. It does not exploit the *structural* properties of tennis and MMA:

- **Tennis**: 97-99% Pinnacle match rate — the highest in our portfolio. Pinnacle is also the sharpest tennis line globally.
- **Tennis BO5 format**: variance reduction vs BO3 of the same favorite — Kalshi inherits BO3 mental model.
- **Tennis surface specialization**: clay/grass specialists priced unevenly across books.
- **MMA single-shot variance**: KO/submission can flip any fight. Heavy chalk historically underperforms implied odds.

These properties suggest five strategies that exploit distinct alpha sources, all built on the same observer-paper-live phasing.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Number of strategies | 5 (A1, A2, A3, B1, C) | One per alpha source. B2 (UFC mid-dog) excluded — too similar to existing underdog family. |
| Phasing | observer-replay → paper → live | Same as A1/C1/B2/B1 deployment pattern. Selection-bias risk mitigated. |
| Cross-strategy dedup | NONE — same ticker may fire multiple strategies | User decision. Edge sources are independent (e.g. A1=Kalshi-Pinn gap vs C=Pinn-eu gap); 9-gate same-strategy dedup + dollar exposure cap still applies. |
| Spec format | One umbrella, five files | Matches [wnba-5strat](2026-05-13-wnba-strategy-expansion-design.md) precedent. |
| Launch mode | Paper-twin for all 5 at deploy | Slower than wnba (which went live immediately), but tennis/MMA have only 4 days of observer data — paper buffers selection-bias risk. |
| Promotion gate baseline | n≥25, ROI≥+3%, drop-top-5 ≥ 0 | Standard [sport_promotion_thresholds](../../memory/sport_promotion_thresholds.md). B1 relaxed to n≥15 due to slow UFC card cadence. |
| Live cutoff | mts > 60 (pre-match only) | Pinnacle locks shortly before match start; in-play lines fundamentally different. |

## Common scaffolding

All five strategies share:

1. **Base class**: `SportsTraderStrategy` (in `strategies/sports_trader.py`).
2. **Per-strategy `OBSERVATION_ONLY_SPORTS` override** to unblock the relevant sport(s):
   ```python
   OBSERVATION_ONLY_SPORTS = (
       SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}  # or {"UFC"}
   )
   ```
3. **9-gate dedup pipeline** prefixing each `evaluate()` (same-strategy dedup, NOT cross-strategy):
   ```python
   gates = [
       self._dedup_gate(ticker),
       self._price_band_gate(yes_ask),
       self._mts_gate(mts),
       self._depth_gate(depth),
       self._n_books_gate(n_books_eu),
       self._pinnacle_age_gate(pinnacle_age_s),
       self._pinnacle_confirm_gate(pinnacle_yes_prob, ...),
       self._strategy_signal_gate(...),  # per-strategy specific
       self._sizing_gate(edge, depth),
   ]
   if any(g is None for g in gates):
       return None
   ```
4. **LIVE_STRATEGIES env append** at deploy.
5. **promotion_eval.py**: per-strategy bucket label added so `python3 scripts/promotion_eval.py --window 14 --strategy <name>` works.
6. **PAPER_TWIN=1** for all 5 at launch.
7. **Dashboard row** per strategy (existing pattern handles automatically once STRATEGY_NAME registered).

## Strategy 1 — `tennis_pinnacle_arb_v1` (A1)

**Thesis**: Pinnacle is the sharpest tennis line globally. Kalshi tennis tickers are illiquid in the 1-3 day pre-match window, allowing retail flow to drift away from Pinnacle. We arb the gap.

**File**: `strategies/sports_tennis_pinnacle_arb_v1.py`
**Class**: `SportsTennisPinnacleArbV1Strategy(SportsTraderStrategy)`
**STRATEGY_NAME**: `tennis_pinnacle_arb_v1`

| Parameter | Value |
|---|---|
| Sports | ATP, WTA |
| Direction | Pinnacle-side (BUY whichever side Pinnacle's prob exceeds Kalshi's ask) |
| Signal gate | `\|kalshi_yes_ask − pinnacle_yes_prob\| ≥ 0.05` |
| Pinnacle freshness | `pinnacle_age_s < 600` |
| Consensus confirm | `n_books_eu ≥ 15`, `\|eu_yes_prob − pinnacle_yes_prob\| ≤ 0.03` |
| Price band | yes_ask ∈ [0.10, 0.90] |
| Window | mts ∈ [60, 4320] |
| Depth | top-of-book size ≥ $50 on entry side |
| Sizing | edge-scaled: $5 base + `round(edge*100)*$1`, cap $30 |
| Expected monthly n | 15-30 |
| Promotion gate | n≥25 settled, ROI≥+3%, drop-5 ROI ≥ 0 |

**Replay step (Phase 0)**: Backtest using settled tennis tickers in `odds_observations` from 5/12-5/14 (Italian Open active week). Required output: hypothetical fire log with kalshi_yes_ask, pinnacle_yes_prob, eu_yes_prob, settle_value. Validate signal direction (Pinnacle wins on average), measure raw ROI before fees.

## Strategy 2 — `atp_gs_bo5_chalk_v1` (A2)

**Thesis**: ATP Grand Slam matches are BO5. A −300 favorite in BO5 wins ~85-88% empirically vs ~78% in BO3-equivalent lines. Kalshi pricing inherits BO3 expectations on a BO5 contract — we BUY YES on top-seed chalk in early rounds where variance is largest.

**File**: `strategies/sports_atp_gs_bo5_chalk_v1.py`
**Class**: `SportsAtpGsBo5ChalkV1Strategy(SportsTraderStrategy)`
**STRATEGY_NAME**: `atp_gs_bo5_chalk_v1`

| Parameter | Value |
|---|---|
| Sports | ATP only |
| Direction | BUY YES on favorite |
| Slug filter | `TENNIS_SLUG_META[slug]["format"]=="BO5"` AND `TENNIS_SLUG_META[slug]["tier"]=="GS"` |
| Round filter | `parse_tennis_round(yes_sub_title) in {"R128", "R64", "R32"}` |
| Price band | yes_ask ∈ [0.70, 0.85] |
| Pinnacle confirm | `pinnacle_yes_prob ≥ 0.72` |
| Window | mts ∈ [60, 4320] |
| Depth | ≥ $30 |
| Sizing | size 6 (fixed) |
| Expected monthly n | 8-12 during slam weeks, 0 between slams |
| Promotion gate | n≥25 settled, ROI≥+3%, drop-5 ≥ 0. Realistic timeline: ~6 weeks (French Open finish + Wimbledon mid). |

**Calendar dependence**: French Open 5/25-6/8 → Wimbledon 6/29-7/13 → US Open 8/24-9/7. AO already past. Dashboard "sleeping" indicator for non-slam weeks.

## Strategy 3 — `tennis_bid_imbalance_v1` (A3)

**Thesis**: Top-of-book bid-size skew predicts settlement direction. Validated on crypto observers in [crypto_observer Phase 1](../../memory/crypto_observer_phase1_promotion_2026-05-14.md) (bid_imbalance_v1: 18/26=69% WR pre-revision). Tennis has 21+ book eu consensus and Pinnacle confirmation → cleaner imbalance signal than crypto.

**File**: `strategies/sports_tennis_bid_imbalance_v1.py`
**Class**: `SportsTennisBidImbalanceV1Strategy(SportsTraderStrategy)`
**STRATEGY_NAME**: `tennis_bid_imbalance_v1`

| Parameter | Value |
|---|---|
| Sports | ATP, WTA |
| Signal | `imbalance = yes_bid_size / (yes_bid_size + no_bid_size)` |
| BUY YES condition | `imbalance > 0.65` AND `yes_ask ∈ [0.50, 0.85]` |
| BUY NO condition | `imbalance < 0.35` AND `yes_ask ∈ [0.15, 0.50]` |
| Pinnacle confirm | direction agrees with `pinnacle_yes_prob − yes_ask` sign |
| Window | mts ∈ [60, 1440] (intraday — imbalance signal degrades long-window) |
| Sizing | size 3 |
| Expected monthly n | 20-40 |
| Promotion gate | n≥30, ROI≥+5%, Wilson lower > 0% |

**Phase 0 replay (mandatory before paper)**: Replay full ATP+WTA observer window. Bucket by imbalance decile. **Baseline** = settlement WR for the matching price band (0.50-0.85 BUY YES side, 0.15-0.50 BUY NO side) *without* the imbalance filter applied. Require **top imbalance decile WR ≥ baseline + 6pp** across full universe (not just trade fires). This guards against [bid_imbalance_v2 selection-bias trap](../../memory/bid_imbalance_v2_2026-05-15.md) where 5/14 n=26 promo cohort was a fluke vs full n=266 = 36% WR.

## Strategy 4 — `ufc_chalk_fade_v1` (B1)

**Thesis**: MMA is a single-shot sport — KO/submission ends fights instantly. Heavy chalk (favorites at ≥0.78 implied) historically underperforms by 3-5pp due to variance. We BUY NO on heavy UFC favorites.

**File**: `strategies/sports_ufc_chalk_fade_v1.py`
**Class**: `SportsUfcChalkFadeV1Strategy(SportsTraderStrategy)`
**STRATEGY_NAME**: `ufc_chalk_fade_v1`

| Parameter | Value |
|---|---|
| Sport | UFC only |
| Direction | BUY NO |
| Price band | yes_ask ≥ 0.78 |
| Pinnacle confirm | `pinnacle_yes_prob ≥ 0.78` (sharp also calls it chalk) |
| Window | mts ∈ [60, 4320] |
| Depth | ≥ $20 (UFC tickers thinner) |
| Sizing | size 2 (variance-aware) |
| Expected monthly n | 4-8 (slow card cadence) |
| Promotion gate | **relaxed** — n≥15, ROI≥+3%, Wilson lower ≥ −3% |

**Realistic timeline**: 3-4 months minimum to n=15. May stay paper-only into Q3 2026.

**Replay step (Phase 0)**: Identify settled UFC fights with yes_ask ≥ 0.78 in last 14 days (pre-observer + observer). Validate variance thesis (target: chalk WR < 78%).

## Strategy 5 — `tennis_surface_skew_v1` (C)

**Thesis**: Pinnacle prices surface specialization (clay/grass specialists) more aggressively than other books. When Pinnacle diverges from eu consensus on a clay or grass match, that gap is Pinnacle's surface-aware lean. We follow it before Kalshi adjusts.

**Distinction from A1**: A1 uses *Kalshi vs Pinnacle* gap. This uses *Pinnacle vs eu consensus* gap. Different signal, different alpha source. May fire concurrently with A1 on same ticker (per Decisions table).

**File**: `strategies/sports_tennis_surface_skew_v1.py`
**Class**: `SportsTennisSurfaceSkewV1Strategy(SportsTraderStrategy)`
**STRATEGY_NAME**: `tennis_surface_skew_v1`

| Parameter | Value |
|---|---|
| Sports | ATP, WTA |
| Surface filter | `TENNIS_SLUG_META[slug]["surface"] in {"clay", "grass"}` (hard excluded — neutral) |
| Signal | `surface_skew = pinnacle_yes_prob − eu_yes_prob` |
| BUY threshold | `\|surface_skew\| ≥ 0.04` → direction = sign of surface_skew |
| Confirmation | `n_books_eu ≥ 15`, `pinnacle_age_s < 600` |
| Price band | yes_ask ∈ [0.20, 0.80] |
| Window | mts ∈ [60, 4320] |
| Depth | ≥ $30 |
| Sizing | size 4 |
| Expected monthly n | ~15 in clay/grass season (May-July), ~0 hard-court |
| Promotion gate | n≥25, ROI≥+3%, drop-5 ≥ 0 |

**Seasonal dependence**: Clay swing peaks French Open. Grass swing peaks Wimbledon. Hard-court season (Q3/Q4) effectively idle.

## Risk register

1. **A1 + C overlap on same ticker**: explicit user OK — different alpha sources, dollar exposure cap (9-gate) still controls aggregate position size.
2. **A2 calendar idle**: dashboard sleep indicator + automated cron check to confirm strategy is healthy (not failing silently) between slams.
3. **B1 slow sample**: 3-4 month paper-only OK. Re-evaluate fire rate at 6 months — if n < 10, consider relaxing yes_ask threshold to 0.75.
4. **A3 selection-bias**: Phase 0 full-universe replay required before paper-twin promotion. Same trap that hit `bid_imbalance_v2`.
5. **NY resident**: All 5 are Kalshi-only. No Polymarket leg.
6. **In-play exclusion**: All 5 gate `mts > 60`. Pinnacle pre-match line locks ~30-60min before start; in-play prices are a fundamentally different market.

## Replay infrastructure (Phase 0)

Required for A1, A3, B1 before paper-twin enable. A2 and C use a lighter replay (paper-only thesis confirmation).

**Script**: `scripts/tennis_mma_strategy_replay.py` (new) — reads `odds_observations` + settled outcomes, simulates fires per strategy gate, outputs:
- n_fires, WR, ROI gross, ROI net (Kalshi 7% fee at entry per [feedback_backtest_sim_correctness](../../memory/feedback_backtest_sim_correctness.md))
- drop-top-5 ROI
- Wilson 95% CI on WR

**Strategy file readers** (mandatory per [feedback_observer_replay_before_gate_changes](../../memory/feedback_observer_replay_before_gate_changes.md)): replay must read the strategy `.py` line-by-line to mirror exact gate logic. No fake-edge approximations.

## Rollout plan

| Date | Milestone |
|---|---|
| 5/15 (today) | Spec approved + written-plans handoff. |
| 5/16-5/17 | Implement 5 .py files + replay script. Run Phase 0 replays. |
| 5/17 | Deploy all 5 paper-only (PAPER_TWIN=1, LIVE_STRATEGIES env update). |
| 5/18-5/31 | Paper accumulation. Watch for dashboard fire rate vs expected n. |
| 5/25 | T+14 observer promotion_eval (independent of these 5 strategies — runs the original 4-archetype replay for completeness). |
| 6/1+ | Per-strategy live promotion based on paper gates above. |

## Open questions

None at design time. All 5 risk items confirmed by user.

## Memory updates required

After spec approval and writing-plans handoff:

- New project memory: `tennis_mma_5strat_design_2026-05-15.md` linking to this spec, with summary of 5 strategy names, promotion gates, target dates.
- Update [tennis_mma_observer_2026-05-11.md](../../memory/tennis_mma_observer_2026-05-11.md) to note "Strategy portfolio designed 5/15 — see [[tennis-mma-5strat-design-2026-05-15]]".
