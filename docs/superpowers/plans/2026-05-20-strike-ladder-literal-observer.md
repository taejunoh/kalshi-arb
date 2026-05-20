# Strike-Ladder Literal Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `StrikeLadderLiteralObserver` to the crypto-observer Phase 0 harness that records full-event-ladder sum violations plus OTM sub-cluster sum violations on KXBTC/KXETH bucket markets, complementing the existing narrow `ClusterSumObserver` (ATM±5) and the depth-gated `LadderCryptoStrategy`.

**Architecture:** New `BaseObserver` subclass in `strategies/crypto_observer.py` alongside `SettlementEdgeObserver` and `ClusterSumObserver`. Pure function over `EventContext` — no new DB schema, no new EventContext fields, no new files. 4 happy-path/edge-case tests + 1 registry smoke test added to `tests/test_crypto_observer.py`. Observer registered in `OBSERVER_CLASSES` so the harness picks it up automatically.

**Tech Stack:** Python 3 / dataclasses / pytest.

**Spec:** [docs/superpowers/specs/2026-05-20-strike-ladder-literal-observer-design.md](../specs/2026-05-20-strike-ladder-literal-observer-design.md)

---

## File Structure

**Modify:**

1. `strategies/crypto_observer.py`
   - Add 7 module-level constants near the existing constant block (after the `SETTLEMENT_EDGE_*` constants, currently around line 91).
   - Add `StrikeLadderLiteralObserver` class after `SettlementEdgeObserver` (currently lines 523-585) and before `BvTConsistencyObserver` (currently line 587).
   - Append `StrikeLadderLiteralObserver` to `OBSERVER_CLASSES` tuple (currently lines 1367-1386).

2. `tests/test_crypto_observer.py`
   - Import `StrikeLadderLiteralObserver` from `strategies.crypto_observer` (existing import block).
   - Add 5 new tests at end of file.

**No new files. No DB schema changes. No harness changes.**

---

## Task 1: Full observer + full_underpriced happy-path test

**Files:**
- Modify: `tests/test_crypto_observer.py` (imports + new test)
- Modify: `strategies/crypto_observer.py` (7 constants + `StrikeLadderLiteralObserver` class)

Implements the entire observer in one task because the OTM sub-cluster branch shares the same evaluate() method with the full-ladder branch — splitting them would require returning to edit the same method. Subsequent tasks are pure test additions that exercise the already-implemented branches.

- [ ] **Step 1: Add the import in the test file**

In `tests/test_crypto_observer.py`, modify the existing import block (around lines 11-23). Add `StrikeLadderLiteralObserver` to the imports. The new import block:

```python
from strategies.crypto_observer import (
    Leg, EventContext,
    TailDecayObserver, AtmFadeObserver, ClusterSumObserver,
    BvTConsistencyObserver, SpotDriftMomentumObserver,
    OpenWideningObserver, DeribitTailSkewObserver, BidImbalanceObserver,
    OpenNoTouchObserver, AtmHoldObserver, BidImbalanceFadeObserver,
    LateCloseTailObserver, DeribitAtmSkewObserver,
    MidOtmTailObserver,
    SettlementEdgeObserver,
    LognormalFairDevObserver,
    StrikeLadderLiteralObserver,
    _parse_suffix_threshold, _suffix_kind, _event_key, _build_leg,
    _lognormal_p_above, _flip,
)
```

(The exact prior import order may have small variations — only add the `StrikeLadderLiteralObserver` line in the right alphabetical/logical position; do not reorder other imports.)

- [ ] **Step 2: Add a test-file helper for building uniform-ya ladders**

Append (near the existing test-file helper section, just below `_ctx()` — around line 62, or simply at the top of the strike_ladder section if more convenient):

```python
def _uniform_ladder(spot: float, spacing: float, n_legs: int,
                    ya_each: float, yb_each: float):
    """Build a symmetric ladder of n_legs centered on spot.

    Each leg has identical ya/yb. Threshold = spot - n_legs/2*spacing + i*spacing.
    All sizes default to 200 (via _b()).
    """
    start = spot - (n_legs // 2) * spacing
    return [_b(int(start + i * spacing), ya=ya_each, yb=yb_each)
            for i in range(n_legs)]
```

- [ ] **Step 3: Add the happy-path test**

Append at end of test file:

```python
# ── strike_ladder_literal observer ────────────────────────────────


def test_strike_ladder_literal_full_underpriced_fires():
    """30-leg uniform ladder with sum_ya = 0.84 → full_underpriced signal."""
    # 30 legs * ya=0.028 each = sum_ya = 0.84 ≤ 0.95
    # 30 legs * yb=0.020 each = sum_yb = 0.60       (no full_overpriced)
    legs = _uniform_ladder(spot=70_000.0, spacing=1000.0, n_legs=30,
                           ya_each=0.028, yb_each=0.020)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)

    full_unders = [s for s in sigs if s.features["kind"] == "full_underpriced"]
    assert len(full_unders) == 1, (
        f"expected 1 full_underpriced signal, got {len(full_unders)}: {sigs}"
    )
    sig = full_unders[0]
    assert sig.predicted_outcome == "yes"
    assert sig.features["n_legs_total"] == 30
    assert sig.features["full_sum_ya"] == pytest.approx(0.84, abs=0.01)
    assert sig.features["full_sum_yb"] == pytest.approx(0.60, abs=0.01)
    # cluster_* fields are None for full-ladder kind
    assert sig.features["cluster_n"] is None
    assert sig.features["cluster_sum_ya"] is None
    assert sig.features["cluster_sum_yb"] is None
    assert sig.features["cluster_center_dist_pct"] is None
    assert "spot" in sig.features
    assert sig.features["spot"] == 70_000.0
    assert sig.features["mts_s"] == pytest.approx(300.0, abs=0.1)
```

- [ ] **Step 4: Run the test to confirm it fails**

```bash
pytest tests/test_crypto_observer.py::test_strike_ladder_literal_full_underpriced_fires -v
```

Expected: FAIL with `ImportError: cannot import name 'StrikeLadderLiteralObserver'`.

- [ ] **Step 5: Add the 7 module-level constants**

In `strategies/crypto_observer.py`, locate the `SETTLEMENT_EDGE_*` constants block (ends around line 91 with `SETTLEMENT_EDGE_ABS_SKEW_MIN = 0.20`). Add immediately after that line:

```python
# strike_ladder_literal (2026-05-20): full-event-ladder sum violation
# (kind="full_*") + OTM sub-cluster sum violation centered at ±15%
# from spot (kind="otm_*"). Sibling to ClusterSumObserver (narrow
# ATM±5) and LadderCryptoStrategy (depth-gated walk-arb). This
# observer has no depth gate — fires on raw ya/yb signal, lets
# analysis layer filter on executable depth at 5/27+ eval.
LITERAL_MIN_LEGS = 10
LITERAL_FULL_SUM_UNDERPRICED_MAX = 0.95
LITERAL_FULL_SUM_OVERPRICED_MIN = 1.05
LITERAL_OTM_RADIUS = 5
LITERAL_OTM_DIST_PCT_MIN = 0.10
LITERAL_OTM_DIST_PCT_MAX = 0.30
LITERAL_MTS_MIN_S = 60               # exclude settlement_edge window
```

- [ ] **Step 6: Add the `StrikeLadderLiteralObserver` class**

In `strategies/crypto_observer.py`, locate `SettlementEdgeObserver` (ends ~line 585) and the next observer (`class BvTConsistencyObserver` ~line 587). Insert the new observer between them:

```python
# ── 3c. strike_ladder_literal ──────────────────────────────────────


class StrikeLadderLiteralObserver(BaseObserver):
    """Phase 0: full-event-ladder sum violations + OTM sub-cluster sum violations.

    Fills the measurement gap between ClusterSumObserver (ATM±5 narrow)
    and LadderCryptoStrategy (depth-walk-gated). No depth gate — fires
    on raw ya/yb. Two output kinds:

    full_* : Σ across ALL b_legs in event.
      - sum_ya ≤ 0.95 → kind='full_underpriced', predicted='yes'
        (target = leg with lowest ya — most "free" YES bet).
      - sum_yb ≥ 1.05 → kind='full_overpriced', predicted='no'
        (target = leg with highest yb).

    otm_* : Σ across ±5 buckets centered at spot * (1 ± 0.15).
      Same sum thresholds. Skipped if center distance not in
      [LITERAL_OTM_DIST_PCT_MIN, LITERAL_OTM_DIST_PCT_MAX] or cluster
      has <5 legs (ladder edge).

    Excludes mts_s < LITERAL_MTS_MIN_S to avoid overlap with
    SettlementEdgeObserver, which owns the final-30s window.
    """
    name = "strike_ladder_literal"

    def _build_signal(self, ctx, target, kind, predicted, *,
                      n_legs_total, full_sum_ya, full_sum_yb,
                      cluster_n=None, cluster_sum_ya=None,
                      cluster_sum_yb=None, cluster_dist=None) -> Signal:
        return Signal(
            observer=self.name, series=ctx.series,
            event_key=ctx.event_key, market_id=target.ticker,
            predicted_outcome=predicted,
            features={
                "kind": kind,
                "n_legs_total": n_legs_total,
                "full_sum_ya": round(full_sum_ya, 3),
                "full_sum_yb": round(full_sum_yb, 3),
                "cluster_n": cluster_n,
                "cluster_sum_ya": (round(cluster_sum_ya, 3)
                                   if cluster_sum_ya is not None else None),
                "cluster_sum_yb": (round(cluster_sum_yb, 3)
                                   if cluster_sum_yb is not None else None),
                "cluster_center_dist_pct": (round(cluster_dist, 4)
                                            if cluster_dist is not None else None),
                "target_ya": round(target.ya, 4),
                "target_yb": round(target.yb, 4),
                "target_na": round(target.na, 4),
                "target_dist_pct": round((target.threshold - ctx.spot) / ctx.spot, 4),
                "spot": ctx.spot,
                "mts_s": round(ctx.close_time_secs, 1),
            },
        )

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs <= 0 or ctx.close_time_secs < LITERAL_MTS_MIN_S:
            return []
        if len(ctx.b_legs) < LITERAL_MIN_LEGS:
            return []
        if ctx.spot <= 0:
            return []

        out: list[Signal] = []
        n_legs_total = len(ctx.b_legs)
        full_sum_ya = sum(l.ya for l in ctx.b_legs)
        full_sum_yb = sum(l.yb for l in ctx.b_legs)

        # Full-ladder violations
        if full_sum_ya <= LITERAL_FULL_SUM_UNDERPRICED_MAX:
            target = min(ctx.b_legs, key=lambda l: l.ya)
            out.append(self._build_signal(
                ctx, target, "full_underpriced", "yes",
                n_legs_total=n_legs_total,
                full_sum_ya=full_sum_ya, full_sum_yb=full_sum_yb,
            ))
        if full_sum_yb >= LITERAL_FULL_SUM_OVERPRICED_MIN:
            target = max(ctx.b_legs, key=lambda l: l.yb)
            out.append(self._build_signal(
                ctx, target, "full_overpriced", "no",
                n_legs_total=n_legs_total,
                full_sum_ya=full_sum_ya, full_sum_yb=full_sum_yb,
            ))

        # OTM sub-cluster (both sides)
        for sign in (+1, -1):
            target_strike = ctx.spot * (1 + sign * 0.15)
            center_idx = min(
                range(len(ctx.b_legs)),
                key=lambda i: abs(ctx.b_legs[i].threshold - target_strike),
            )
            center = ctx.b_legs[center_idx]
            dist_pct = abs(center.threshold - ctx.spot) / ctx.spot
            if dist_pct < LITERAL_OTM_DIST_PCT_MIN or dist_pct > LITERAL_OTM_DIST_PCT_MAX:
                continue
            lo = max(0, center_idx - LITERAL_OTM_RADIUS)
            hi = min(len(ctx.b_legs), center_idx + LITERAL_OTM_RADIUS + 1)
            cluster = ctx.b_legs[lo:hi]
            if len(cluster) < 5:
                continue
            cluster_sum_ya = sum(l.ya for l in cluster)
            cluster_sum_yb = sum(l.yb for l in cluster)
            signed_dist = (center.threshold - ctx.spot) / ctx.spot
            if cluster_sum_ya <= LITERAL_FULL_SUM_UNDERPRICED_MAX:
                cluster_target = min(cluster, key=lambda l: l.ya)
                out.append(self._build_signal(
                    ctx, cluster_target, "otm_underpriced", "yes",
                    n_legs_total=n_legs_total,
                    full_sum_ya=full_sum_ya, full_sum_yb=full_sum_yb,
                    cluster_n=len(cluster), cluster_sum_ya=cluster_sum_ya,
                    cluster_sum_yb=cluster_sum_yb, cluster_dist=signed_dist,
                ))
            if cluster_sum_yb >= LITERAL_FULL_SUM_OVERPRICED_MIN:
                cluster_target = max(cluster, key=lambda l: l.yb)
                out.append(self._build_signal(
                    ctx, cluster_target, "otm_overpriced", "no",
                    n_legs_total=n_legs_total,
                    full_sum_ya=full_sum_ya, full_sum_yb=full_sum_yb,
                    cluster_n=len(cluster), cluster_sum_ya=cluster_sum_ya,
                    cluster_sum_yb=cluster_sum_yb, cluster_dist=signed_dist,
                ))
        return out
```

- [ ] **Step 7: Run the happy-path test**

```bash
pytest tests/test_crypto_observer.py::test_strike_ladder_literal_full_underpriced_fires -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): add strike_ladder_literal Phase 0 observer

Full-event-ladder sum violation + OTM sub-cluster sum violation at
±15% from spot. Fills the measurement gap between ClusterSumObserver
(ATM±5 narrow) and LadderCryptoStrategy (depth-walk-gated). No depth
gate — pure signal. Excludes mts_s < 60s (settlement_edge owns that
window). Promotion gate 5/27 + 6/02 per fee-adjusted-EV methodology.

Spec: docs/superpowers/specs/2026-05-20-strike-ladder-literal-observer-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: OTM sub-cluster fire test

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

The Task 1 implementation already handles the OTM sub-cluster branch — this task adds a test that exercises it specifically (full-ladder gate not tripped, OTM sub-cluster gate tripped).

- [ ] **Step 1: Append the OTM-only test**

```python
def test_strike_ladder_literal_otm_sub_cluster_fires():
    """ATM region rich, OTM region cheap → full doesn't fire but OTM does.

    Ladder shape:
      - 30 legs total, $1000 spacing, spot=70000.
      - Center 10 legs (65500-74500): ya=0.10, yb=0.05 → ATM sum_ya=1.0
      - Outer 20 legs (55500-64500 + 75500-84500): ya=0.01, yb=0.005
      - Full sum_ya = 1.0 + 20 * 0.01 = 1.20 (between 0.95 and 1.05? NO,
        1.20 > 1.05, so full_overpriced check needs sum_yb).
      - Full sum_yb = 0.50 + 20 * 0.005 = 0.60 → no full_overpriced.

    So full_underpriced gate (sum_ya ≤ 0.95) NOT met (1.20 > 0.95).
    full_overpriced gate (sum_yb ≥ 1.05) NOT met (0.60 < 1.05).
    But OTM cluster at +15% sits in outer region: 5 legs * ya=0.01
    = 0.05 ≤ 0.95 → otm_underpriced fires.
    """
    spot = 70_000.0
    spacing = 1000.0
    # 30 legs: indices 0..29 → thresholds 55500..84500
    legs = []
    for i in range(30):
        thr = int(55500 + i * spacing)
        if 65500 <= thr <= 74500:
            legs.append(_b(thr, ya=0.10, yb=0.05))     # ATM rich
        else:
            legs.append(_b(thr, ya=0.01, yb=0.005))    # OTM cheap
    ctx = _ctx(b_legs=legs, spot=spot, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)

    # No full-ladder signal
    full_sigs = [s for s in sigs
                 if s.features["kind"] in ("full_underpriced", "full_overpriced")]
    assert full_sigs == [], f"unexpected full-ladder signal: {full_sigs}"

    # OTM signals fire on at least one side (both sides should fire here
    # since the ladder is symmetric, but the +15% side is sufficient).
    otm_under = [s for s in sigs if s.features["kind"] == "otm_underpriced"]
    assert len(otm_under) >= 1, (
        f"expected at least one otm_underpriced signal, got {sigs}"
    )
    sig = otm_under[0]
    assert sig.predicted_outcome == "yes"
    assert sig.features["cluster_n"] >= 5
    assert sig.features["cluster_sum_ya"] is not None
    assert sig.features["cluster_sum_ya"] <= 0.95
    assert sig.features["cluster_center_dist_pct"] is not None
    assert abs(sig.features["cluster_center_dist_pct"]) >= 0.10
    assert abs(sig.features["cluster_center_dist_pct"]) <= 0.30
```

- [ ] **Step 2: Run all strike_ladder_literal tests**

```bash
pytest tests/test_crypto_observer.py -k strike_ladder_literal -v
```

Expected: 2 PASS (1 from Task 1 + 1 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin strike_ladder_literal OTM sub-cluster path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: full_overpriced fire test

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

- [ ] **Step 1: Append the full_overpriced test**

```python
def test_strike_ladder_literal_full_overpriced_fires():
    """30-leg ladder with sum_yb = 1.20 → full_overpriced signal, predicted='no'."""
    # 30 legs * ya=0.05 = sum_ya = 1.50  (not in underpriced zone)
    # 30 legs * yb=0.04 = sum_yb = 1.20  ≥ 1.05  → full_overpriced
    legs = _uniform_ladder(spot=70_000.0, spacing=1000.0, n_legs=30,
                           ya_each=0.05, yb_each=0.04)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)

    full_over = [s for s in sigs if s.features["kind"] == "full_overpriced"]
    assert len(full_over) == 1, (
        f"expected 1 full_overpriced signal, got {len(full_over)}: {sigs}"
    )
    sig = full_over[0]
    assert sig.predicted_outcome == "no"
    assert sig.features["full_sum_yb"] == pytest.approx(1.20, abs=0.01)
    assert sig.features["full_sum_ya"] == pytest.approx(1.50, abs=0.01)
    assert sig.features["cluster_n"] is None     # full-kind has no cluster
```

- [ ] **Step 2: Run all strike_ladder_literal tests**

```bash
pytest tests/test_crypto_observer.py -k strike_ladder_literal -v
```

Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin strike_ladder_literal full_overpriced path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: mts_s window + MIN_LEGS gate tests

**Files:**
- Modify: `tests/test_crypto_observer.py` (2 new tests)

- [ ] **Step 1: Append the gate tests**

```python
def test_strike_ladder_literal_excludes_settlement_edge_window():
    """mts_s < LITERAL_MTS_MIN_S (60) → no fire, even with a violation."""
    legs = _uniform_ladder(spot=70_000.0, spacing=1000.0, n_legs=30,
                           ya_each=0.028, yb_each=0.020)
    # mts_s = 30 is inside settlement_edge window — strike_ladder_literal skips
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=30.0)
    assert StrikeLadderLiteralObserver().evaluate(ctx) == []

    # mts_s = None / 0 / negative also skip
    for invalid_close_s in (None, 0.0, -5.0):
        ctx_invalid = _ctx(b_legs=legs, spot=70_000.0,
                           close_s=invalid_close_s)
        assert StrikeLadderLiteralObserver().evaluate(ctx_invalid) == []


def test_strike_ladder_literal_skips_below_min_legs():
    """Fewer than LITERAL_MIN_LEGS (10) legs → no fire even with strong violation."""
    # 9 legs * ya=0.05 = sum_ya = 0.45 (well below 0.95) but n<10 → skip
    legs = _uniform_ladder(spot=70_000.0, spacing=1000.0, n_legs=9,
                           ya_each=0.05, yb_each=0.04)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    assert StrikeLadderLiteralObserver().evaluate(ctx) == []
```

- [ ] **Step 2: Run all strike_ladder_literal tests**

```bash
pytest tests/test_crypto_observer.py -k strike_ladder_literal -v
```

Expected: 5 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin strike_ladder_literal mts + min_legs gates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Register observer in `OBSERVER_CLASSES`

**Files:**
- Modify: `strategies/crypto_observer.py` (2 line addition in OBSERVER_CLASSES tuple)
- Modify: `tests/test_crypto_observer.py` (1 registry smoke test)

Without this step the observer class exists but the harness never instantiates it.

- [ ] **Step 1: Append `StrikeLadderLiteralObserver` to OBSERVER_CLASSES**

In `strategies/crypto_observer.py`, locate the `OBSERVER_CLASSES` tuple (begins with `OBSERVER_CLASSES: tuple[type[BaseObserver], ...] = (`, currently around line 1367). The existing tuple body ends with `LognormalFairDevObserver,` and then `)`.

Insert two new lines immediately before the closing `)` of the tuple — that is, after the `LognormalFairDevObserver,` line:

```python
    # 2026-05-20: full-ladder + OTM sub-cluster sum violations (no depth gate)
    StrikeLadderLiteralObserver,
```

The tuple should now end with:

```python
    LognormalFairDevObserver,
    # 2026-05-20: full-ladder + OTM sub-cluster sum violations (no depth gate)
    StrikeLadderLiteralObserver,
)
```

- [ ] **Step 2: Add a registry smoke test**

Append to `tests/test_crypto_observer.py`:

```python
def test_strike_ladder_literal_registered_in_observer_classes():
    """Harness picks up strike_ladder_literal via OBSERVER_CLASSES tuple."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert StrikeLadderLiteralObserver in OBSERVER_CLASSES
```

- [ ] **Step 3: Run all crypto_observer tests**

```bash
pytest tests/test_crypto_observer.py -v
```

Expected: all tests PASS (including 6 strike_ladder_literal tests now: 5 from Tasks 1-4 + 1 registry).

- [ ] **Step 4: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): register strike_ladder_literal in OBSERVER_CLASSES

Wires the observer into the scan-tick harness. Without this commit
StrikeLadderLiteralObserver exists in code but is never instantiated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Final smoke + sanity

**Files:**
- (none — verification only)

- [ ] **Step 1: Run the full crypto_observer test suite**

```bash
pytest tests/test_crypto_observer.py tests/test_crypto_observer_phase1.py -v
```

Expected: All tests PASS. None of the pre-existing tests should be affected (strike_ladder_literal is purely additive).

- [ ] **Step 2: Grep sanity check**

```bash
grep -rn "strike_ladder_literal\|StrikeLadderLiteralObserver" strategies/crypto_observer.py tests/test_crypto_observer.py
```

Expected (counts approximate):
- `strategies/crypto_observer.py`: ~5+ mentions (constants comment block, class def, `name` attr, OBSERVER_CLASSES entry)
- `tests/test_crypto_observer.py`: ~10+ mentions (import + 6 tests)

- [ ] **Step 3: Confirm observer instantiates with the expected name**

```bash
python3 -c "from strategies.crypto_observer import OBSERVER_CLASSES, StrikeLadderLiteralObserver; assert StrikeLadderLiteralObserver in OBSERVER_CLASSES; obs = StrikeLadderLiteralObserver(); print(f'name={obs.name!r} ok')"
```

Expected:
```
name='strike_ladder_literal' ok
```

- [ ] **Step 4: Push when ready** (not automated — user decides deploy timing).

---

## Out of scope (per spec)

- Depth-aware variant — already covered by `LadderCryptoStrategy`.
- T-leg (cumulative) ladder violations — different math (monotone-in-strike). Deferred until B-leg data shows alpha.
- Multi-bucket OTM scan beyond `±15%` center — deferred.
- Phase 1 trader — gated on 5/27 + 6/02 eval per spec promotion decision tree.

---

## Expected commit graph after plan complete

```
* feat(crypto_observer): register strike_ladder_literal in OBSERVER_CLASSES
* test(crypto_observer): pin strike_ladder_literal mts + min_legs gates
* test(crypto_observer): pin strike_ladder_literal full_overpriced path
* test(crypto_observer): pin strike_ladder_literal OTM sub-cluster path
* feat(crypto_observer): add strike_ladder_literal Phase 0 observer
* docs(plan): strike_ladder_literal observer implementation plan
```

5 implementation commits.
