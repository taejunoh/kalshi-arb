# Settlement-Edge Literal Observer — Design

**Date**: 2026-05-20
**Owner**: taejun
**Status**: Spec — pending implementation plan
**Predecessors**: [[crypto_observer_portfolio_2026-05-13]], [[cross_asset_observer_suite_2026-05-20]]
**Related**: `KalshiBookLagObserver` (dynamic event-driven sibling)

## Motivation

The thesis "as Kalshi markets approach settlement, market makers lift
quotes asymmetrically — leaving a depth-skew signal in the last ~30s"
is currently unmeasured. `KalshiBookLagObserver`
([strategies/crypto_cross_asset_observer.py:367](../../strategies/crypto_cross_asset_observer.py#L367))
fires on spot-move-with-stale-mid in a 20-min window; it is
**event-driven**, not static, and does not measure bid/ask side
asymmetry.

A static micro-timing observer in `mts_s ∈ (0, 30]` that watches the
depth skew `(ask_size - bid_size) / (ask_size + bid_size)` complements
the dynamic version. If MMs withdraw quotes asymmetrically in the
in-the-money vs out-of-money direction, the late-window skew is a weak
directional signal independent of the spot-lag thesis.

Phase 0 logging only — no Phase 1 trader until per-bucket WR and
fee-adjusted EV is measured (per [[fee_adjusted_promotion_criteria_2026-05-20]]).

## Architecture

### New observer

`SettlementEdgeObserver` added to `strategies/crypto_observer.py`
alongside `ClusterSumObserver` (~60 LOC). Lives in the same harness
because it uses the existing per-event `Leg` data and `EventContext`
fields — no new caches, no new EventContext fields.

Rationale: not a new file (unlike cross-asset suite). cluster_sum
(`crypto_observer.py:432`) is the structural sibling — same scan-cached
data, same single-tick fire decision, same Signal output. Co-location
keeps maintenance overhead low.

### Observer constants

```python
SETTLEMENT_EDGE_MTS_MAX_S = 30          # ≤30s to close
SETTLEMENT_EDGE_ATM_RADIUS = 2          # ATM ± 2 buckets
SETTLEMENT_EDGE_MIN_SIDE_SIZE = 1       # one side must be > 1 to compute skew
SETTLEMENT_EDGE_ABS_SKEW_MIN = 0.20     # |skew| > 0.20 to fire (excludes near-zero noise)
```

### Trigger logic

For each `b_leg` in the ATM±2 cluster:
1. `mts_s = ctx.close_time_secs`; skip if `mts_s is None or mts_s ≤ 0 or mts_s > 30`.
2. `ask_size = leg.ya_size`, `bid_size = leg.yb_size`.
3. Skip if `ask_size + bid_size < SETTLEMENT_EDGE_MIN_SIDE_SIZE`
   (thin-book degenerate — no MM presence).
4. `skew = (ask_size - bid_size) / (ask_size + bid_size)`.
5. Skip if `abs(skew) < SETTLEMENT_EDGE_ABS_SKEW_MIN`.
6. `predicted_outcome = "no" if skew > 0 else "yes"`.
   - `skew > 0` (ask thicker than bid) → MMs unwilling to sell → market
     not moving up to that strike → predict no-touch (yes-side loses).
   - `skew < 0` (bid thicker than ask) → MMs willing to buy → predict
     touch (yes-side wins).

### Output

Standard `Signal` to `crypto_observer_signals` via
`db.record_crypto_observer_signal()`, with `observer="settlement_edge"`:

```python
Signal(
    observer="settlement_edge",
    series=ctx.series,
    event_key=ctx.event_key,
    market_id=leg.ticker,
    predicted_outcome="yes" | "no",
    features={
        "skew": round(skew, 4),
        "ask_size": leg.ya_size,
        "bid_size": leg.yb_size,
        "ya": round(leg.ya, 4),
        "yb": round(leg.yb, 4),
        "na": round(leg.na, 4),
        "mts_s": round(mts_s, 2),
        "bucket_dist_from_atm": atm_idx - leg_idx,    # signed
        "spot": ctx.spot,
        "strike": leg.threshold,
    },
)
```

`features` capture enough state for post-hoc sub-cohort analysis:
mts-bucketed (e.g., 0-10s vs 10-20s vs 20-30s), skew-magnitude-bucketed,
ATM-distance, ya-bucketed (fee positioning).

### Fire cadence and dedup

Observer is invoked once per scan tick (~5s cadence in current harness).
At `mts_s ≤ 30`, that's ~6 fires per market over the window. **No
explicit dedup** — each tick is a fresh observation with possibly
different skew. The settlement backfill cron then joins each fire to
the same `actual_outcome`. Post-hoc analysis can either:
- Treat each fire independently (raw WR per fire), or
- Group by `(event_key, market_id)` and average skew (one signal per market).

We keep the raw per-fire stream and decide the aggregation in analysis.

## Data flow

```
scanner (every 5s)
  → for each crypto event:
      → build EventContext (existing)
      → for observer in [tail_decay, ..., settlement_edge]:
          → observer.evaluate(ctx) returns list[Signal]
          → db.record_crypto_observer_signal(signal) for each
  → daily backfill cron (existing):
      → query unsettled signals, fetch Kalshi outcome, fill actual_outcome
```

No changes to scanner, EventContext, or backfill cron — observer
plugs in via the existing `OBSERVERS` registry list at the bottom of
`crypto_observer.py`.

## Error handling

- Missing `ya_size` / `yb_size` (Kalshi sometimes omits) → skip leg silently.
- `ctx.b_legs` empty → existing `atm_bucket_index()` returns `None`, skip.
- `ctx.spot` zero/missing → existing harness already guards in `EventContext`
  construction.
- DB write failure → existing `record_crypto_observer_signal()` error path
  (try/except logging, no propagation).

No new failure modes.

## Testing

One test in `tests/test_crypto_observer.py`:

```python
def test_settlement_edge_fires_on_asymmetric_depth():
    ctx = _build_ctx(
        mts_s=15.0,
        spot=70_000,
        b_legs=[_leg("KXBTC-...-B69500", ya=0.40, yb=0.38, ya_size=10, yb_size=80),
                _leg("KXBTC-...-B70000", ya=0.50, yb=0.48, ya_size=20, yb_size=100),  # ATM, skew = -0.67
                _leg("KXBTC-...-B70500", ya=0.30, yb=0.28, ya_size=15, yb_size=60)],
    )
    signals = SettlementEdgeObserver().evaluate(ctx)
    assert len(signals) >= 1
    atm_sig = next(s for s in signals if s.market_id.endswith("B70000"))
    assert atm_sig.predicted_outcome == "yes"        # bid heavier → predict touch
    assert atm_sig.features["skew"] == pytest.approx(-0.667, abs=0.01)
```

Add 2-3 more tests for:
- `mts_s = 31` → no fire (outside window)
- `skew = 0.10` → no fire (below `ABS_SKEW_MIN`)
- `ask_size + bid_size = 0` → no fire (thin book)

YAGNI: skip dataset-replay test until first week's data confirms the
observer fires at expected rate.

## Promotion criteria (5/27 eval)

After 1 week of data:

1. Total fires > 50.
2. Per-cohort fee-adjusted EV check (per
   [[fee_adjusted_promotion_criteria_2026-05-20]]) — bucket by
   `(skew_mag, mts_s_band, na_band)` and look for any sub-cohort with:
   - n ≥ 30
   - WR ≥ 60%
   - `(2 * WR - 1) * avg_yb_price - fee > 0` for `predicted="no"` (or
     symmetric for `predicted="yes"`)

If yes → Phase 1 paper trader (`settlement_edge_v1`). If no → keep as
Phase 0 observer, re-eval at 6/02 with 2 weeks of data.

## Open question (defer)

ATM±2 vs ATM±3 vs ATM±5 — depends on KXBTC bucket spacing (~$250).
ATM±2 covers $500 either side of spot. KXETH and KXBTCD have different
spacing; the code uses `atm_bucket_index()` ± radius so the universe
adapts. Re-tune at 5/27 if fire distribution is skewed toward edges.

## Files to touch

1. [strategies/crypto_observer.py](../../strategies/crypto_observer.py)
   — new constants (4 lines), new `SettlementEdgeObserver` class
   (~60 LOC), append to `OBSERVERS` registry (1 line).
2. [tests/test_crypto_observer.py](../../tests/) — 4 new tests
   (~80 LOC).

No DB schema change, no new files, no scanner / harness / backfill
changes.

## Out of scope

- Time-series quote-pull velocity tracking (Design B in brainstorm)
  — deferred until 5/27 data shows static skew has alpha.
- MM-presence gate beyond the `MIN_SIDE_SIZE = 1` floor (Design C)
  — `features_json` already records `ask_size` + `bid_size`, so a
  post-hoc MM-presence sub-cohort can be filtered without code change.
- Phase 1 trader / market_order routing — gated on 5/27 eval.
