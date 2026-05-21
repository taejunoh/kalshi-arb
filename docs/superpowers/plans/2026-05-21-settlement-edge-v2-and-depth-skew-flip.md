# settlement_edge v2 + depth_skew_flip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retune `SettlementEdgeObserver` in-place to v2 semantics (rename + widened window + entry-price gate + tightened skew floor) and add a new sister `DepthSkewFlipObserver` with a per-leg skew cache that detects sign-flip events.

**Architecture:** v2 is a single-file diff on `strategies/crypto_observer.py` — constants block + class body + tests. The new observer adds a `_DepthSkewCache` (mirrors `_BookMidCache` in `crypto_cross_asset_observer.py`), wires it into `CryptoObserverStrategy` and `EventContext`, and registers `DepthSkewFlipObserver` in `OBSERVER_CLASSES`. Both observers stay Phase 0 paper-only.

**Tech Stack:** Python, pytest, sqlite3 (paper signal store). No new external deps.

**Spec:** `docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md`

---

## Task 1: Retune `SettlementEdgeObserver` to v2

**Files:**
- Modify: `strategies/crypto_observer.py:82-91` (constants), `strategies/crypto_observer.py:547-606` (observer class)
- Modify: `tests/test_crypto_observer.py:799-922` (existing settlement_edge tests)

This task migrates v1 tests to v2 semantics first (TDD red), then updates the observer to make them pass (TDD green). New tests for v2-only behavior (entry-price gate, mts_min boundary) are added in the same migration.

- [ ] **Step 1: Rewrite the settlement_edge test block to v2 semantics**

Replace [tests/test_crypto_observer.py:799-922](tests/test_crypto_observer.py#L799-L922) (the entire `# ── settlement_edge observer ──` section through the end of `test_settlement_edge_registered_in_observer_classes`) with the following block:

```python
# ── settlement_edge_v2 observer ──────────────────────────────────


def test_settlement_edge_v2_fires_on_asymmetric_depth_in_atm():
    """ATM bucket in mts_s ∈ [60, 600], strong bid → predicted='yes',
    entry price ya=0.50 in [0.40, 0.85] band → fires."""
    b_legs = [
        _b(69500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
        _b(69750, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70000, ya=0.50, yb=0.48, ya_size=20, yb_size=100),   # ATM
        _b(70250, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    atm_sigs = [s for s in sigs if s.market_id.endswith("B70000")]
    assert len(atm_sigs) == 1
    atm = atm_sigs[0]
    assert atm.observer == "settlement_edge_v2"
    assert atm.predicted_outcome == "yes"
    assert atm.features["skew"] == pytest.approx(-0.667, abs=0.01)
    assert atm.features["mts_s"] == pytest.approx(120.0, abs=0.01)
    assert atm.features["bucket_dist_from_atm"] == 0
    assert atm.features["entry_price"] == pytest.approx(0.50, abs=0.001)


def test_settlement_edge_v2_skips_below_mts_min():
    """mts_s = 45 (below MIN=60) → no fire even with strong skew + valid price."""
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=45.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_skips_above_mts_max():
    """mts_s = 700 (above MAX=600) → no fire."""
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=700.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_skips_at_or_below_zero_mts():
    """mts_s ≤ 0 / None → no signal."""
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100)]
    for cs in (0.0, -5.0, None):
        ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=cs)
        assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_filters_below_skew_floor():
    """|skew| = 0.30 (below new ABS_SKEW_MIN=0.40) → no fire."""
    # ask=130, bid=70, total=200 → skew=+0.30
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=130, yb_size=70)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_fires_at_skew_threshold():
    """|skew| just over 0.40 fires; just under does not."""
    # Just over: ask=141, bid=59, skew=82/200=0.41 → fires (predict 'no')
    b_legs_fire = [_b(70000, ya=0.50, yb=0.48, ya_size=141, yb_size=59)]
    ctx_fire = _ctx(b_legs=b_legs_fire, spot=70000.0, close_s=120.0)
    sigs = SettlementEdgeObserver().evaluate(ctx_fire)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"  # skew > 0 → predicts 'no'
    # Just under: ask=139, bid=61, skew=78/200=0.39 → no fire
    b_legs_skip = [_b(70000, ya=0.50, yb=0.48, ya_size=139, yb_size=61)]
    ctx_skip = _ctx(b_legs=b_legs_skip, spot=70000.0, close_s=120.0)
    assert SettlementEdgeObserver().evaluate(ctx_skip) == []


def test_settlement_edge_v2_skips_thin_book():
    """ya_size + yb_size ≤ MIN_SIDE_SIZE=5 → no fire."""
    # Total = 5 (at boundary, gate is `<=` so skipped)
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=2, yb_size=3)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []
    # Total = 6 (just over) with strong skew should fire
    b_legs2 = [_b(70000, ya=0.50, yb=0.48, ya_size=1, yb_size=5)]
    ctx2 = _ctx(b_legs=b_legs2, spot=70000.0, close_s=120.0)
    sigs = SettlementEdgeObserver().evaluate(ctx2)
    # skew = (1-5)/6 = -0.667, ya=0.50 in band, predicts 'yes'
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"


def test_settlement_edge_v2_skips_entry_price_above_max():
    """Predicted-side price > 0.85 → no fire (would be near-locked)."""
    # Strong negative skew → predicts 'yes', entry = ya = 0.90 (above MAX)
    b_legs = [_b(70000, ya=0.90, yb=0.88, ya_size=10, yb_size=100)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_skips_entry_price_below_min():
    """Predicted-side price < 0.40 → no fire (too far OTM to be alpha)."""
    # Strong negative skew → predicts 'yes', entry = ya = 0.30 (below MIN)
    b_legs = [_b(70000, ya=0.30, yb=0.28, ya_size=10, yb_size=100)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    assert SettlementEdgeObserver().evaluate(ctx) == []


def test_settlement_edge_v2_fires_on_no_side_when_skew_positive():
    """Strong positive skew → predicts 'no', entry = na = 1 - yb."""
    # ask=100, bid=10, skew=+0.818 → predicts 'no', entry = na = 1 - 0.50 = 0.50
    b_legs = [_b(70000, ya=0.52, yb=0.50, ya_size=100, yb_size=10)]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=120.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].features["entry_price"] == pytest.approx(0.50, abs=0.001)


def test_settlement_edge_v2_registered_in_observer_classes():
    """Harness picks up settlement_edge_v2 via OBSERVER_CLASSES tuple."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert SettlementEdgeObserver in OBSERVER_CLASSES
```

- [ ] **Step 2: Run the migrated tests — expect FAIL**

Run: `pytest tests/test_crypto_observer.py -k "settlement_edge_v2" -v`

Expected: most tests FAIL because the observer still uses v1 constants (mts ≤ 30, skew ≥ 0.20, no entry-price gate, name='settlement_edge'). The `test_settlement_edge_v2_skips_at_or_below_zero_mts` test should pass — that behavior is unchanged.

- [ ] **Step 3: Update the constants block**

Replace [strategies/crypto_observer.py:82-91](strategies/crypto_observer.py#L82-L91) (the `# settlement_edge (2026-05-20): ...` block) with:

```python
# settlement_edge_v2 (2026-05-21): retune of v1 after 24h audit showed 93%
# of fires sat at predicted-side $1.00 (untradable). v2 widens the window,
# adds an entry-price gate, and tightens the skew floor. Spec:
# docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md
SETTLEMENT_EDGE_MTS_MIN_S = 60        # exclude collapse zone (<60s = price locked)
SETTLEMENT_EDGE_MTS_MAX_S = 600       # 10-minute pre-close window
SETTLEMENT_EDGE_ATM_RADIUS = 2
SETTLEMENT_EDGE_MIN_SIDE_SIZE = 5     # raised from 1 — phantom guard
SETTLEMENT_EDGE_ABS_SKEW_MIN = 0.40   # raised from 0.20 — v1 cohort 197/201 fires at ≥0.80, floor was non-binding
SETTLEMENT_EDGE_ENTRY_PRICE_MIN = 0.40
SETTLEMENT_EDGE_ENTRY_PRICE_MAX = 0.85
```

- [ ] **Step 4: Update the `SettlementEdgeObserver` class**

Replace [strategies/crypto_observer.py:547-606](strategies/crypto_observer.py#L547-L606) (the entire `class SettlementEdgeObserver` definition) with:

```python
class SettlementEdgeObserver(BaseObserver):
    """Phase 0 v2: per-leg depth-skew on ATM±2 cluster in the [60, 600]s
    pre-close window, gated to entry prices in [0.40, 0.85].

    v1 (mts ≤ 30, skew ≥ 0.20, no price gate) was audited 2026-05-21:
    n=201 settled, 93% of fires at predicted-side $1.00 (untradable),
    tradable cohort (n=14) ROI -0.31% / WR 78.6% vs BE_WR 78.8%. v2 moves
    observation to the zone where the signal exists AND price is still
    tradable. Spec: docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md
    """
    name = "settlement_edge_v2"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs < SETTLEMENT_EDGE_MTS_MIN_S:
            return []
        if ctx.close_time_secs > SETTLEMENT_EDGE_MTS_MAX_S:
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
            entry_price = leg.ya if predicted == "yes" else leg.na
            if entry_price < SETTLEMENT_EDGE_ENTRY_PRICE_MIN:
                continue
            if entry_price > SETTLEMENT_EDGE_ENTRY_PRICE_MAX:
                continue
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
                    "entry_price": round(entry_price, 4),
                },
            ))
        return out
```

- [ ] **Step 5: Run the migrated tests — expect PASS**

Run: `pytest tests/test_crypto_observer.py -k "settlement_edge_v2" -v`

Expected: all 10 `settlement_edge_v2` tests PASS.

- [ ] **Step 6: Run the full crypto_observer test suite for regression**

Run: `pytest tests/test_crypto_observer.py -v`

Expected: all tests PASS. If any other test references `settlement_edge` (e.g. `test_strike_ladder_literal_excludes_settlement_edge_window` — locate by grep), confirm it still passes — `LITERAL_MTS_MIN_S = 60` (find in crypto_observer.py by grep) already matches `SETTLEMENT_EDGE_MTS_MIN_S = 60`, so no other code needs updating.

- [ ] **Step 7: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(settlement_edge): v2 retune — mts [60,600] + entry price gate + tightened skew floor

v1 24h audit (n=201) showed 93% of fires at predicted-side $1.00 (untradable);
tradable cohort (n=14) ROI -0.31% vs BE_WR 78.8%. v2 moves observation window
to [60, 600]s, adds entry-price gate [0.40, 0.85], raises skew floor to 0.40
(v1 floor 0.20 was non-binding — 197/201 fires already ≥0.80), raises
min_side_size 1 → 5 for phantom guard. Observer renamed to settlement_edge_v2
for clean cohort separation in phase0 audits.

Spec: docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `_DepthSkewCache` class

**Files:**
- Modify: `strategies/crypto_observer.py` (add new helper class near `_BookMidCache` import site, around line 1668)
- Modify: `tests/test_crypto_observer.py` (add cache test block, after the existing observer tests)

Mirrors `_BookMidCache` exactly — same `(series, strike) → deque[(ts, value)]` shape, same `update`/`n_seconds_ago`/`prune_stale`/`size` API, same hard-cap behavior. The value stored is skew (signed float in [-1.0, +1.0]) instead of mid (positive float in [0, 1]).

- [ ] **Step 1: Write failing tests for `_DepthSkewCache`**

Add the following test block at the end of [tests/test_crypto_observer.py](tests/test_crypto_observer.py) (after the last existing test):

```python
# ── _DepthSkewCache ───────────────────────────────────────────────


def test_depth_skew_cache_update_and_get_now():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    cache.update("KXBTC", 90250.0, 1000.0, -0.45)
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1000.0) == -0.45


def test_depth_skew_cache_n_seconds_ago():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    cache.update("KXBTC", 90250.0, 1000.0, -0.40)
    cache.update("KXBTC", 90250.0, 1015.0,  0.10)
    cache.update("KXBTC", 90250.0, 1030.0, +0.47)
    # 30s ago from t=1030 → most recent entry with ts <= 1000
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1030.0) == -0.40


def test_depth_skew_cache_returns_none_on_data_gap():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    cache.update("KXBTC", 90250.0, 1000.0, -0.40)
    # 30s ago from t=1100 → target=1070, nearest sample 70s away > 2*30 guard
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1100.0) is None


def test_depth_skew_cache_returns_none_on_missing_key():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    assert cache.n_seconds_ago("KXETH", 2120.0, 30.0, 1000.0) is None


def test_depth_skew_cache_rolling_maxlen():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    for i in range(70):
        # values bounce around 0 to verify signed storage
        cache.update("KXBTC", 90250.0, float(i), (float(i) - 35) * 0.01)
    # Oldest survivor should be ts=10, value=(10-35)*0.01=-0.25
    assert cache.n_seconds_ago("KXBTC", 90250.0, 59.0, 69.0) == pytest.approx(-0.25)
    # ts=0 should be gone
    assert cache.n_seconds_ago("KXBTC", 90250.0, 70.0, 69.0) is None


def test_depth_skew_cache_prune_stale_keys():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache()
    cache.update("KXBTC", 90250.0, 1000.0, -0.40)
    cache.update("KXBTC", 90350.0, 1100.0,  0.50)
    removed = cache.prune_stale(now_ts=1200.0, max_age_s=120.0)
    assert removed == 1
    assert cache.size() == 1
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1200.0) is None
    assert cache.n_seconds_ago("KXBTC", 90350.0, 0.0, 1200.0) is not None


def test_depth_skew_cache_hard_cap_flush():
    from strategies.crypto_observer import _DepthSkewCache
    cache = _DepthSkewCache(hard_cap=5)
    for i in range(6):
        cache.update("KXBTC", float(90000 + i * 100), 1000.0 + i, 0.4)
    assert cache.size() == 0  # flushed
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_crypto_observer.py -k "depth_skew_cache" -v`

Expected: all 7 tests FAIL with `ImportError: cannot import name '_DepthSkewCache'`.

- [ ] **Step 3: Implement `_DepthSkewCache`**

Add the following class **immediately after** the existing import `from strategies.crypto_cross_asset_observer import _BookMidCache` line (currently at [strategies/crypto_observer.py:1668](strategies/crypto_observer.py#L1668)) — i.e. just above `self._book_mid_cache = _BookMidCache()`. Actually, place it at module scope, not inside `__init__`. Find a clean insertion point at module top level near other utility classes (around [strategies/crypto_observer.py:330](strategies/crypto_observer.py#L330), just before the `@dataclass class Leg:` definition), and add:

```python
class _DepthSkewCache:
    """Rolling (series, strike) → deque[(ts, skew)] for per-leg depth sign-flip detection.

    Mirrors _BookMidCache (strategies/crypto_cross_asset_observer.py) but stores
    signed skew in [-1.0, +1.0] instead of mid in [0, 1]. Same lookback / prune /
    hard-cap semantics.
    """

    DEQUE_MAXLEN = 60
    DEFAULT_HARD_CAP = 20_000
    STALE_GUARD_MULTIPLIER = 2.0

    def __init__(self, hard_cap: int = DEFAULT_HARD_CAP):
        self._cache: dict[tuple[str, float], deque] = {}
        self._hard_cap = hard_cap

    def update(self, series: str, strike: float, ts: float, skew: float) -> bool:
        flushed = False
        if (series, strike) not in self._cache and len(self._cache) >= self._hard_cap:
            logger.error(
                f"_DepthSkewCache hard cap {self._hard_cap} reached — flushing all "
                f"entries. This indicates pruning is broken."
            )
            self._cache.clear()
            flushed = True
        if flushed:
            return flushed
        key = (series, strike)
        dq = self._cache.get(key)
        if dq is None:
            dq = deque(maxlen=self.DEQUE_MAXLEN)
            self._cache[key] = dq
        dq.append((ts, skew))
        return flushed

    def n_seconds_ago(self, series: str, strike: float, lookback_s: float,
                      now_ts: float) -> Optional[float]:
        dq = self._cache.get((series, strike))
        if not dq:
            return None
        target = now_ts - lookback_s
        if lookback_s > 0:
            max_gap = self.STALE_GUARD_MULTIPLIER * lookback_s
        else:
            max_gap = 120.0
        best_ts = None
        best_skew = None
        for ts, skew in dq:
            if ts <= target:
                best_ts = ts
                best_skew = skew
            else:
                break
        if best_ts is None:
            return None
        if (target - best_ts) > max_gap:
            return None
        return best_skew

    def prune_stale(self, now_ts: float, max_age_s: float = 120.0) -> int:
        to_drop: list[tuple[str, float]] = []
        for key, dq in self._cache.items():
            if not dq:
                to_drop.append(key)
                continue
            last_ts, _ = dq[-1]
            if (now_ts - last_ts) > max_age_s:
                to_drop.append(key)
        for key in to_drop:
            del self._cache[key]
        return len(to_drop)

    def size(self) -> int:
        return len(self._cache)
```

Verify the imports `deque`, `logger`, `Optional` are already imported at the top of [strategies/crypto_observer.py](strategies/crypto_observer.py). If `deque` is not yet imported at module scope, add `from collections import deque` to the existing imports.

- [ ] **Step 4: Run cache tests — expect PASS**

Run: `pytest tests/test_crypto_observer.py -k "depth_skew_cache" -v`

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): _DepthSkewCache helper (BookMidCache pattern)

Per-(series, strike) rolling cache of signed skew values for sign-flip
detection. Mirrors _BookMidCache semantics: deque maxlen 60 (~60s @ 1Hz),
hard cap 20k keys, prune_stale + STALE_GUARD_MULTIPLIER lookback guard.
Prerequisite for DepthSkewFlipObserver.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire `_DepthSkewCache` into `CryptoObserverStrategy` + `EventContext`

**Files:**
- Modify: `strategies/crypto_observer.py:370` (EventContext field)
- Modify: `strategies/crypto_observer.py:1669-1670` (cache instantiation in `__init__`)
- Modify: `strategies/crypto_observer.py:1803-1815` (pass cache to EventContext at construction)
- Modify: `strategies/crypto_observer.py:1836-1873` (per-tick update + prune + size-warn)

No new tests in this task — the cache is plumbed through the strategy but exercised only by `DepthSkewFlipObserver` in Task 4. After this task, the cache exists in memory and gets populated on every scan tick, but no observer reads from it yet.

- [ ] **Step 1: Add `depth_skew_cache` field to `EventContext`**

In [strategies/crypto_observer.py:370](strategies/crypto_observer.py#L370), find the line:

```python
    book_mid_cache: Optional[object] = None  # _BookMidCache, untyped to avoid circular import
```

Replace it with:

```python
    book_mid_cache: Optional[object] = None  # _BookMidCache, untyped to avoid circular import
    depth_skew_cache: Optional[object] = None  # _DepthSkewCache, untyped to mirror book_mid_cache
```

- [ ] **Step 2: Instantiate `_DepthSkewCache` in `CryptoObserverStrategy.__init__`**

In [strategies/crypto_observer.py:1667-1670](strategies/crypto_observer.py#L1667-L1670), find:

```python
        # 2026-05-20: rolling bucket-mid cache for kalshi_book_lag observer.
        from strategies.crypto_cross_asset_observer import _BookMidCache
        self._book_mid_cache = _BookMidCache()
        self._last_book_prune_ts = 0.0
```

Replace with:

```python
        # 2026-05-20: rolling bucket-mid cache for kalshi_book_lag observer.
        from strategies.crypto_cross_asset_observer import _BookMidCache
        self._book_mid_cache = _BookMidCache()
        self._last_book_prune_ts = 0.0
        # 2026-05-21: per-leg skew cache for depth_skew_flip observer (sister of settlement_edge_v2).
        self._depth_skew_cache = _DepthSkewCache()
        self._last_depth_skew_prune_ts = 0.0
```

- [ ] **Step 3: Pass cache to `EventContext`**

In [strategies/crypto_observer.py:1803](strategies/crypto_observer.py#L1803), find the `contexts.append(EventContext(...))` block. Locate the line:

```python
                book_mid_cache=self._book_mid_cache,
```

(this line is just below the `corr_5min=corr_5min, corr_15min=corr_15min,` line in the same EventContext construction — find it by grepping for `book_mid_cache=self._book_mid_cache`).

Add immediately after it:

```python
                depth_skew_cache=self._depth_skew_cache,
```

- [ ] **Step 4: Update cache on every market tick (alongside book_mid_cache update)**

In [strategies/crypto_observer.py:1841-1857](strategies/crypto_observer.py#L1841-L1857), find the existing block:

```python
            ya = _safe_float(m.get("yes_ask_dollars"))
            yb = _safe_float(m.get("yes_bid_dollars"))
            if ya is None or yb is None:
                continue
            suffix = t.rsplit("-", 1)[-1]
            kind = _suffix_kind(suffix)
            if kind != "bucketed":
                continue
            strike = _parse_suffix_threshold(suffix)
            if strike is None:
                continue
            mid = (ya + yb) / 2.0
            flushed = self._book_mid_cache.update(series, strike, now_epoch, mid)
            if flushed:
                logger.error(
                    "[BOOK_MID_CACHE_FLUSH] hard cap hit — alert ops"
                )
```

Add immediately after the `if flushed: ... ops")` line (still inside the `for m in markets:` loop):

```python
            # 2026-05-21: per-leg skew update for depth_skew_flip observer.
            ya_size = _safe_float(m.get("yes_ask_size_fp")) or 0.0
            yb_size = _safe_float(m.get("yes_bid_size_fp")) or 0.0
            total_size = ya_size + yb_size
            if total_size > 0:
                skew = (ya_size - yb_size) / total_size
                flushed_skew = self._depth_skew_cache.update(
                    series, strike, now_epoch, skew,
                )
                if flushed_skew:
                    logger.error(
                        "[DEPTH_SKEW_CACHE_FLUSH] hard cap hit — alert ops"
                    )
```

- [ ] **Step 5: Add prune + size-warn alongside book_mid_cache**

In [strategies/crypto_observer.py:1858-1873](strategies/crypto_observer.py#L1858-L1873), find:

```python
        # Prune stale book entries every 60s
        if (now_epoch - self._last_book_prune_ts) >= 60.0:
            removed = self._book_mid_cache.prune_stale(now_epoch, max_age_s=120.0)
            self._last_book_prune_ts = now_epoch
            if debug:
                logger.warning(
                    f"[BOOK_MID_CACHE_PRUNE] size={self._book_mid_cache.size()} "
                    f"removed={removed}"
                )
        # Soft watchdog
        if self._book_mid_cache.size() > 10_000:
            if debug:
                logger.warning(
                    f"[BOOK_MID_CACHE_SIZE_WARN] size={self._book_mid_cache.size()} "
                    f"approaching hard cap"
                )
```

Add immediately after the `[BOOK_MID_CACHE_SIZE_WARN]` block (closing brace of the second `if`):

```python
        # 2026-05-21: parallel prune + watchdog for depth_skew_cache
        if (now_epoch - self._last_depth_skew_prune_ts) >= 60.0:
            removed_skew = self._depth_skew_cache.prune_stale(now_epoch, max_age_s=120.0)
            self._last_depth_skew_prune_ts = now_epoch
            if debug:
                logger.warning(
                    f"[DEPTH_SKEW_CACHE_PRUNE] size={self._depth_skew_cache.size()} "
                    f"removed={removed_skew}"
                )
        if self._depth_skew_cache.size() > 10_000:
            if debug:
                logger.warning(
                    f"[DEPTH_SKEW_CACHE_SIZE_WARN] size={self._depth_skew_cache.size()} "
                    f"approaching hard cap"
                )
```

- [ ] **Step 6: Run the full crypto_observer test suite for regression**

Run: `pytest tests/test_crypto_observer.py -v`

Expected: all tests PASS. No new test failures from the EventContext field addition (the default is None so existing test fixtures work unchanged).

- [ ] **Step 7: Commit**

```bash
git add strategies/crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): wire _DepthSkewCache through CryptoObserverStrategy + EventContext

Cache populated per market tick (alongside book_mid_cache update),
pruned every 60s, soft-watchdog at 10k keys, hard cap 20k. No observer
reads from it yet — wiring-only prep for DepthSkewFlipObserver.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `DepthSkewFlipObserver` + registration

**Files:**
- Modify: `strategies/crypto_observer.py` (add constants + class, register in `OBSERVER_CLASSES`)
- Modify: `tests/test_crypto_observer.py` (add observer tests)

The observer fires when a leg's current skew has opposite sign to its skew `LOOKBACK_S` seconds ago, with both endpoints above the magnitude floor. Predicted side follows the CURRENT skew sign (the new direction).

- [ ] **Step 1: Write failing tests for `DepthSkewFlipObserver`**

Add the following test block at the end of [tests/test_crypto_observer.py](tests/test_crypto_observer.py) (after the `_DepthSkewCache` tests from Task 2):

```python
# ── DepthSkewFlipObserver ─────────────────────────────────────────


def _ctx_with_skew_cache(*, b_legs, spot, close_s, cache):
    """EventContext with a pre-loaded _DepthSkewCache. ctx.now uses module-level NOW;
    cache entries must be stamped at NOW.timestamp() - 30.0 to align with the
    observer's `ctx.now.timestamp()` lookup at LOOKBACK_S=30."""
    from strategies.crypto_observer import EventContext
    return EventContext(
        series="KXBTC", asset="BTC", event_key="26MAY1310",
        now=NOW, spot=spot, spot_60s_ago=None, deribit_iv=None,
        b_legs=b_legs, t_legs=[],
        open_time_secs=None, close_time_secs=close_s,
        depth_skew_cache=cache,
    )


def test_depth_skew_flip_no_fire_on_cache_miss():
    """Empty cache → no fire even with strong current skew."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=20, yb_size=100)]  # skew=-0.667
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=_DepthSkewCache(),
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_no_fire_when_same_sign():
    """skew_old=-0.50, skew_now=-0.70 → same sign, no flip."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, -0.50)
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=15, yb_size=85)]  # skew=-0.70
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=cache,
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_fires_on_clean_sign_flip():
    """skew_old=-0.40, skew_now=+0.50 → flip with both ≥ 0.20 magnitude.
    Predicted side follows current skew sign (positive → 'no')."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, -0.40)
    # Current: ya_size=150, yb_size=50 → skew=+0.50
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=150, yb_size=50)]
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=cache,
    )
    sigs = DepthSkewFlipObserver().evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].observer == "depth_skew_flip"
    assert sigs[0].predicted_outcome == "no"  # current skew > 0 → predicts 'no'
    assert sigs[0].features["skew_now"] == pytest.approx(0.50, abs=0.01)
    assert sigs[0].features["skew_old"] == pytest.approx(-0.40, abs=0.01)
    assert sigs[0].features["lookback_s"] == 30.0


def test_depth_skew_flip_skips_when_old_magnitude_below_floor():
    """skew_old=-0.15 (below MIN_BEFORE=0.20), skew_now=+0.50 → no fire."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, -0.15)
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=150, yb_size=50)]  # +0.50
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=cache,
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_skips_when_current_magnitude_below_floor():
    """skew_old=-0.50, skew_now=+0.15 (below MIN_AFTER=0.20) → no fire."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, -0.50)
    # ya_size=115, yb_size=85 → skew=+0.15
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=115, yb_size=85)]
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=cache,
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_respects_mts_min():
    """Even valid flip → no fire if mts_s < 60 (below MIN)."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, -0.40)
    b_legs = [_b(70000, ya=0.50, yb=0.48, ya_size=150, yb_size=50)]
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=45.0, cache=cache,  # mts=45 < 60
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_respects_entry_price_gate():
    """Valid flip but entry price 0.30 (below MIN=0.40) → no fire."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, _DepthSkewCache,
    )
    cache = _DepthSkewCache()
    cache.update("KXBTC", 70000.0, NOW.timestamp() - 30.0, +0.40)
    # Negative current skew → predicts 'yes', entry = ya = 0.30 < MIN
    b_legs = [_b(70000, ya=0.30, yb=0.28, ya_size=50, yb_size=150)]  # -0.50
    ctx = _ctx_with_skew_cache(
        b_legs=b_legs, spot=70000.0, close_s=120.0, cache=cache,
    )
    assert DepthSkewFlipObserver().evaluate(ctx) == []


def test_depth_skew_flip_registered_in_observer_classes():
    """Harness picks up depth_skew_flip via OBSERVER_CLASSES tuple."""
    from strategies.crypto_observer import (
        DepthSkewFlipObserver, OBSERVER_CLASSES,
    )
    assert DepthSkewFlipObserver in OBSERVER_CLASSES
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_crypto_observer.py -k "depth_skew_flip" -v`

Expected: all 8 tests FAIL with `ImportError: cannot import name 'DepthSkewFlipObserver'`.

- [ ] **Step 3: Add constants block**

In [strategies/crypto_observer.py](strategies/crypto_observer.py), find the `# strike_ladder_literal (2026-05-20):` block at line 157-168. Add immediately after `LITERAL_MTS_MIN_S = 60` (line 168):

```python

# depth_skew_flip (2026-05-21): sister of settlement_edge_v2 (static).
# Fires on per-leg depth-skew sign-flip events over a LOOKBACK_S rolling
# window. Tests the dynamic counterpart: the *moment* MMs reverse a
# lopsided book may carry alpha distinct from the steady-state skew.
# Spec: docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md
DEPTH_SKEW_FLIP_LOOKBACK_S = 30
DEPTH_SKEW_FLIP_MIN_BEFORE = 0.20
DEPTH_SKEW_FLIP_MIN_AFTER  = 0.20
DEPTH_SKEW_FLIP_MTS_MIN_S = 60
DEPTH_SKEW_FLIP_MTS_MAX_S = 600
DEPTH_SKEW_FLIP_ATM_RADIUS = 2
DEPTH_SKEW_FLIP_MIN_SIDE_SIZE = 5
DEPTH_SKEW_FLIP_ENTRY_PRICE_MIN = 0.40
DEPTH_SKEW_FLIP_ENTRY_PRICE_MAX = 0.85
```

- [ ] **Step 4: Add `DepthSkewFlipObserver` class**

Add the following class **immediately after** `SettlementEdgeObserver` (which currently ends at [strategies/crypto_observer.py:606](strategies/crypto_observer.py#L606), but will be slightly different after Task 1 — locate it by `class SettlementEdgeObserver` and place the new class right after its closing `return out`):

```python


# ── 3c. depth_skew_flip ────────────────────────────────────────────


class DepthSkewFlipObserver(BaseObserver):
    """Phase 0: per-leg depth-skew sign-flip detector. Sister of
    SettlementEdgeObserver (static state) but fires on the dynamic event
    of a sign change in the per-leg (ya_size, yb_size) asymmetry over
    LOOKBACK_S seconds. Predicted direction follows the CURRENT skew sign
    (latest information dominates).

    Requires `ctx.depth_skew_cache` populated by CryptoObserverStrategy.
    """
    name = "depth_skew_flip"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs < DEPTH_SKEW_FLIP_MTS_MIN_S:
            return []
        if ctx.close_time_secs > DEPTH_SKEW_FLIP_MTS_MAX_S:
            return []
        if ctx.depth_skew_cache is None:
            return []
        if not ctx.b_legs:
            return []
        atm_idx = ctx.atm_bucket_index()
        if atm_idx is None:
            return []

        lo = max(0, atm_idx - DEPTH_SKEW_FLIP_ATM_RADIUS)
        hi = min(len(ctx.b_legs), atm_idx + DEPTH_SKEW_FLIP_ATM_RADIUS + 1)
        now_ts = ctx.now.timestamp()

        out: list[Signal] = []
        for i in range(lo, hi):
            leg = ctx.b_legs[i]
            total_size = leg.ya_size + leg.yb_size
            if total_size <= DEPTH_SKEW_FLIP_MIN_SIDE_SIZE:
                continue
            skew_now = (leg.ya_size - leg.yb_size) / total_size
            if abs(skew_now) < DEPTH_SKEW_FLIP_MIN_AFTER:
                continue
            skew_old = ctx.depth_skew_cache.n_seconds_ago(
                ctx.series, leg.threshold,
                DEPTH_SKEW_FLIP_LOOKBACK_S, now_ts,
            )
            if skew_old is None:
                continue
            if abs(skew_old) < DEPTH_SKEW_FLIP_MIN_BEFORE:
                continue
            # Sign-flip check (strict — same sign or either-zero blocks)
            if (skew_old > 0) == (skew_now > 0):
                continue
            predicted = "no" if skew_now > 0 else "yes"
            entry_price = leg.ya if predicted == "yes" else leg.na
            if entry_price < DEPTH_SKEW_FLIP_ENTRY_PRICE_MIN:
                continue
            if entry_price > DEPTH_SKEW_FLIP_ENTRY_PRICE_MAX:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome=predicted,
                features={
                    "skew_now": round(skew_now, 4),
                    "skew_old": round(skew_old, 4),
                    "lookback_s": float(DEPTH_SKEW_FLIP_LOOKBACK_S),
                    "ask_size": leg.ya_size,
                    "bid_size": leg.yb_size,
                    "ya": round(leg.ya, 4),
                    "yb": round(leg.yb, 4),
                    "na": round(leg.na, 4),
                    "mts_s": round(ctx.close_time_secs, 2),
                    "bucket_dist_from_atm": i - atm_idx,
                    "spot": ctx.spot,
                    "strike": leg.threshold,
                    "entry_price": round(entry_price, 4),
                },
            ))
        return out
```

- [ ] **Step 5: Register in `OBSERVER_CLASSES`**

In [strategies/crypto_observer.py:1575](strategies/crypto_observer.py#L1575), find the `OBSERVER_CLASSES = (` tuple. Locate the line:

```python
    SettlementEdgeObserver,
```

Add immediately after it:

```python
    DepthSkewFlipObserver,
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_crypto_observer.py -k "depth_skew_flip" -v`

Expected: all 8 tests PASS.

- [ ] **Step 7: Run full crypto_observer test suite for regression**

Run: `pytest tests/test_crypto_observer.py -v`

Expected: all tests PASS — both the settlement_edge_v2 retune tests (Task 1), the _DepthSkewCache tests (Task 2), and the new depth_skew_flip observer tests.

- [ ] **Step 8: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): DepthSkewFlipObserver — sister of settlement_edge_v2

Dynamic counterpart to settlement_edge_v2: fires on per-leg depth-skew
sign-flip events over a 30s rolling window. Both endpoints must have
|skew| ≥ 0.20. Predicted direction follows current skew sign. Shares the
mts [60, 600] and entry-price [0.40, 0.85] gates with v2 for direct
cohort comparability. Phase 0 paper-only; 1-week eval 2026-05-28.

Justification for separate observer (vs widening v2's gate): per
feedback-sister-strategy-disjoint-signal — static-state and dynamic-event
are disjoint signal classes, each gets independent paper accountability.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes (for the executing engineer)

1. **Spec coverage check** before declaring done:
   - settlement_edge_v2 constants, observer body, rename → Task 1
   - `_DepthSkewCache` (BookMidCache pattern) → Task 2
   - EventContext field + strategy wiring + per-tick update + prune → Task 3
   - `DepthSkewFlipObserver` + constants + registration → Task 4
   - Test plan items 1-12 (v2) + cache 1-4 + flip 1-8 are all covered across Tasks 1-4
   - `scripts/phase0_observer_eval_alarm.py` change is **out of scope** for this plan — it lives in the T+7d (2026-05-28) eval ticket, not the deploy.

2. **What this plan does NOT touch:**
   - `monitoring/dashboard/*` — no Phase 0 dashboard changes (settlement_edge had no special wiring)
   - `LIVE_PROMOTABLE_OBSERVERS` — both observers stay Phase 0
   - Systemd timers — no new alarms (existing `kalshi-phase0-observer-eval` covers cross-venue observers only; depth_skew_flip + settlement_edge_v2 are reviewed manually at T+7d)
   - v1 `settlement_edge` DB rows — left untouched for historical lookup. Observer rename gives clean cohort separation.

3. **Possible gotcha — `ctx.now.timestamp()`:** the flip observer reads `ctx.now.timestamp()` for the cache lookup. In the test fixtures, `_ctx_with_skew_cache` uses the top-of-file `NOW` constant for `ctx.now`. The cache update timestamp in tests must be `NOW.timestamp() - 30.0` (not an arbitrary `1000.0`) so the lookback math aligns. The "fires_on_clean_sign_flip" test demonstrates this — copy the pattern in any new tests added later.

4. **Possible gotcha — `_safe_float` and size fields:** `_safe_float` returns `None` for unparseable inputs. The `update_loop` already handles `ya/yb None` with `continue` (line 1843). For sizes, the new lines use `or 0.0` — when `_safe_float` returns `None`, the `or` defaults to `0.0`, then `total_size = 0` skips the update. This is intentional.

5. **Reading order:** Tasks 1 → 2 → 3 → 4 in strict order. Task 4 imports `_DepthSkewCache` (Task 2) and depends on `EventContext.depth_skew_cache` (Task 3). Do not parallelize.
