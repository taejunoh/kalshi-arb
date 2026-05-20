# Settlement-Edge Literal Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `SettlementEdgeObserver` to the crypto-observer Phase 0 harness that records bid/ask depth-skew signals for KXBTC/KXETH bucket markets in the final 30 seconds before settlement.

**Architecture:** New `BaseObserver` subclass in `strategies/crypto_observer.py` alongside `ClusterSumObserver`. Pure function over `EventContext` — no new DB schema, no new EventContext fields, no new files. 4 unit tests added to `tests/test_crypto_observer.py`. Observer registered in `OBSERVER_CLASSES` tuple so harness picks it up automatically.

**Tech Stack:** Python 3 / dataclasses / pytest. Same primitives every other observer in the file uses.

**Spec:** [docs/superpowers/specs/2026-05-20-settlement-edge-literal-observer-design.md](../specs/2026-05-20-settlement-edge-literal-observer-design.md)

---

## File Structure

**Modify:**

1. `strategies/crypto_observer.py`
   - Add 4 module-level constants near existing constant block (around line 80, after `MID_OTM_TAIL_*` constants).
   - Add `SettlementEdgeObserver` class after `ClusterSumObserver` (currently line 432-490) and before the `# ── 4. bvt_consistency ──` section (line 493).
   - Append `SettlementEdgeObserver` to `OBSERVER_CLASSES` tuple (line 1194-1208).

2. `tests/test_crypto_observer.py`
   - Import `SettlementEdgeObserver` from `strategies.crypto_observer` (line 11-21 import block).
   - Add 4 new tests at end of file.

**No new files.** **No schema changes.** **No harness changes.**

---

## Task 1: Happy-path test + minimal implementation

**Files:**
- Modify: `tests/test_crypto_observer.py` (imports + new test)
- Modify: `strategies/crypto_observer.py` (4 constants + `SettlementEdgeObserver` class)

This task introduces the observer with the canonical happy path: market within ATM±2, mts_s ≤ 30, ask side much thicker than bid → predict "no".

- [ ] **Step 1: Add the import in the test file**

In `tests/test_crypto_observer.py`, modify the existing import block (around line 11-21). Add `SettlementEdgeObserver` to the existing import. The new import block:

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
    _parse_suffix_threshold, _suffix_kind, _event_key, _build_leg,
    _lognormal_p_above, _flip,
)
```

- [ ] **Step 2: Add the happy-path test at the end of the test file**

Append to `tests/test_crypto_observer.py`:

```python
# ── settlement_edge observer ──────────────────────────────────────


def test_settlement_edge_fires_on_asymmetric_depth_in_atm():
    """ATM bucket within mts_s ≤ 30 with ask much thinner than bid →
    predict_outcome='yes' (bid heavier → market expects touch)."""
    # Build a ladder with $250 spacing, spot 70000, ATM bucket = B70000.
    # ATM bucket has ask_size=20, bid_size=100 → skew = (20-100)/120 = -0.667
    # Predicted='yes' since skew < 0.
    b_legs = [
        _b(69500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
        _b(69750, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70000, ya=0.50, yb=0.48, ya_size=20, yb_size=100),   # ATM (closest to spot 70000)
        _b(70250, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=15.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    # ATM±2 covers all 5 legs (radius 2 → indices 0..4 in this 5-leg ladder).
    # Every leg has skew magnitude ≥ 0.20 (all are 60-80% bid-heavy).
    # Expect a signal on the ATM leg.
    atm_sigs = [s for s in sigs if s.market_id.endswith("B70000")]
    assert len(atm_sigs) == 1, f"expected 1 ATM signal, got {len(atm_sigs)}: {sigs}"
    atm = atm_sigs[0]
    assert atm.predicted_outcome == "yes"
    assert atm.features["skew"] == pytest.approx(-0.667, abs=0.01)
    assert atm.features["ask_size"] == 20
    assert atm.features["bid_size"] == 100
    assert atm.features["mts_s"] == pytest.approx(15.0, abs=0.01)
    assert atm.features["bucket_dist_from_atm"] == 0
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
pytest tests/test_crypto_observer.py::test_settlement_edge_fires_on_asymmetric_depth_in_atm -v
```

Expected: FAIL with `ImportError: cannot import name 'SettlementEdgeObserver'` (or `AttributeError`).

- [ ] **Step 4: Add the four module-level constants**

In `strategies/crypto_observer.py`, locate the `MID_OTM_TAIL_*` constants block (around line 75-80). Add immediately after the last `MID_OTM_TAIL_*` line:

```python
# settlement_edge (2026-05-20): static micro-timing depth-skew observer.
# Watches mts_s ≤ 30s window for bid/ask size asymmetry on ATM±N
# bucket cluster. Tests the thesis that market makers withdraw quotes
# asymmetrically near settlement, leaving a depth-skew tell about the
# resolution direction. Phase 0 logging only — promotion gate per
# fee-adjusted-EV methodology.
SETTLEMENT_EDGE_MTS_MAX_S = 30
SETTLEMENT_EDGE_ATM_RADIUS = 2
SETTLEMENT_EDGE_MIN_SIDE_SIZE = 1         # ask_size + bid_size must exceed this
SETTLEMENT_EDGE_ABS_SKEW_MIN = 0.20       # |skew| ≥ this to fire
```

- [ ] **Step 5: Add the `SettlementEdgeObserver` class**

In `strategies/crypto_observer.py`, locate `ClusterSumObserver` (ends at line 490) and the next section comment (`# ── 4. bvt_consistency ──` at line 493). Insert the new observer between them:

```python
# ── 3b. settlement_edge ────────────────────────────────────────────


class SettlementEdgeObserver(BaseObserver):
    """Phase 0: bid/ask depth-skew tell in the final 30s before settlement.

    Thesis: as MMs withdraw quotes near settlement, the bucket's
    ask/bid depth becomes asymmetric in a direction that correlates
    with the resolution. We measure
        skew = (ask_size - bid_size) / (ask_size + bid_size)
    on each leg in the ATM ± SETTLEMENT_EDGE_ATM_RADIUS cluster when
    mts_s ≤ SETTLEMENT_EDGE_MTS_MAX_S, and predict:
        skew > 0  (ask thicker → MMs not selling) → predict 'no'
        skew < 0  (bid thicker → MMs willing buy) → predict 'yes'

    Sibling to KalshiBookLagObserver (dynamic event-driven on spot move).
    This observer is static — fires every tick within the window.
    """
    name = "settlement_edge"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs <= 0 or ctx.close_time_secs > SETTLEMENT_EDGE_MTS_MAX_S:
            return []
        if not ctx.b_legs:
            return []
        atm_idx = ctx.atm_bucket_index()
        if atm_idx is None:
            return []

        lo = max(0, atm_idx - SETTLEMENT_EDGE_ATM_RADIUS)
        hi = min(len(ctx.b_legs), atm_idx + SETTLEMENT_EDGE_ATM_RADIUS + 1)

        out: list[Signal] = []
        for i in range(lo, hi):
            leg = ctx.b_legs[i]
            total_size = leg.ya_size + leg.yb_size
            if total_size <= SETTLEMENT_EDGE_MIN_SIDE_SIZE:
                continue
            skew = (leg.ya_size - leg.yb_size) / total_size
            if abs(skew) < SETTLEMENT_EDGE_ABS_SKEW_MIN:
                continue
            predicted = "no" if skew > 0 else "yes"
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome=predicted,
                features={
                    "skew": round(skew, 4),
                    "ask_size": leg.ya_size,
                    "bid_size": leg.yb_size,
                    "ya": round(leg.ya, 4),
                    "yb": round(leg.yb, 4),
                    "na": round(leg.na, 4),
                    "mts_s": round(ctx.close_time_secs, 2),
                    "bucket_dist_from_atm": i - atm_idx,
                    "spot": ctx.spot,
                    "strike": leg.threshold,
                },
            ))
        return out
```

- [ ] **Step 6: Run the happy-path test to confirm it passes**

```bash
pytest tests/test_crypto_observer.py::test_settlement_edge_fires_on_asymmetric_depth_in_atm -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): add settlement_edge Phase 0 observer

Static micro-timing observer measuring bid/ask depth skew on ATM±2
buckets in the final 30s before settlement. Pure signal logging — no
trades. Sibling to KalshiBookLagObserver (which is dynamic event-driven
on spot moves). Promotion gate 5/27 per fee-adjusted-EV methodology.

Spec: docs/superpowers/specs/2026-05-20-settlement-edge-literal-observer-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Edge case — mts_s outside the 30s window

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

The implementation in Task 1 already gates on `close_time_secs > SETTLEMENT_EDGE_MTS_MAX_S`. This task pins that behavior with an explicit test.

- [ ] **Step 1: Add the out-of-window test**

Append to `tests/test_crypto_observer.py`:

```python
def test_settlement_edge_does_not_fire_outside_30s_window():
    """mts_s = 60 (outside ≤30 window) → no signal even with strong skew."""
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),  # skew = -0.818
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=60.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert sigs == [], f"expected no signals at mts_s=60, got: {sigs}"


def test_settlement_edge_does_not_fire_at_or_below_zero_mts():
    """mts_s ≤ 0 (post-close or missing) → no signal."""
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),
    ]
    # Zero
    ctx_zero = _ctx(b_legs=b_legs, spot=70000.0, close_s=0.0)
    assert SettlementEdgeObserver().evaluate(ctx_zero) == []
    # Negative (already past close)
    ctx_neg = _ctx(b_legs=b_legs, spot=70000.0, close_s=-5.0)
    assert SettlementEdgeObserver().evaluate(ctx_neg) == []
    # None
    ctx_none = _ctx(b_legs=b_legs, spot=70000.0, close_s=None)
    assert SettlementEdgeObserver().evaluate(ctx_none) == []
```

- [ ] **Step 2: Run the tests to confirm they pass**

```bash
pytest tests/test_crypto_observer.py -k settlement_edge -v
```

Expected: 3 tests PASS (happy path + 2 new ones).

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin settlement_edge mts_s window gate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Edge case — skew magnitude below threshold

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

- [ ] **Step 1: Add the below-threshold test**

Append to `tests/test_crypto_observer.py`:

```python
def test_settlement_edge_filters_near_zero_skew():
    """|skew| = 0.05 (below ABS_SKEW_MIN=0.20) → no signal even within window."""
    # ask_size=50, bid_size=55 → skew = (50-55)/105 = -0.0476
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=50, yb_size=55),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=20.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert sigs == [], f"expected no signal at |skew|≈0.05, got: {sigs}"


def test_settlement_edge_fires_exactly_at_skew_threshold():
    """|skew| just over 0.20 fires; just under 0.20 does not."""
    # Just over: ask=120, bid=80 → skew = 40/200 = 0.20 (boundary)
    # We require abs(skew) >= 0.20 to NOT fire — i.e. strict >=
    # Use 0.21 to be safe: ask=121, bid=79 → skew = 42/200 = 0.21
    b_legs_fire = [
        _b(70000, ya=0.50, yb=0.48, ya_size=121, yb_size=79),
    ]
    ctx_fire = _ctx(b_legs=b_legs_fire, spot=70000.0, close_s=20.0)
    sigs_fire = SettlementEdgeObserver().evaluate(ctx_fire)
    assert len(sigs_fire) == 1
    assert sigs_fire[0].predicted_outcome == "no"  # skew > 0 → no

    # Just under: ask=119, bid=81 → skew = 38/200 = 0.19 → no fire
    b_legs_skip = [
        _b(70000, ya=0.50, yb=0.48, ya_size=119, yb_size=81),
    ]
    ctx_skip = _ctx(b_legs=b_legs_skip, spot=70000.0, close_s=20.0)
    assert SettlementEdgeObserver().evaluate(ctx_skip) == []
```

- [ ] **Step 2: Run the tests to confirm they pass**

```bash
pytest tests/test_crypto_observer.py -k settlement_edge -v
```

Expected: 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin settlement_edge skew-magnitude threshold

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Edge case — thin-book (no MM presence)

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

- [ ] **Step 1: Add the thin-book test**

Append to `tests/test_crypto_observer.py`:

```python
def test_settlement_edge_skips_thin_book():
    """ask_size + bid_size ≤ MIN_SIDE_SIZE → no signal (no MM presence)."""
    # All sizes zero
    b_legs_zero = [
        _b(70000, ya=0.50, yb=0.48, ya_size=0, yb_size=0),
    ]
    ctx_zero = _ctx(b_legs=b_legs_zero, spot=70000.0, close_s=15.0)
    assert SettlementEdgeObserver().evaluate(ctx_zero) == []

    # Total exactly at MIN_SIDE_SIZE (1) — still skipped (<=)
    b_legs_min = [
        _b(70000, ya=0.50, yb=0.48, ya_size=1, yb_size=0),
    ]
    ctx_min = _ctx(b_legs=b_legs_min, spot=70000.0, close_s=15.0)
    assert SettlementEdgeObserver().evaluate(ctx_min) == []


def test_settlement_edge_fires_on_negative_skew_predicts_yes():
    """Pure direction-mapping check: skew < 0 → predicted='yes'."""
    # ask=10, bid=100 → skew = -90/110 = -0.818
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=10.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"
    assert sigs[0].features["skew"] < 0
```

- [ ] **Step 2: Run all settlement_edge tests**

```bash
pytest tests/test_crypto_observer.py -k settlement_edge -v
```

Expected: 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin settlement_edge thin-book + direction map

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Register observer in `OBSERVER_CLASSES`

**Files:**
- Modify: `strategies/crypto_observer.py` (1 line in OBSERVER_CLASSES tuple)

Without this step the observer code exists but the harness never instantiates it — no signals are recorded.

- [ ] **Step 1: Append `SettlementEdgeObserver` to OBSERVER_CLASSES**

In `strategies/crypto_observer.py`, locate the `OBSERVER_CLASSES` tuple (line 1194). The current end of the tuple (before the cross-asset extension below it):

```python
OBSERVER_CLASSES: tuple[type[BaseObserver], ...] = (
    TailDecayObserver, AtmFadeObserver, ClusterSumObserver,
    BvTConsistencyObserver, SpotDriftMomentumObserver,
    OpenWideningObserver, DeribitTailSkewObserver, BidImbalanceObserver,
    # Flipped siblings
    OpenNoTouchObserver, AtmHoldObserver, BidImbalanceFadeObserver,
    # 2026-05-18 additions: gap-fill / new-hypothesis observers
    LateCloseTailObserver, DeribitAtmSkewObserver,
    # 2026-05-20: fee-positive zone explorer (paired with late_close_tail)
    MidOtmTailObserver,
    # 2026-05-19 Phase 0 additions: cross-venue Deribit signals
    FundingRateFadeObserver, VolSkewFadeObserver,
    # 2026-05-19 Phase 0: external-data signals (yfinance ETF + CME)
    EtfFlowLagObserver, CmeBasisFadeObserver,
)
```

Modify the `MidOtmTailObserver` comment block to also list `SettlementEdgeObserver`. Replace these two lines:

```python
    # 2026-05-20: fee-positive zone explorer (paired with late_close_tail)
    MidOtmTailObserver,
```

with:

```python
    # 2026-05-20: fee-positive zone explorer (paired with late_close_tail)
    MidOtmTailObserver,
    # 2026-05-20: static micro-timing depth-skew (mts_s ≤ 30, ATM±2)
    SettlementEdgeObserver,
```

- [ ] **Step 2: Add a smoke test that the registry contains the new observer**

Append to `tests/test_crypto_observer.py`:

```python
def test_settlement_edge_registered_in_observer_classes():
    """Harness picks up settlement_edge via OBSERVER_CLASSES tuple."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert SettlementEdgeObserver in OBSERVER_CLASSES
```

- [ ] **Step 3: Run all crypto_observer tests**

```bash
pytest tests/test_crypto_observer.py -v
```

Expected: All tests PASS (including pre-existing ones — should be 8 settlement_edge tests + the rest of the file).

- [ ] **Step 4: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): register settlement_edge in OBSERVER_CLASSES

Wires the observer into the scan-tick harness. Without this commit
SettlementEdgeObserver exists in code but is never instantiated.

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

Expected: All tests PASS. None of the pre-existing tests should be affected (settlement_edge is purely additive).

- [ ] **Step 2: Run a fast grep sanity check**

Confirm the observer is consistently referenced (no typos in observer name):

```bash
grep -rn "settlement_edge\|SettlementEdgeObserver" strategies/crypto_observer.py tests/test_crypto_observer.py
```

Expected output (counts approximate):
- `strategies/crypto_observer.py`: ~4 mentions (class def, name attr, OBSERVER_CLASSES, constants comment block)
- `tests/test_crypto_observer.py`: ~10+ mentions (import, 7+ tests)

- [ ] **Step 3: Confirm observer is wired through the actual harness instantiation**

```bash
python3 -c "from strategies.crypto_observer import OBSERVER_CLASSES, SettlementEdgeObserver; assert SettlementEdgeObserver in OBSERVER_CLASSES; obs = SettlementEdgeObserver(); print(f'name={obs.name!r} ok')"
```

Expected output:
```
name='settlement_edge' ok
```

- [ ] **Step 4: Push when ready**

This step is intentionally not automated — the user pushes when they want the deploy. The systemd service on EC2 picks up the change on the next `git pull && systemctl restart kalshi-bot.service` (per the user's standard deploy flow per [[ec2_deployment]]).

No `git push` here.

---

## Out of scope (per spec)

- Time-series quote-pull velocity tracking (Design B in brainstorm).
- MM-presence gate beyond the `MIN_SIDE_SIZE = 1` floor (Design C).
- Phase 1 trader / market_order routing — gated on 5/27 eval.
- Settlement backfill changes — existing cron picks up the new observer name automatically (queries by `observer != actual_outcome IS NULL`).

---

## Expected commit graph after plan complete

```
* feat(crypto_observer): register settlement_edge in OBSERVER_CLASSES
* test(crypto_observer): pin settlement_edge thin-book + direction map
* test(crypto_observer): pin settlement_edge skew-magnitude threshold
* test(crypto_observer): pin settlement_edge mts_s window gate
* feat(crypto_observer): add settlement_edge Phase 0 observer
* docs(spec): settlement_edge literal observer design (91e38fc, already merged)
```

5 new commits — one per task with TDD discipline.
