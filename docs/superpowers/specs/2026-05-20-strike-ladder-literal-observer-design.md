# Strike-Ladder Literal Observer — Design

**Date**: 2026-05-20 (redesigned 2026-05-21)
**Owner**: taejun
**Status**: **REDESIGNED 2026-05-21** — original 4-kind logic shipped 5/20 and
was retired ~24h later after first-week data showed it was measuring book
sparsity and OTM-zone tautology, not mispricing. See "2026-05-21 redesign"
section at the bottom of this doc and [[strike_ladder_literal_2026-05-20]]
memory entry. The motivation and gap-analysis below remain valid; the
underpriced/OTM gates do not.
**Predecessors**: [[cluster_sum_inverted_finding_2026-05-20]], [[ladder_crypto_phase0_resurrection_2026-05-19]]
**Related**: `ClusterSumObserver` (ATM±5 narrow sibling), `LadderCryptoStrategy` (depth-walk arb sibling)

## Motivation

Two existing components measure ladder-violation patterns on KXBTC/KXETH:

1. **`ClusterSumObserver`**
   ([strategies/crypto_observer.py:432](../../strategies/crypto_observer.py#L432))
   — sum-to-1 violation on ATM±5 cluster only. Per-bucket WR 1.7% /
   flipped 98.4% (multi-leg arb statistic, not a directional signal —
   see [[cluster_sum_inverted_finding_2026-05-20]]).
2. **`LadderCryptoStrategy`**
   ([strategies/ladder_crypto.py:41](../../strategies/ladder_crypto.py#L41))
   — full orderbook walk, requires depth at every leg
   (`min_depth_usdc` gate). Fires only when book-walk arb is
   executable. 14d gate 6/02.

There is a **measurement gap between them**: cluster_sum's narrow ATM±5
view misses wide-ladder mispricing patterns, and ladder_crypto's
depth-walk gate suppresses signal whenever OTM legs are thin (most
KXBTC events). Neither observer answers: *does the full-strike ladder
exhibit sum-to-1 violations in OTM zones that disappear under
depth-walk realism filters?*

The literal-ladder observer fills this gap. It measures:
- Sum of `ya` (and `yb`) across **all b_legs** in the event
  (typically 30-60 legs vs cluster_sum's 11)
- Sub-cluster sums at non-ATM centers (e.g., OTM ±5 cluster centered
  at strike 20% from spot)

Pure signal — no depth gate, no executability requirement. Logs more
fires than ladder_crypto, captures the residual signal that depth
gates eliminate. The 6/02 ladder_crypto eval is **decoupled** from this
observer's deploy: regardless of whether ladder_crypto promotes or
retires, this observer collects independent Phase 0 data.

## Architecture

### New observer

`StrikeLadderLiteralObserver` added to
`strategies/crypto_observer.py` alongside `ClusterSumObserver`
(~80 LOC). Same harness, same `EventContext` (no new fields), same
scan-cached `b_legs` data.

Rationale: shares all primitives with `ClusterSumObserver`. Inline
co-location lets us A/B compare the two observers' fire rates and WR
on the same event ticks.

### Observer constants

```python
LITERAL_MIN_LEGS = 10                   # event needs ≥10 b_legs for full-ladder math
LITERAL_FULL_SUM_UNDERPRICED_MAX = 0.95 # full-ladder sum_ya threshold (cf. cluster's 0.95)
LITERAL_FULL_SUM_OVERPRICED_MIN = 1.05  # full-ladder sum_yb threshold (cf. cluster's 1.05)
LITERAL_OTM_RADIUS = 5                  # OTM cluster radius (same as cluster_sum)
LITERAL_OTM_DIST_PCT_MIN = 0.10         # OTM cluster center ≥10% from spot
LITERAL_OTM_DIST_PCT_MAX = 0.30         # OTM cluster center ≤30% from spot
LITERAL_MTS_MIN_S = 60                  # excludes settlement-edge noise (see settlement_edge spec)
```

### Trigger logic

For each `EventContext` tick:

1. Skip if `len(ctx.b_legs) < LITERAL_MIN_LEGS`.
2. Skip if `ctx.close_time_secs is None or ctx.close_time_secs < LITERAL_MTS_MIN_S`
   (avoid overlap with `settlement_edge` observer).
3. **Full-ladder check**:
   - `full_sum_ya = sum(l.ya for l in ctx.b_legs)`
   - `full_sum_yb = sum(l.yb for l in ctx.b_legs)`
   - If `full_sum_ya ≤ LITERAL_FULL_SUM_UNDERPRICED_MAX` → emit signal
     `kind="full_underpriced"`, `predicted_outcome="yes"` (some leg
     must touch), target = leg with lowest `ya`.
   - If `full_sum_yb ≥ LITERAL_FULL_SUM_OVERPRICED_MIN` → emit signal
     `kind="full_overpriced"`, `predicted_outcome="no"`, target =
     leg with highest `yb`.
4. **OTM sub-cluster scan** (both sides of ATM):
   - For each direction `sign ∈ {+1, -1}`:
     - Find `center_idx` such that
       `abs(b_legs[center_idx].threshold - (1 + sign * 0.15) * ctx.spot)` minimized.
     - Skip if `|center.threshold - ctx.spot| / ctx.spot` outside
       `[LITERAL_OTM_DIST_PCT_MIN, LITERAL_OTM_DIST_PCT_MAX]`.
     - Cluster = `b_legs[max(0, center_idx - 5) : min(len, center_idx + 6)]`.
     - Skip if `len(cluster) < 5`.
     - Apply same sum_ya / sum_yb thresholds → emit signals with
       `kind="otm_underpriced"` / `kind="otm_overpriced"`,
       `features.cluster_center_dist_pct = (center - spot) / spot`.

### Output

`observer="strike_ladder_literal"` Signals to `crypto_observer_signals`:

```python
Signal(
    observer="strike_ladder_literal",
    series=ctx.series,
    event_key=ctx.event_key,
    market_id=target.ticker,
    predicted_outcome="yes" | "no",
    features={
        "kind": "full_underpriced" | "full_overpriced"
              | "otm_underpriced" | "otm_overpriced",
        "n_legs_total": len(ctx.b_legs),
        "full_sum_ya": round(full_sum_ya, 3),
        "full_sum_yb": round(full_sum_yb, 3),
        # OTM sub-cluster only:
        "cluster_n": cluster_n,                    # nullable
        "cluster_sum_ya": cluster_sum_ya,          # nullable
        "cluster_sum_yb": cluster_sum_yb,          # nullable
        "cluster_center_dist_pct": cluster_dist,   # signed, nullable
        # Target leg:
        "target_ya": target.ya,
        "target_yb": target.yb,
        "target_na": target.na,
        "target_dist_pct": (target.threshold - ctx.spot) / ctx.spot,
        "spot": ctx.spot,
        "mts_s": round(ctx.close_time_secs, 1),
    },
)
```

### Fire cadence

Once per scan tick per event per `kind`. No dedup across ticks — same
rationale as `settlement_edge`: each tick is a fresh observation,
analysis layer aggregates if needed.

**Expected volume**: full-ladder violations are rarer than
cluster_sum's ATM±5 violations (full ladder is closer to sum-to-1 in
expectation). OTM sub-cluster violations should fire on most events
where book is uneven (sparse OTM coverage). Estimate 100-300
fires/day per kind.

## Distinction from sibling components

| | cluster_sum | ladder_crypto | strike_ladder_literal |
|---|---|---|---|
| Scope | ATM±5 only | Full event | Full event + OTM sub-clusters |
| Depth gate | None | `min_depth_usdc` walk | None |
| Output table | `crypto_observer_signals` | `ladder_violations` | `crypto_observer_signals` |
| Phase | 0 (observer) | 0 (paper trader) | 0 (observer) |
| Eval date | rolling | 6/02 (14d gate) | 5/27 + 6/03 (1-2 weeks) |
| 6/02 ladder_crypto eval impact | none | self | **none** (decoupled) |

The "decoupled from 6/02" property is the design's purpose.
ladder_crypto may retire, promote, or stay paper — none of those
outcomes change what this observer measures.

## Data flow

Identical to other crypto_observer Phase 0 observers (no new
infrastructure):

```
scanner → EventContext → StrikeLadderLiteralObserver.evaluate(ctx)
       → Signal → db.record_crypto_observer_signal(...)
       → settlement backfill cron (existing) fills actual_outcome
```

No DB schema change, no scanner change, no new caches, no new
EventContext fields.

## Error handling

- `ctx.b_legs` partial / `len < LITERAL_MIN_LEGS` → silent skip.
- `ctx.spot ≤ 0` → guarded by harness; silent skip.
- Sub-cluster center calculation that finds no leg in range
  (sparse OTM) → silent skip for that side, continue with other side.
- DB write failure → existing `record_crypto_observer_signal()` path.

## Testing

3 tests in `tests/test_crypto_observer.py`:

```python
def test_strike_ladder_literal_full_underpriced_fires():
    # 30 b_legs with sum(ya) = 0.85 → emits full_underpriced signal
    ctx = _build_ctx_full_ladder(n_legs=30, ya_each=0.85/30, mts_s=300)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    full_sig = next(s for s in sigs if s.features["kind"] == "full_underpriced")
    assert full_sig.predicted_outcome == "yes"
    assert full_sig.features["full_sum_ya"] == pytest.approx(0.85, abs=0.01)

def test_strike_ladder_literal_otm_sub_cluster_fires():
    # spot=70k, OTM cluster center at 80.5k (+15%), 5 legs with sum(ya)=0.04
    ctx = _build_ctx_with_otm_cluster(spot=70_000, otm_center=80_500,
                                       otm_sum_ya=0.04)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    otm_sig = next(s for s in sigs if s.features["kind"] == "otm_underpriced")
    assert otm_sig.features["cluster_center_dist_pct"] == pytest.approx(0.15, abs=0.01)

def test_strike_ladder_literal_excludes_settlement_edge():
    # mts_s=30 → below MIN_S=60, no fire
    ctx = _build_ctx_full_ladder(n_legs=30, ya_each=0.01, mts_s=30)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    assert sigs == []
```

## Promotion criteria (5/27 + 6/02 eval)

Two-stage eval:

1. **5/27** (1 week): basic prevalence. Total fires per kind? Per-kind
   raw WR? Is the OTM sub-cluster signal different in distribution
   from the full-ladder signal?
2. **6/02** (2 weeks, coincides with ladder_crypto 14d gate):
   - Cross-reference: when `strike_ladder_literal` fires
     `full_underpriced`, does ladder_crypto fire `find_arb_opportunities`
     on the same event? Depth-gate filter rate = (literal_fires -
     ladder_crypto_fires) / literal_fires. If high, the signal is
     real but unexecutable.
   - Per-cohort fee-adjusted EV by `(kind, na_band, mts_band)` using
     [[fee_adjusted_promotion_criteria_2026-05-20]] methodology.
   - **Decision tree**:
     - ladder_crypto promotes + literal sub-cohort positive →
       retire literal, fold into ladder_crypto as a pre-walk filter.
     - ladder_crypto retires + literal sub-cohort positive → promote
       literal to Phase 1 paper as `strike_ladder_v1` (no
       depth-walk requirement).
     - Both negative → keep literal as Phase 0 observer indefinitely,
       no promotion.

## Files to touch

1. [strategies/crypto_observer.py](../../strategies/crypto_observer.py)
   — new constants (7 lines), new `StrikeLadderLiteralObserver` class
   (~80 LOC), append to `OBSERVERS` registry (1 line).
2. [tests/test_crypto_observer.py](../../tests/) — 3 new tests
   (~120 LOC).

No DB schema change, no new files.

## Out of scope

- Depth-aware variant — that is `LadderCryptoStrategy`'s job.
  Cross-referencing is an analysis-time operation at 6/02.
- T-leg (cumulative) ladder violations — different math
  (monotone-in-strike instead of sum-to-1). Defer until 5/27 data
  on B-leg ladder shows signal.
- Multi-bucket OTM scan beyond ±15% center — keeps the cohort space
  manageable for the 6/02 analysis. Wider OTM extensions are a
  follow-up if the ±15% cohort lights up.

## Open question (defer)

The `±15%` OTM center is a rule of thumb based on KXBTC daily move
distribution. KXBTCD (daily) and KXXRP have different vol scales.
Initial deploy uses the same `±15%` constant for all `TARGET_SERIES`.
Re-tune at 5/27 if KXXRP / KXBTCD fire rate is degenerate.

---

## 2026-05-21 redesign

### Why the original gates failed

After ~24h of fires (n=11,798, settled n=4,704), observed WR = **0%**.
Investigation revealed three structural issues, not a single bug:

1. **`full_underpriced` gate (`sum_ya ≤ 0.95`) measured book sparsity, not
   arb.** B-legs are *mutually exclusive* bucket markets, so fair
   `sum(ya) ≈ 1.0`. Observed median was 0.18 — fair pricing forbids this.
   It happens because most OTM-bucket YES-asks on Kalshi are absent
   (no resting offer → `ya = 0`). Gate fired on thin books, not on real
   "ladder costs less than $1" arb.

2. **`otm_underpriced` gate (`cluster_sum_ya ≤ 0.95` on 11-leg OTM
   cluster) was a tautology.** Sum of `P(expiry ∈ each OTM bucket)` over
   an OTM cluster is always small by definition (observed median 0.11).
   Gate fires whenever the cluster *is* OTM, regardless of pricing. EV
   under fair pricing is exactly 0: cluster cost = cluster prior.

3. **Single-leg `predicted_outcome` target was inverted.** Gates picked
   the *lowest-ya* leg in the cluster (deepest-OTM, ya ~ 0.01) and
   predicted YES. The basket arb thesis was correct ("some leg in the
   underpriced cluster must touch") but the chosen leg was the **least
   likely** of those to touch — implied probability ~1%, so 0% WR is
   prior, not signal failure.

### Real signal direction in the same data

Same fires recorded `full_sum_ya` median **1.89** (max 11.31). For a
mutually-exclusive 18+ leg ladder, fair `sum_ya ≈ 1.0`. Observed values
≥ 1.10 indicate **YES is broadly overpriced** — selling YES on every
resting leg captures `(sum_ya - 1) - fees` regardless of which leg
resolves YES.

### Redesigned observer

Single gate, single emitted `kind`:

```
gate:
  len(b_legs) ≥ 10
  mts_s ≥ 60
  n_legs_with_ya>0 ≥ 5         # sparsity defense
  sum(ya across all b_legs) ≥ 1.10
emit:
  kind            = "full_overpriced"
  market_id       = ticker of highest-ya resting leg (≈ ATM bucket)
  predicted_outcome = "no"
  features:
    full_sum_ya, resting_sum_ya, n_resting_ya_legs,
    n_legs_total, target_ya, target_dist_pct, spot, mts_s
```

WR proxy semantics: highest-ya leg is the "most likely YES" of the
ladder. Predicting NO means we expect the ladder's own consensus
(`target_ya`) to be wrong upward. `WR > (1 - target_ya)` = edge in the
mispricing direction. Pipeline-compatible single-row signal.

Basket-level EV (the real arb measurement) is computed by a separate
retrospective script: pull `full_sum_ya` + resolution outcome per event,
compute `(resting_sum_ya - 1) - fees - per_leg_slippage * n_resting`.

### What was dropped

- All four cluster_sum/full_sum gates from the original design.
- OTM ±15% sub-cluster scan entirely.
- `cluster_*` feature fields (replaced by `n_resting_ya_legs` + `resting_sum_ya`).
- Sister-strategy cross-reference with `ladder_crypto` (the new gate is
  in the *opposite direction* — overpriced ladder vs. depth-walk arb on
  underpriced ladder. They no longer measure overlapping conditions).

### Evaluation

- **5/27** (1 week): per-bucket fee-net EV by `full_sum_ya` band
  (1.10-1.20 / 1.20-1.50 / 1.50+). Cross-check WR proxy vs basket EV.
- **6/02** (2 weeks): retrospective basket evaluation. Decide
  promote-to-paper if basket-EV positive after fees + slippage on
  events with `n_resting_ya_legs ≥ 10`.
