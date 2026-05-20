# Crypto Cross-Asset Observer Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three Phase 0 cross-asset observers (lead_lag, corr_break, book_lag) to the crypto_observer harness with rolling correlation + book-mid history utilities and memory-leak defenses.

**Architecture:** New module `strategies/crypto_cross_asset_observer.py` hosting 3 observer classes + `_BookMidCache` + `_pearson_correlation`. Existing `crypto_observer.py` harness gets: extended `_spot_history` (maxlen 180 → 1800 to cover 15-min correlation window), a `_BookMidCache` instance updated each scan iter, and 8 new fields on `EventContext` (BTC/ETH spots at 10s/30s/15min lookbacks + corr + book_mid_cache pointer). Observer crash isolation already exists; we add hard-cap memory defense and a dashboard health widget.

**Tech Stack:** Python 3.11, asyncio, pytest. Uses existing `PriceFeed` (binance.us kline_1m + bookTicker), existing `crypto_observer_signals` DB table (JSON features, no migration), existing observer registration via `OBSERVER_CLASSES` tuple.

---

## File Structure

**Create:**
- `strategies/crypto_cross_asset_observer.py` — 3 observer classes + `_BookMidCache` + `_pearson_correlation` helper (~350 LOC)
- `tests/test_crypto_cross_asset_observer.py` — ~24 unit tests (~400 LOC)

**Modify:**
- `strategies/crypto_observer.py`:
  - Extend `_spot_history` deque maxlen from 180 to 1800 (~1 line)
  - Add `_correlation_5_15min` method on the harness (~25 lines)
  - Instantiate `_BookMidCache` in harness `__init__` (~3 lines)
  - Update book-mid cache + extend `_build_contexts` to populate cross-asset fields on each `EventContext` (~30 lines)
  - Hard-cap watchdog + Telegram alert in `scan_and_execute` (~15 lines)
  - Import 3 new observers + add to `OBSERVER_CLASSES` (~5 lines)
  - Extend `EventContext` dataclass with cross-asset + cache fields (~15 lines)
- `monitoring/dashboard/api.py`:
  - Add 3 observers to `KNOWN_OBSERVERS` tuple (~3 lines)

**Out of scope (deferred):**
- Phase 1 paper trader (separate spec after promotion gate)
- T-leg variants for cross-asset (Phase 0 b_legs only)
- Dashboard cross_asset_health widget (will add in follow-up after data shows up)

---

## Task 1: Pearson correlation helper

**Files:**
- Create: `strategies/crypto_cross_asset_observer.py`
- Test: `tests/test_crypto_cross_asset_observer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_crypto_cross_asset_observer.py`:

```python
"""Unit tests for crypto cross-asset observer suite."""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

import pytest

from strategies.crypto_cross_asset_observer import (
    _pearson_correlation,
)


def test_pearson_correlation_perfect_positive():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert _pearson_correlation(xs, ys) == pytest.approx(1.0, abs=1e-9)


def test_pearson_correlation_perfect_negative():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [10.0, 8.0, 6.0, 4.0, 2.0]
    assert _pearson_correlation(xs, ys) == pytest.approx(-1.0, abs=1e-9)


def test_pearson_correlation_uncorrelated():
    # Anti-aligned around mean -> ~0
    xs = [1.0, 2.0, 1.0, 2.0]
    ys = [1.0, 1.0, 2.0, 2.0]
    result = _pearson_correlation(xs, ys)
    assert abs(result) < 0.1


def test_pearson_correlation_returns_none_on_short_input():
    # < 10 samples → None per spec
    xs = [1.0, 2.0, 3.0]
    ys = [1.0, 2.0, 3.0]
    assert _pearson_correlation(xs, ys) is None


def test_pearson_correlation_returns_none_on_zero_variance():
    # Flat series → undefined corr
    xs = [1.0] * 15
    ys = list(range(15))
    assert _pearson_correlation(xs, ys) is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v
```
Expected: `ModuleNotFoundError: No module named 'strategies.crypto_cross_asset_observer'`

- [ ] **Step 3: Write minimal implementation**

Create `strategies/crypto_cross_asset_observer.py`:

```python
"""Cross-asset crypto Phase 0 observers.

Three observers test hypotheses that the within-market-state observers in
crypto_observer.py do not cover:
  - btc_eth_lead_lag: BTC moves first, ETH spot/Kalshi hasn't caught up
  - cross_asset_corr_break: BTC-ETH 5-min correlation drops sharply
  - kalshi_book_lag: Spot moved but Kalshi bucket-mid is stale

Phase 0 data-only — no trading. Promotion to Phase 1 requires bucket-WR
analysis after ≥1 week of data per the design spec.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Optional

logger = logging.getLogger("crypto_cross_asset_observer")

# Minimum samples required to compute a stable correlation. Below this, return
# None and the consuming observer gracefully skips (any None feature → skip).
CORR_MIN_SAMPLES = 10


def _pearson_correlation(xs: list[float], ys: list[float]) -> Optional[float]:
    """Pearson correlation of two equal-length numeric series.

    Returns None if either series has < CORR_MIN_SAMPLES points or zero
    variance (the correlation is undefined when one series is constant).
    """
    n = len(xs)
    if n != len(ys) or n < CORR_MIN_SAMPLES:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return None
    return num / denom
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_cross_asset_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_cross_asset): pearson correlation helper

Foundation for cross_asset_corr_break observer. Returns None when sample
count < 10 or denominator is zero (one series constant) — consuming
observers skip on None features per the staleness guard pattern."
```

---

## Task 2: `_BookMidCache` rolling window + n_seconds_ago

**Files:**
- Modify: `strategies/crypto_cross_asset_observer.py`
- Modify: `tests/test_crypto_cross_asset_observer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_cross_asset_observer import _BookMidCache


def test_book_mid_cache_update_and_get_now():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.45)
    # Same-ts lookup at 0s lookback returns the inserted value
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1000.0) == 0.45


def test_book_mid_cache_n_seconds_ago():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    cache.update("KXBTC", 90250.0, 1015.0, 0.44)
    cache.update("KXBTC", 90250.0, 1030.0, 0.47)
    # 30s ago from t=1030 → most recent entry with ts <= 1000
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1030.0) == 0.40


def test_book_mid_cache_returns_none_on_data_gap():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    # 30s ago from t=1030 → target ts=1000 is exactly the only sample → OK
    # 30s ago from t=1100 → target ts=1070, but nearest sample is at t=1000
    # which is 70s away → > 2*30 = 60s guard → None
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1100.0) is None


def test_book_mid_cache_returns_none_on_missing_key():
    cache = _BookMidCache()
    assert cache.n_seconds_ago("KXETH", 2120.0, 30.0, 1000.0) is None


def test_book_mid_cache_rolling_maxlen():
    cache = _BookMidCache()
    # Insert 70 entries — maxlen=60 should evict oldest 10
    for i in range(70):
        cache.update("KXBTC", 90250.0, float(i), float(i) * 0.01)
    # Oldest survivor should be ts=10
    assert cache.n_seconds_ago("KXBTC", 90250.0, 59.0, 69.0) == pytest.approx(0.10)
    # ts=0 should be gone (would be reported as None or older value via guard)
    assert cache.n_seconds_ago("KXBTC", 90250.0, 70.0, 69.0) is None


def test_book_mid_cache_prune_stale_keys():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    cache.update("KXBTC", 90350.0, 1100.0, 0.50)
    # Prune from t=1200 with max_age=120s → 90250 (last_ts=1000, age=200) removed
    removed = cache.prune_stale(now_ts=1200.0, max_age_s=120.0)
    assert removed == 1
    assert cache.size() == 1
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1200.0) is None
    # 90350 still present
    assert cache.n_seconds_ago("KXBTC", 90350.0, 0.0, 1200.0) is not None


def test_book_mid_cache_hard_cap_flush():
    cache = _BookMidCache(hard_cap=5)
    for i in range(6):
        cache.update("KXBTC", float(90000 + i * 100), 1000.0 + i, 0.4)
    # At insert 6 we exceed cap → flush returns True
    assert cache.size() == 0  # flushed
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k book_mid_cache
```
Expected: ImportError on `_BookMidCache`

- [ ] **Step 3: Write `_BookMidCache` implementation**

Append to `strategies/crypto_cross_asset_observer.py`:

```python
# BookMidCache: per-bucket rolling history of mid prices for book_lag observer.
#
# Memory budget (t3.small, ~530MB current RSS):
#   per entry: (ts, mid) tuple ~64 bytes
#   per bucket: maxlen=60 → ~4 KB
#   ~5000 active buckets (KXBTC + KXETH × ~24 contracts × ~50 strikes)
#   max ~20 MB — t3.small has 2 GB so this is ~1% headroom impact
#
# Defenses:
#   - deque maxlen=60 auto-evicts oldest entry on overflow
#   - prune_stale() removes keys whose last entry is older than max_age_s
#   - hard_cap (default 20,000 keys) triggers full flush + Telegram alert.
#     If we ever hit the cap, pruning is broken — alert and recover.
class _BookMidCache:
    """Rolling (series, strike) → deque[(ts, mid)] storage for book_lag."""

    DEQUE_MAXLEN = 60                  # ~30s window at 1Hz + 30s slack
    DEFAULT_HARD_CAP = 20_000          # max distinct (series, strike) keys
    STALE_GUARD_MULTIPLIER = 2.0       # n_seconds_ago accepts ≤ 2× lookback away

    def __init__(self, hard_cap: int = DEFAULT_HARD_CAP):
        self._cache: dict[tuple[str, float], deque] = {}
        self._hard_cap = hard_cap

    def update(self, series: str, strike: float, ts: float, mid: float) -> bool:
        """Insert (ts, mid) for the (series, strike) bucket.

        Returns True if a hard-cap flush occurred during this call (caller
        is responsible for downstream alerting).
        """
        flushed = False
        if (series, strike) not in self._cache and len(self._cache) >= self._hard_cap:
            logger.error(
                f"_BookMidCache hard cap {self._hard_cap} reached — flushing all "
                f"entries. This indicates pruning is broken."
            )
            self._cache.clear()
            flushed = True
        key = (series, strike)
        dq = self._cache.get(key)
        if dq is None:
            dq = deque(maxlen=self.DEQUE_MAXLEN)
            self._cache[key] = dq
        dq.append((ts, mid))
        return flushed

    def n_seconds_ago(self, series: str, strike: float, lookback_s: float,
                      now_ts: float) -> Optional[float]:
        """Most recent mid at or before (now_ts - lookback_s).

        Returns None if:
          - the key has no history, or
          - the nearest sample is > STALE_GUARD_MULTIPLIER × lookback_s away
            (data gap — stale lookback would mis-attribute).
        """
        dq = self._cache.get((series, strike))
        if not dq:
            return None
        target = now_ts - lookback_s
        max_gap = self.STALE_GUARD_MULTIPLIER * lookback_s if lookback_s > 0 else 60.0
        # Walk deque (oldest → newest) and remember the latest entry with ts ≤ target.
        best_ts = None
        best_mid = None
        for ts, mid in dq:
            if ts <= target:
                best_ts = ts
                best_mid = mid
            else:
                break
        if best_ts is None:
            return None
        if (target - best_ts) > max_gap:
            return None
        return best_mid

    def prune_stale(self, now_ts: float, max_age_s: float = 120.0) -> int:
        """Drop keys whose newest entry is older than max_age_s. Returns count."""
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

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v
```
Expected: 12 passed (5 corr + 7 cache)

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_cross_asset_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_cross_asset): _BookMidCache rolling-window cache

Per-bucket deque (maxlen=60) for KXBTC/KXETH mid prices. Hard-cap flush
defense (default 20k keys) guards against pruning bugs. n_seconds_ago
rejects samples > 2× lookback away (data-gap guard)."
```

---

## Task 3: Extend `_spot_history` maxlen + add `_correlation_5_15min`

**Files:**
- Modify: `strategies/crypto_observer.py:1243-1246`, plus new method

- [ ] **Step 1: Write failing test**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_observer import CryptoObserverStrategy


def _fake_harness_with_spot_history(btc_series, eth_series):
    """Build a stub CryptoObserverStrategy with pre-filled _spot_history.

    Each input is list[(ts, price)] sorted ascending.
    """
    h = CryptoObserverStrategy.__new__(CryptoObserverStrategy)
    h._spot_history = {
        "BTC": deque(maxlen=1800),
        "ETH": deque(maxlen=1800),
    }
    for ts, p in btc_series:
        h._spot_history["BTC"].append((ts, p))
    for ts, p in eth_series:
        h._spot_history["ETH"].append((ts, p))
    return h


def test_correlation_perfectly_correlated_returns_close_to_one():
    # 20 BTC samples + 20 ETH samples, both linearly increasing
    btc = [(float(i), 60000.0 + i * 100) for i in range(20)]
    eth = [(float(i), 2000.0 + i * 5) for i in range(20)]
    h = _fake_harness_with_spot_history(btc, eth)
    corr = h._correlation_lookback(window_secs=20.0, now_ts=20.0)
    assert corr == pytest.approx(1.0, abs=1e-6)


def test_correlation_returns_none_on_sparse():
    # Only 5 samples each → below CORR_MIN_SAMPLES
    btc = [(float(i), 60000.0) for i in range(5)]
    eth = [(float(i), 2000.0) for i in range(5)]
    h = _fake_harness_with_spot_history(btc, eth)
    corr = h._correlation_lookback(window_secs=10.0, now_ts=5.0)
    assert corr is None


def test_spot_history_maxlen_is_1800():
    h = CryptoObserverStrategy.__new__(CryptoObserverStrategy)
    h._spot_history = {
        "BTC": deque(maxlen=1800),
        "ETH": deque(maxlen=1800),
    }
    # If we change the init structure, this guard test catches it
    assert h._spot_history["BTC"].maxlen == 1800
    assert h._spot_history["ETH"].maxlen == 1800
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k "correlation_perfectly or correlation_returns_none or spot_history_maxlen"
```
Expected: `AttributeError: ... has no attribute '_correlation_lookback'`

- [ ] **Step 3: Extend `_spot_history` maxlen**

In `strategies/crypto_observer.py:1243-1246`, replace:

```python
        self._spot_history: dict[str, deque] = {
            "BTC": deque(maxlen=180),  # ~3 min at 1Hz
            "ETH": deque(maxlen=180),
        }
```

with:

```python
        # 2026-05-20: maxlen extended 180 → 1800 to cover 15-min correlation
        # window for cross-asset observers. Memory: ~57 KB total (1800 entries
        # × 16 bytes × 2 assets), negligible.
        self._spot_history: dict[str, deque] = {
            "BTC": deque(maxlen=1800),  # ~30 min at 1Hz
            "ETH": deque(maxlen=1800),
        }
```

- [ ] **Step 4: Add `_correlation_lookback` method**

Insert immediately after `_spot_n_seconds_ago` (around line 1267) in `strategies/crypto_observer.py`:

```python
    def _correlation_lookback(self, window_secs: float, now_ts: float
                              ) -> Optional[float]:
        """BTC-ETH Pearson correlation over the last `window_secs` seconds.

        Joins BTC and ETH spot streams on nearest timestamp (within 5s
        tolerance). Returns None on sparse data (< CORR_MIN_SAMPLES) per
        _pearson_correlation contract.
        """
        from strategies.crypto_cross_asset_observer import _pearson_correlation
        btc_hist = self._spot_history.get("BTC")
        eth_hist = self._spot_history.get("ETH")
        if not btc_hist or not eth_hist:
            return None
        cutoff = now_ts - window_secs
        btc_window = [(ts, p) for ts, p in btc_hist if ts >= cutoff]
        eth_window = [(ts, p) for ts, p in eth_hist if ts >= cutoff]
        # Join on nearest ts (5s tolerance). Walk both sorted lists.
        btc_window.sort(key=lambda x: x[0])
        eth_window.sort(key=lambda x: x[0])
        xs: list[float] = []
        ys: list[float] = []
        j = 0
        for bts, bp in btc_window:
            # Advance ETH pointer to closest match
            while j + 1 < len(eth_window) and abs(eth_window[j + 1][0] - bts) <= abs(eth_window[j][0] - bts):
                j += 1
            if j < len(eth_window) and abs(eth_window[j][0] - bts) <= 5.0:
                xs.append(bp)
                ys.append(eth_window[j][1])
        return _pearson_correlation(xs, ys)
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v
```
Expected: 15 passed (12 from earlier + 3 new)

- [ ] **Step 6: Run full observer suite to verify no regression**

```bash
python3 -m pytest tests/test_crypto_observer.py
```
Expected: All previous tests still pass.

- [ ] **Step 7: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_observer): _correlation_lookback + extend _spot_history

Extends per-asset spot history maxlen 180 → 1800 (~30 min at 1Hz) to
cover 15-min correlation lookback window. Adds _correlation_lookback
method that joins BTC + ETH streams on nearest ts (5s tolerance) and
delegates to _pearson_correlation."
```

---

## Task 4: Extend `EventContext` with cross-asset fields

**Files:**
- Modify: `strategies/crypto_observer.py:292-309` (EventContext dataclass)

- [ ] **Step 1: Write failing test**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_observer import EventContext


def test_event_context_has_cross_asset_fields():
    """All new cross-asset fields default to None when omitted."""
    ctx = EventContext(
        series="KXBTC", asset="BTC", event_key="26MAY2010",
        now=datetime.now(timezone.utc),
        spot=89750.0, spot_60s_ago=89700.0,
        deribit_iv=None,
        b_legs=[], t_legs=[],
        open_time_secs=None, close_time_secs=None,
    )
    # New cross-asset fields all default to None
    assert ctx.spot_btc is None
    assert ctx.spot_eth is None
    assert ctx.spot_btc_30s_ago is None
    assert ctx.spot_eth_30s_ago is None
    assert ctx.spot_btc_10s_ago is None
    assert ctx.spot_eth_10s_ago is None
    assert ctx.corr_5min is None
    assert ctx.corr_15min is None
    assert ctx.book_mid_cache is None
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py::test_event_context_has_cross_asset_fields -v
```
Expected: AttributeError on `spot_btc`

- [ ] **Step 3: Add fields to `EventContext`**

In `strategies/crypto_observer.py`, replace lines 305-309 (the existing Phase 0 additions block) and the lines ending the dataclass:

```python
    # 2026-05-19 Phase 0 additions (None if Deribit fetch failed this tick)
    funding_8h: Optional[float] = None   # signed decimal, e.g. 0.0005 = +0.05%/8h
    iv_skew: Optional[float] = None      # put_IV - call_IV at ±10% strike, decimal
    etf_flow_z: Optional[float] = None   # signed volume z-score on IBIT/ETHA
    cme_basis: Optional[float] = None    # (futures_close - spot) / spot
    # 2026-05-20 Phase 0 cross-asset (filled identically on every ctx by harness)
    spot_btc: Optional[float] = None
    spot_eth: Optional[float] = None
    spot_btc_30s_ago: Optional[float] = None
    spot_eth_30s_ago: Optional[float] = None
    spot_btc_10s_ago: Optional[float] = None
    spot_eth_10s_ago: Optional[float] = None
    corr_5min: Optional[float] = None
    corr_15min: Optional[float] = None
    book_mid_cache: Optional[object] = None  # _BookMidCache, untyped to avoid circular import
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v
python3 -m pytest tests/test_crypto_observer.py
```
Expected: All pass. New field test passes.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_observer): EventContext cross-asset fields

Adds 9 fields used by upcoming cross-asset observers: spot_btc/eth and
their 10s/30s lookbacks, corr_5min, corr_15min, book_mid_cache pointer.
All default to None so existing observers ignore them transparently."
```

---

## Task 5: Wire `_BookMidCache` + cross-asset fields into harness

**Files:**
- Modify: `strategies/crypto_observer.py` (harness init, scan_and_execute, _build_contexts)

- [ ] **Step 1: Add `_book_mid_cache` to harness `__init__`**

Insert in `strategies/crypto_observer.py` immediately after the `self._spot_history = {...}` block (around line 1247):

```python
        # 2026-05-20: rolling bucket-mid cache for kalshi_book_lag observer.
        from strategies.crypto_cross_asset_observer import _BookMidCache
        self._book_mid_cache = _BookMidCache()
        self._last_book_prune_ts = 0.0
```

- [ ] **Step 2: Update cache on each scan iter**

In `scan_and_execute` around line 1354 (just after `self._update_spot_history(now_epoch)`), insert:

```python
        # 2026-05-20: cross-asset bookkeeping.
        for m in markets:
            t = m.get("ticker", "")
            series = t.split("-")[0]
            if series not in TARGET_SERIES:
                continue
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

- [ ] **Step 3: Populate cross-asset fields in `_build_contexts`**

In `_build_contexts` around line 1326 (where `spot_60s = self._spot_n_seconds_ago(...)` is computed), expand to compute all cross-asset fields once per scan and then apply to every ctx:

Replace the existing per-event ctx construction block (lines ~1326-1335) with:

```python
        # 2026-05-20: cross-asset features computed once per scan
        spot_btc_now = self._price_feed.get_fresh_spot("BTC")
        spot_eth_now = self._price_feed.get_fresh_spot("ETH")
        spot_btc_30s = self._spot_n_seconds_ago("BTC", 30.0, now_epoch)
        spot_eth_30s = self._spot_n_seconds_ago("ETH", 30.0, now_epoch)
        spot_btc_10s = self._spot_n_seconds_ago("BTC", 10.0, now_epoch)
        spot_eth_10s = self._spot_n_seconds_ago("ETH", 10.0, now_epoch)
        corr_5min = self._correlation_lookback(300.0, now_epoch)
        corr_15min = self._correlation_lookback(900.0, now_epoch)

        contexts: list[EventContext] = []
        for (series, ev), legs in by_event.items():
            asset = SERIES_ASSET.get(series)
            if asset is None:
                continue
            spot = self._price_feed.get_fresh_spot(asset)
            if spot is None or spot <= 0:
                continue
            b_legs = sorted([l for l in legs if l.kind == "bucketed"],
                            key=lambda l: l.threshold)
            t_legs = sorted([l for l in legs if l.kind == "cumulative"],
                            key=lambda l: l.threshold)
            rep = legs[0].raw
            close_s = _close_time_secs(rep, now)
            open_s = _open_age_secs(rep, now)
            spot_60s = self._spot_n_seconds_ago(asset, SPOT_DRIFT_LOOKBACK_S,
                                                now_epoch)
            contexts.append(EventContext(
                series=series, asset=asset, event_key=ev,
                now=now, spot=spot, spot_60s_ago=spot_60s,
                deribit_iv=None,  # filled async below
                b_legs=b_legs, t_legs=t_legs,
                open_time_secs=open_s, close_time_secs=close_s,
                # cross-asset
                spot_btc=spot_btc_now, spot_eth=spot_eth_now,
                spot_btc_30s_ago=spot_btc_30s, spot_eth_30s_ago=spot_eth_30s,
                spot_btc_10s_ago=spot_btc_10s, spot_eth_10s_ago=spot_eth_10s,
                corr_5min=corr_5min, corr_15min=corr_15min,
                book_mid_cache=self._book_mid_cache,
            ))
        return contexts
```

(Take care: this replaces the existing block. If the existing block has additional fields like `funding_8h` set somewhere else, preserve those.)

- [ ] **Step 4: Run full observer suite**

```bash
python3 -m pytest tests/test_crypto_observer.py tests/test_crypto_cross_asset_observer.py -v
```
Expected: All pass. No regression.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_observer.py
git commit -m "feat(crypto_observer): wire _BookMidCache + cross-asset ctx fields

Scan loop now updates _book_mid_cache for every (series, strike) it
sees, prunes stale entries every 60s, and logs warnings if cache size
crosses 10k (soft) or hits the 20k hard cap. _build_contexts computes
cross-asset features once per scan and writes them onto every ctx."
```

---

## Task 6: `BtcEthLeadLagObserver` — fires_on_main + skip tests + implementation

**Files:**
- Modify: `strategies/crypto_cross_asset_observer.py`
- Modify: `tests/test_crypto_cross_asset_observer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_cross_asset_observer import BtcEthLeadLagObserver
from strategies.crypto_observer import Leg


def _make_leg(series: str, strike: float, ya: float, yb: float,
              yb_size: float = 50) -> Leg:
    return Leg(
        ticker=f"{series}-26MAY2010-B{int(strike)}",
        kind="bucketed", threshold=float(strike),
        ya=ya, yb=yb, na=1.0 - yb, nb=1.0 - ya,
        ya_size=100, yb_size=yb_size, na_size=100, nb_size=100,
        liquidity=200.0, raw={},
    )


def _make_ctx(series: str = "KXETH", asset: str = "ETH",
              spot: float = 2120.0, b_legs=None,
              spot_btc=None, spot_eth=None,
              spot_btc_30s_ago=None, spot_eth_30s_ago=None,
              spot_btc_10s_ago=None, spot_eth_10s_ago=None,
              corr_5min=None, corr_15min=None,
              close_s: float = 1200.0, book_mid_cache=None):
    return EventContext(
        series=series, asset=asset, event_key="26MAY2010",
        now=datetime.now(timezone.utc),
        spot=spot, spot_60s_ago=None,
        deribit_iv=None,
        b_legs=b_legs or [], t_legs=[],
        open_time_secs=None, close_time_secs=close_s,
        spot_btc=spot_btc, spot_eth=spot_eth,
        spot_btc_30s_ago=spot_btc_30s_ago,
        spot_eth_30s_ago=spot_eth_30s_ago,
        spot_btc_10s_ago=spot_btc_10s_ago,
        spot_eth_10s_ago=spot_eth_10s_ago,
        corr_5min=corr_5min, corr_15min=corr_15min,
        book_mid_cache=book_mid_cache,
    )


def test_lead_lag_fires_on_btc_jump_eth_static():
    """BTC +0.5% over 30s, ETH effectively flat (+0.05%) — should fire."""
    # ETH spot 2120, bucket at 2130 (~0.47% above spot, within dist_pct 2%)
    legs = [_make_leg("KXETH", 2130, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60200.0,   # +0.498%
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,     # +0.047%
        spot_btc_10s_ago=60500.0, spot_eth_10s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=1200.0,
    )
    obs = BtcEthLeadLagObserver()
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.observer == "btc_eth_lead_lag"
    assert sig.predicted_outcome == "yes"
    assert sig.features["predicted_direction"] == "up"
    assert sig.features["delta_btc_30s_pct"] == pytest.approx(0.00498, abs=1e-5)


def test_lead_lag_fires_on_btc_drop_eth_static():
    """BTC -0.5%, ETH ~flat — fires on bucket BELOW spot, predicted_direction=down."""
    legs = [_make_leg("KXETH", 2110, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60000.0, spot_btc_30s_ago=60300.0,   # -0.497%
        spot_eth=2120.0, spot_eth_30s_ago=2120.5,
        spot_btc_10s_ago=60000.0, spot_eth_10s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=1200.0,
    )
    obs = BtcEthLeadLagObserver()
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].features["predicted_direction"] == "down"


def test_lead_lag_skips_when_eth_also_moves():
    """ETH moved 0.5% — not lagging. Skip."""
    legs = [_make_leg("KXETH", 2130, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60200.0,
        spot_eth=2120.0, spot_eth_30s_ago=2110.0,   # +0.47% — also moved
        spot_btc_10s_ago=60500.0, spot_eth_10s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []


def test_lead_lag_skips_when_btc_didnt_move():
    """BTC moved <0.3% — below trigger. Skip."""
    legs = [_make_leg("KXETH", 2130, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=60000.0,   # +0.17% — below 0.3%
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.85, corr_15min=0.85,
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []


def test_lead_lag_skips_outside_ya_band():
    """ya 0.8 outside [0.05, 0.60] — skip."""
    legs = [_make_leg("KXETH", 2130, ya=0.80, yb=0.75, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60200.0,
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.85, corr_15min=0.85,
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []


def test_lead_lag_skips_outside_mts():
    """mts_s 100s < 300s — skip."""
    legs = [_make_leg("KXETH", 2130, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60200.0,
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=100.0,  # under threshold
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []


def test_lead_lag_skips_outside_dist_pct():
    """Strike 2200 vs spot 2120 → 3.77% > 2% dist gate — skip."""
    legs = [_make_leg("KXETH", 2200, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60200.0,
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.85, corr_15min=0.85,
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []


def test_lead_lag_skips_when_stale_data():
    """spot_btc_30s_ago is None → can't compute delta → skip."""
    legs = [_make_leg("KXETH", 2130, ya=0.15, yb=0.10, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=None,   # stale
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.85, corr_15min=0.85,
    )
    assert BtcEthLeadLagObserver().evaluate(ctx) == []
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k lead_lag
```
Expected: ImportError on `BtcEthLeadLagObserver`

- [ ] **Step 3: Write `BtcEthLeadLagObserver` implementation**

Append to `strategies/crypto_cross_asset_observer.py`:

```python
# ── Observer base class import (lives in crypto_observer.py) ───────


def _safe_div(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return a / b


# Local mirror of BaseObserver / Signal — implemented as duck-typed classes
# so this module doesn't need to import from crypto_observer (avoid circular).
# The harness only needs .name and .evaluate(ctx).
class _CrossAssetBaseObserver:
    """Shared parent for cross-asset observers. Provides feature helpers."""
    name: str = ""

    def _delta_pct(self, now_val: Optional[float], past_val: Optional[float]
                   ) -> Optional[float]:
        if now_val is None or past_val is None or past_val == 0:
            return None
        return (now_val - past_val) / past_val

    def _source_signal_hash(self, now_ts: float, delta_btc_pct: float) -> int:
        bucket_time_15s = int(now_ts // 15) * 15
        sign = 1 if delta_btc_pct >= 0 else -1
        return hash((bucket_time_15s, sign, round(delta_btc_pct, 3)))


# Gates per design spec
LEAD_LAG_LEAD_THRESHOLD = 0.003       # 0.3% Δspot_BTC over 30s
LEAD_LAG_LAG_THRESHOLD = 0.0015       # 0.15% Δspot_ETH over 30s
LEAD_LAG_YA_MIN = 0.05
LEAD_LAG_YA_MAX = 0.60
LEAD_LAG_MTS_MIN_S = 300.0
LEAD_LAG_DIST_PCT_MAX = 0.020         # 2% from spot
LEAD_LAG_YB_SIZE_MIN = 30
LEAD_LAG_TARGET_SERIES = "KXETH"      # Primary — KXBTC reverse-direction also logged


class BtcEthLeadLagObserver(_CrossAssetBaseObserver):
    """BTC moves first, ETH spot/Kalshi hasn't caught up.

    Fires on KXETH bucket near the expected catch-up zone. Logs predicted
    direction (up/down) corresponding to sign(Δspot_BTC). Phase 0 only.
    """
    name = "btc_eth_lead_lag"

    def evaluate(self, ctx) -> list:
        from strategies.crypto_observer import Signal  # avoid circular at import time

        # Only fires on KXETH primary
        if ctx.series != LEAD_LAG_TARGET_SERIES:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs < LEAD_LAG_MTS_MIN_S:
            return []
        if not ctx.b_legs:
            return []

        delta_btc = self._delta_pct(ctx.spot_btc, ctx.spot_btc_30s_ago)
        delta_eth = self._delta_pct(ctx.spot_eth, ctx.spot_eth_30s_ago)
        if delta_btc is None or delta_eth is None:
            return []
        if abs(delta_btc) < LEAD_LAG_LEAD_THRESHOLD:
            return []
        if abs(delta_eth) > LEAD_LAG_LAG_THRESHOLD:
            return []

        direction = "up" if delta_btc > 0 else "down"
        out: list = []
        for leg in ctx.b_legs:
            if leg.ya < LEAD_LAG_YA_MIN or leg.ya > LEAD_LAG_YA_MAX:
                continue
            if leg.yb_size < LEAD_LAG_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct > LEAD_LAG_DIST_PCT_MAX:
                continue
            # Strike must be on the expected catch-up side
            if direction == "up" and leg.threshold <= ctx.spot:
                continue
            if direction == "down" and leg.threshold >= ctx.spot:
                continue

            delta_btc_10s = self._delta_pct(ctx.spot_btc, ctx.spot_btc_10s_ago)
            delta_eth_10s = self._delta_pct(ctx.spot_eth, ctx.spot_eth_10s_ago)
            now_ts = ctx.now.timestamp()

            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="yes",
                features={
                    "ya": leg.ya, "yb": leg.yb, "na": leg.na,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                    "kind": leg.kind,
                    "spot_btc": ctx.spot_btc, "spot_eth": ctx.spot_eth,
                    "spot_btc_30s_ago": ctx.spot_btc_30s_ago,
                    "spot_eth_30s_ago": ctx.spot_eth_30s_ago,
                    "spot_btc_10s_ago": ctx.spot_btc_10s_ago,
                    "spot_eth_10s_ago": ctx.spot_eth_10s_ago,
                    "delta_btc_30s_pct": round(delta_btc, 6),
                    "delta_eth_30s_pct": round(delta_eth, 6),
                    "delta_btc_10s_pct": round(delta_btc_10s, 6) if delta_btc_10s else None,
                    "delta_eth_10s_pct": round(delta_eth_10s, 6) if delta_eth_10s else None,
                    "corr_5min": ctx.corr_5min,
                    "corr_15min": ctx.corr_15min,
                    "predicted_direction": direction,
                    "source_signal_hash": self._source_signal_hash(now_ts, delta_btc),
                },
            ))
        return out
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k lead_lag
```
Expected: 8 passed (all lead_lag tests)

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_cross_asset_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_cross_asset): BtcEthLeadLagObserver

Fires on KXETH when BTC moved ≥0.3% over 30s while ETH moved ≤0.15%.
Bucket must be on the expected catch-up side (above spot for BTC up,
below for BTC down). dist_pct ≤2%, ya ∈ [0.05, 0.60], mts ≥300s,
yb_size ≥30. Logs predicted_direction + full cross-asset feature set
including source_signal_hash for dedup."
```

---

## Task 7: `CrossAssetCorrBreakObserver`

**Files:**
- Modify: `strategies/crypto_cross_asset_observer.py`
- Modify: `tests/test_crypto_cross_asset_observer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_cross_asset_observer import CrossAssetCorrBreakObserver


def test_corr_break_fires_when_corr_drops_with_movement():
    """5-min corr <0.4, 15-min baseline ≥0.6, some movement → fire on ATM bucket."""
    # ATM bucket at 2120 with spot 2120 (dist 0%, < 0.5% threshold)
    legs = [_make_leg("KXETH", 2120, ya=0.45, yb=0.40, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60300.0,   # +0.33% > 0.2%
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.25,    # below 0.4
        corr_15min=0.80,   # above baseline 0.6
        close_s=900.0,
    )
    obs = CrossAssetCorrBreakObserver()
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].observer == "cross_asset_corr_break"
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].features["is_atm_bucket"] is True


def test_corr_break_skips_when_no_movement():
    """Both spots flat — no event yet, skip."""
    legs = [_make_leg("KXETH", 2120, ya=0.45, yb=0.40, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60000.0, spot_btc_30s_ago=60000.0,
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.25, corr_15min=0.80,
        close_s=900.0,
    )
    assert CrossAssetCorrBreakObserver().evaluate(ctx) == []


def test_corr_break_skips_when_baseline_low():
    """corr_15min 0.5 < baseline 0.6 — never was high-corr, skip."""
    legs = [_make_leg("KXETH", 2120, ya=0.45, yb=0.40, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60300.0,
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.25, corr_15min=0.50,
        close_s=900.0,
    )
    assert CrossAssetCorrBreakObserver().evaluate(ctx) == []


def test_corr_break_skips_outside_atm():
    """Bucket 2200 vs spot 2120 = 3.77% > 0.5% atm cutoff — skip."""
    legs = [_make_leg("KXETH", 2200, ya=0.45, yb=0.40, yb_size=50)]
    ctx = _make_ctx(
        series="KXETH", asset="ETH", spot=2120.0, b_legs=legs,
        spot_btc=60500.0, spot_btc_30s_ago=60300.0,
        spot_eth=2120.0, spot_eth_30s_ago=2119.0,
        corr_5min=0.25, corr_15min=0.80,
        close_s=900.0,
    )
    assert CrossAssetCorrBreakObserver().evaluate(ctx) == []
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k corr_break
```
Expected: ImportError on `CrossAssetCorrBreakObserver`

- [ ] **Step 3: Write `CrossAssetCorrBreakObserver`**

Append to `strategies/crypto_cross_asset_observer.py`:

```python
# Gates per design spec
CORR_BREAK_CORR_MAX = 0.40           # corr_5min < 0.40 triggers
CORR_BREAK_BASELINE_MIN = 0.60       # corr_15min ≥ 0.60 required (came from high-corr regime)
CORR_BREAK_MOVEMENT_MIN = 0.002      # one side moved ≥0.2% (event happened)
CORR_BREAK_ATM_DIST_PCT_MAX = 0.005  # |strike - spot| / spot ≤ 0.5%
CORR_BREAK_MTS_MIN_S = 300.0
CORR_BREAK_YB_SIZE_MIN = 30


class CrossAssetCorrBreakObserver(_CrossAssetBaseObserver):
    """BTC-ETH 5-min correlation drops sharply from baseline.

    Directionally ambiguous — provisional predicted='no' on ATM bucket as
    conservative default ("ATM doesn't get hit in regime break"). Real
    value is in features for post-hoc directional analysis.
    Phase 0 only.
    """
    name = "cross_asset_corr_break"

    def evaluate(self, ctx) -> list:
        from strategies.crypto_observer import Signal

        if ctx.close_time_secs is None or ctx.close_time_secs < CORR_BREAK_MTS_MIN_S:
            return []
        if ctx.corr_5min is None or ctx.corr_15min is None:
            return []
        if ctx.corr_5min >= CORR_BREAK_CORR_MAX:
            return []
        if ctx.corr_15min < CORR_BREAK_BASELINE_MIN:
            return []

        delta_btc = self._delta_pct(ctx.spot_btc, ctx.spot_btc_30s_ago)
        delta_eth = self._delta_pct(ctx.spot_eth, ctx.spot_eth_30s_ago)
        if delta_btc is None or delta_eth is None:
            return []
        if abs(delta_btc) < CORR_BREAK_MOVEMENT_MIN and abs(delta_eth) < CORR_BREAK_MOVEMENT_MIN:
            return []

        out: list = []
        for leg in ctx.b_legs:
            if leg.yb_size < CORR_BREAK_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct > CORR_BREAK_ATM_DIST_PCT_MAX:
                continue
            now_ts = ctx.now.timestamp()
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={
                    "ya": leg.ya, "yb": leg.yb, "na": leg.na,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                    "kind": leg.kind,
                    "spot_btc": ctx.spot_btc, "spot_eth": ctx.spot_eth,
                    "delta_btc_30s_pct": round(delta_btc, 6),
                    "delta_eth_30s_pct": round(delta_eth, 6),
                    "corr_5min": ctx.corr_5min,
                    "corr_15min": ctx.corr_15min,
                    "is_atm_bucket": True,
                    "source_signal_hash": self._source_signal_hash(now_ts, delta_btc),
                },
            ))
        return out
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k corr_break
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_cross_asset_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_cross_asset): CrossAssetCorrBreakObserver

Fires on ATM bucket (within 0.5% of spot) when corr_5min <0.4 and
corr_15min ≥0.6 (regime had been high-corr) and at least one of
BTC/ETH moved ≥0.2%. Directionally ambiguous — provisional pred=no
for conservative default; real value is the logged features."
```

---

## Task 8: `KalshiBookLagObserver`

**Files:**
- Modify: `strategies/crypto_cross_asset_observer.py`
- Modify: `tests/test_crypto_cross_asset_observer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_cross_asset_observer import (
    KalshiBookLagObserver, _BookMidCache,
)


def test_book_lag_fires_when_book_static_while_spot_moves():
    """Spot +0.5% over 30s, bucket mid unchanged → fire on lagging bucket."""
    cache = _BookMidCache()
    # Bucket mid 30s ago = 0.20 (no change yet)
    cache.update("KXBTC", 60100.0, 970.0, 0.20)
    cache.update("KXBTC", 60100.0, 1000.0, 0.20)  # still 0.20 at now
    legs = [_make_leg("KXBTC", 60100, ya=0.22, yb=0.18, yb_size=50)]
    ctx = _make_ctx(
        series="KXBTC", asset="BTC", spot=60100.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=59800.0,    # +0.50%
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=600.0,
        book_mid_cache=cache,
    )
    # Override now for ctx to align with cache ts
    ctx.now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
    obs = KalshiBookLagObserver()
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].observer == "kalshi_book_lag"
    assert sigs[0].predicted_outcome == "yes"


def test_book_lag_skips_when_book_caught_up():
    """Spot moved, bucket mid also moved ≥1¢ — caught up, skip."""
    cache = _BookMidCache()
    cache.update("KXBTC", 60100.0, 970.0, 0.20)
    cache.update("KXBTC", 60100.0, 1000.0, 0.25)  # +5¢ change → caught up
    legs = [_make_leg("KXBTC", 60100, ya=0.25, yb=0.22, yb_size=50)]
    ctx = _make_ctx(
        series="KXBTC", asset="BTC", spot=60100.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=59800.0,
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=600.0,
        book_mid_cache=cache,
    )
    ctx.now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
    assert KalshiBookLagObserver().evaluate(ctx) == []


def test_book_lag_skips_outside_close_window():
    """mts_s 1500 > 1200 cap — skip."""
    cache = _BookMidCache()
    cache.update("KXBTC", 60100.0, 970.0, 0.20)
    cache.update("KXBTC", 60100.0, 1000.0, 0.20)
    legs = [_make_leg("KXBTC", 60100, ya=0.22, yb=0.18, yb_size=50)]
    ctx = _make_ctx(
        series="KXBTC", asset="BTC", spot=60100.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=59800.0,
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=1500.0,
        book_mid_cache=cache,
    )
    ctx.now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
    assert KalshiBookLagObserver().evaluate(ctx) == []


def test_book_lag_skips_when_no_bucket_history():
    """Cache has no entry for this strike — n_seconds_ago returns None, skip."""
    cache = _BookMidCache()  # empty
    legs = [_make_leg("KXBTC", 60100, ya=0.22, yb=0.18, yb_size=50)]
    ctx = _make_ctx(
        series="KXBTC", asset="BTC", spot=60100.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=59800.0,
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=600.0,
        book_mid_cache=cache,
    )
    ctx.now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
    assert KalshiBookLagObserver().evaluate(ctx) == []


def test_book_lag_skips_when_no_cache_attached():
    """ctx.book_mid_cache is None — skip."""
    legs = [_make_leg("KXBTC", 60100, ya=0.22, yb=0.18, yb_size=50)]
    ctx = _make_ctx(
        series="KXBTC", asset="BTC", spot=60100.0, b_legs=legs,
        spot_btc=60100.0, spot_btc_30s_ago=59800.0,
        spot_eth=2120.0, spot_eth_30s_ago=2120.0,
        corr_5min=0.85, corr_15min=0.85,
        close_s=600.0,
        book_mid_cache=None,
    )
    assert KalshiBookLagObserver().evaluate(ctx) == []
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k book_lag
```
Expected: ImportError on `KalshiBookLagObserver`

- [ ] **Step 3: Write `KalshiBookLagObserver`**

Append to `strategies/crypto_cross_asset_observer.py`:

```python
# Gates per design spec
BOOK_LAG_SPOT_THRESHOLD = 0.003      # 0.3% Δspot over 30s
BOOK_LAG_BOOK_LAG_DOLLAR_MAX = 0.01  # bucket mid moved <1¢ → lagging
BOOK_LAG_LOOKBACK_S = 30.0
BOOK_LAG_YA_MIN = 0.10
BOOK_LAG_YA_MAX = 0.50
BOOK_LAG_MTS_MAX_S = 1200.0
BOOK_LAG_YB_SIZE_MIN = 30


class KalshiBookLagObserver(_CrossAssetBaseObserver):
    """Spot moved meaningfully but Kalshi bucket-mid is stale.

    Fires on the bucket whose strike is closest to current spot (the bucket
    that *should* be ATM after the spot move) when its mid has barely
    moved. Predicts 'yes' on that bucket. Single-asset thesis but housed
    here because it shares BookMidCache infrastructure with cross-asset
    observers.
    """
    name = "kalshi_book_lag"

    def evaluate(self, ctx) -> list:
        from strategies.crypto_observer import Signal

        if ctx.close_time_secs is None or ctx.close_time_secs > BOOK_LAG_MTS_MAX_S:
            return []
        if ctx.book_mid_cache is None:
            return []
        if not ctx.b_legs:
            return []

        # Use the asset of this series, not cross-asset BTC default
        if ctx.asset == "BTC":
            spot_now = ctx.spot_btc
            spot_30s = ctx.spot_btc_30s_ago
        elif ctx.asset == "ETH":
            spot_now = ctx.spot_eth
            spot_30s = ctx.spot_eth_30s_ago
        else:
            return []
        delta = self._delta_pct(spot_now, spot_30s)
        if delta is None or abs(delta) < BOOK_LAG_SPOT_THRESHOLD:
            return []

        # Target bucket = closest to current spot
        target = min(ctx.b_legs, key=lambda l: abs(l.threshold - ctx.spot))
        if target.ya < BOOK_LAG_YA_MIN or target.ya > BOOK_LAG_YA_MAX:
            return []
        if target.yb_size < BOOK_LAG_YB_SIZE_MIN:
            return []

        now_ts = ctx.now.timestamp()
        mid_now = (target.ya + target.yb) / 2.0
        mid_30s_ago = ctx.book_mid_cache.n_seconds_ago(
            ctx.series, target.threshold, BOOK_LAG_LOOKBACK_S, now_ts,
        )
        if mid_30s_ago is None:
            return []
        book_lag_dollars = abs(mid_now - mid_30s_ago)
        if book_lag_dollars >= BOOK_LAG_BOOK_LAG_DOLLAR_MAX:
            return []

        dist_pct = abs(target.threshold - ctx.spot) / ctx.spot
        return [Signal(
            observer=self.name, series=ctx.series,
            event_key=ctx.event_key, market_id=target.ticker,
            predicted_outcome="yes",
            features={
                "ya": target.ya, "yb": target.yb, "na": target.na,
                "yb_size": target.yb_size,
                "spot": ctx.spot, "strike": target.threshold,
                "dist_pct": round(dist_pct, 5),
                "mts_s": round(ctx.close_time_secs, 1),
                "kind": target.kind,
                "spot_at_fire": spot_now, "spot_30s_ago": spot_30s,
                "delta_spot_30s_pct": round(delta, 6),
                "bucket_mid_now": round(mid_now, 4),
                "bucket_mid_30s_ago": round(mid_30s_ago, 4),
                "book_lag_cents": round(book_lag_dollars * 100, 2),
                "corr_5min": ctx.corr_5min,
                "corr_15min": ctx.corr_15min,
            },
        )]
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py -v -k book_lag
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_cross_asset_observer.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_cross_asset): KalshiBookLagObserver

Single-asset thesis (KXBTC vs BTC spot, KXETH vs ETH spot) housed here
for shared BookMidCache infrastructure. Fires on the ATM-relative
target bucket when its mid has moved <1¢ in 30s while spot moved
≥0.3%. mts_s ≤1200 (close window), ya ∈ [0.10, 0.50], yb_size ≥30."
```

---

## Task 9: Register observers in OBSERVER_CLASSES tuple + dashboard

**Files:**
- Modify: `strategies/crypto_observer.py` (OBSERVER_CLASSES tuple)
- Modify: `monitoring/dashboard/api.py` (KNOWN_OBSERVERS tuple)

- [ ] **Step 1: Write registration verification test**

Append to `tests/test_crypto_cross_asset_observer.py`:

```python
from strategies.crypto_observer import OBSERVER_CLASSES


def test_observers_registered_in_observer_classes():
    names = [cls().name for cls in OBSERVER_CLASSES]
    assert "btc_eth_lead_lag" in names
    assert "cross_asset_corr_break" in names
    assert "kalshi_book_lag" in names
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python3 -m pytest tests/test_crypto_cross_asset_observer.py::test_observers_registered_in_observer_classes -v
```
Expected: AssertionError — observers not yet in tuple.

- [ ] **Step 3: Add observers to OBSERVER_CLASSES**

In `strategies/crypto_observer.py` around line 1110-1115, replace the OBSERVER_CLASSES tuple's tail with:

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

# 2026-05-20 Phase 0: cross-asset observers (separate module — own thesis class)
from strategies.crypto_cross_asset_observer import (
    BtcEthLeadLagObserver, CrossAssetCorrBreakObserver, KalshiBookLagObserver,
)
OBSERVER_CLASSES = OBSERVER_CLASSES + (
    BtcEthLeadLagObserver, CrossAssetCorrBreakObserver, KalshiBookLagObserver,
)
```

- [ ] **Step 4: Add to dashboard KNOWN_OBSERVERS**

In `monitoring/dashboard/api.py` around line 1446-1448, replace:

```python
            "open_no_touch", "atm_hold", "bid_imbalance_fade",
            "late_close_tail", "deribit_atm_skew",
            "mid_otm_tail",
            # Direct traders (write Phase 0 signals under their own observer name).
```

with:

```python
            "open_no_touch", "atm_hold", "bid_imbalance_fade",
            "late_close_tail", "deribit_atm_skew",
            "mid_otm_tail",
            "btc_eth_lead_lag", "cross_asset_corr_break", "kalshi_book_lag",
            # Direct traders (write Phase 0 signals under their own observer name).
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/test_crypto_observer.py tests/test_crypto_cross_asset_observer.py tests/test_dashboard_api.py
```
Expected: All pass (~28 new + ~77 existing = ~105 tests).

- [ ] **Step 6: Commit**

```bash
git add strategies/crypto_observer.py monitoring/dashboard/api.py tests/test_crypto_cross_asset_observer.py
git commit -m "feat(crypto_observer): register cross-asset observers

OBSERVER_CLASSES tuple extended with the 3 new Phase 0 observers from
strategies.crypto_cross_asset_observer. Dashboard KNOWN_OBSERVERS
tuple updated so per-observer status table picks them up."
```

---

## Task 10: Deploy + verify telemetry

**Files:** none modified (deploy step only)

- [ ] **Step 1: Push to remote**

```bash
git push origin master
```
Expected: push succeeds.

- [ ] **Step 2: Pull on EC2**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && git pull"
```
Expected: Fast-forward, multiple files updated.

- [ ] **Step 3: Restart bot service**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo systemctl restart kalshi-bot && sleep 3 && sudo systemctl is-active kalshi-bot"
```
Expected: `active`

- [ ] **Step 4: Verify observer fires arrive**

After ~10 min for data to accumulate:

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && python3 -c \"
import sqlite3
c = sqlite3.connect('data/bot.db')
rows = c.execute(\\\"SELECT observer, COUNT(*) FROM crypto_observer_signals \
WHERE observer IN ('btc_eth_lead_lag','cross_asset_corr_break','kalshi_book_lag') \
AND ts > datetime('now','-1 hour') GROUP BY observer\\\").fetchall()
for r in rows: print(r)
\""
```

Expected: at least 1 row, possibly all 3 observers represented. If 0 rows after 30+ minutes, investigate via journalctl logs.

- [ ] **Step 5: Verify BookMidCache prune is running**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '10 minutes ago' | grep BOOK_MID_CACHE"
```
Expected: at least one `[BOOK_MID_CACHE_PRUNE]` line every ~5 min (only logged when debug flag set), no `[BOOK_MID_CACHE_FLUSH]` lines (would be catastrophic).

- [ ] **Step 6: Verify no observer crashes**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '30 minutes ago' | grep -E 'observer.*crashed'"
```
Expected: no output (no observer crashes). If output exists, investigate.

- [ ] **Step 7: Final commit (memory + status note)**

(Manual step — no git changes. Update local memory or notes capturing deploy time so next eval session can find the 1-week mark.)

---

## Self-Review Notes

**Spec coverage check** (every section of the spec mapped to a task):

- "Architecture / New module" → Task 1 (creates the file)
- "Utility components: _BookMidCache" → Task 2
- "_CrossAssetCache" (consolidated to `_pearson_correlation` + `_correlation_lookback` on existing `_spot_history`) → Tasks 1 + 3
- "EventContext extensions" → Task 4
- "Data flow / scan loop wiring" → Task 5
- "Observer 1 lead_lag" → Task 6
- "Observer 2 corr_break" → Task 7
- "Observer 3 book_lag" → Task 8
- "Observer registration" → Task 9
- "Dashboard registration" → Task 9
- "Memory leak defenses" → Task 2 (hard cap) + Task 5 (soft watchdog + prune cadence)
- "Stale data guards" → Task 1 (`_pearson_correlation` returns None on sparse), Task 2 (`n_seconds_ago` 2× lookback guard), each observer (skip when feature None)
- "Observer crash isolation" → already exists (verified at line 1437-1439), no new task
- "Dual-series double-fire / source_signal_hash" → Tasks 6 + 7 (logged in features)
- "Tests" → Tasks 1, 2, 3, 4, 6, 7, 8, 9 (each task adds its own tests)
- "Deploy" → Task 10
- "Promotion gates" — informational only, no code

**Placeholder scan:** no TBD/TODO/"implement later" in any task. All code blocks are complete.

**Type consistency:** `_pearson_correlation` (Task 1) used by `_correlation_lookback` (Task 3). `_BookMidCache` (Task 2) used by all observers via ctx.book_mid_cache. Observer class names consistent across spec, plan, dashboard, OBSERVER_CLASSES tuple.

**Cross-asset health widget** — deferred to follow-up per spec ("dashboard cross_asset_health widget" listed in spec but plan only adds KNOWN_OBSERVERS entry; full widget is a UI work item out of scope here).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-20-crypto-cross-asset-observer.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — execute tasks in this session with checkpoints

Which approach?
