# Cross-Venue Funding Consensus Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-venue funding-rate Phase 0 observer that reads Deribit + OKX + Bybit perpetual funding rates in parallel, emits a `consensus_fade` signal when all venues agree (stronger fade thesis than single-venue `funding_rate_fade`), and emits a `divergence` event when venues disagree sharply (for post-hoc analysis).

**Architecture:** New module `core/funding_rates_multi.py` exposes `async def get_all_funding(asset) -> dict[str, Optional[float]]` using `httpx.AsyncClient` + `asyncio.gather` (per [[sync_in_async_sweep_2026-05-19]] — sync httpx inside async harness was the source of the Connect-Timeout flood). New observer `CrossVenueFundingConsensusObserver` consumes a new `EventContext.funding_8h_by_venue` field populated by the scan-tick harness. The existing single-venue `funding_8h` field is kept for backward compat with `FundingRateFadeObserver`.

**Tech Stack:** Python 3 / `httpx.AsyncClient` / `asyncio.gather` / pytest / `unittest.mock.patch` + `AsyncMock` (per existing test pattern in `tests/test_freshness_sampler.py:43`).

**Spec:** [docs/superpowers/specs/2026-05-20-cross-venue-funding-consensus-observer-design.md](../specs/2026-05-20-cross-venue-funding-consensus-observer-design.md)

---

## File Structure

**Create:**

1. `core/funding_rates_multi.py` — venue-agnostic async funding fetcher (~120 LOC).
   Public API: `VENUES`, `get_funding(asset, venue)`, `get_all_funding(asset)`.
   Internal: per-venue endpoint URLs, per-venue parsers, TTL cache.

2. `tests/test_funding_rates_multi.py` — 2 tests for the fetcher (mocking `httpx.AsyncClient.get`).

**Modify:**

3. `strategies/crypto_observer.py`:
   - Add 5 module-level constants near existing crypto-observer constants.
   - Add `funding_8h_by_venue: dict[str, Optional[float]] = field(default_factory=dict)` to `EventContext`.
   - Add `CrossVenueFundingConsensusObserver` class near `FundingRateFadeObserver`.
   - Wire `get_all_funding(asset)` into the scan-tick harness (`scan_and_execute`).
   - Append `CrossVenueFundingConsensusObserver` to `OBSERVER_CLASSES`.

4. `tests/test_crypto_observer.py`:
   - Import the new observer.
   - Add 3 observer tests + 1 registry smoke test.

**No DB schema changes.** (signals table absorbs the new observer name + features JSON automatically.)

---

## Task 1: Create `core/funding_rates_multi.py` with venue-parallel async fetcher

**Files:**
- Create: `core/funding_rates_multi.py`
- Create: `tests/test_funding_rates_multi.py`

This task is TDD-first: write the test that mocks `httpx.AsyncClient.get` returning per-venue responses, then implement the module.

- [ ] **Step 1: Write the failing test for `get_all_funding`**

Create `tests/test_funding_rates_multi.py` with:

```python
"""Unit tests for core/funding_rates_multi.py.

Mocks httpx.AsyncClient.get so the venue HTTP endpoints are not hit
during test runs.
"""
from __future__ import annotations
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from core.funding_rates_multi import (
    VENUES, get_all_funding, get_funding, _clear_cache_for_tests,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _deribit_payload(funding_8h: float) -> dict:
    return {"result": {"funding_8h": funding_8h}}


def _okx_payload(funding_rate_per_period: float) -> dict:
    # OKX returns fundingRate per 8h period (already 8h on USDT-SWAP)
    # as a string. funding_rates_multi.py is responsible for str->float.
    return {"data": [{"fundingRate": str(funding_rate_per_period)}]}


def _bybit_payload(funding_rate_per_period: float) -> dict:
    return {"result": {"list": [{"fundingRate": str(funding_rate_per_period)}]}}


def _make_fake_get(per_url_payload: dict[str, dict],
                   per_url_status: dict[str, int] | None = None):
    """Build a fake httpx.AsyncClient.get that dispatches on URL substring.

    per_url_payload keys are substrings to match (e.g. "deribit.com",
    "okx.com", "bybit.com").
    """
    per_url_status = per_url_status or {}

    async def fake_get(self, url, *a, **k):
        for needle, payload in per_url_payload.items():
            if needle in url:
                return _FakeResponse(per_url_status.get(needle, 200), payload)
        return _FakeResponse(404, {})
    return fake_get


@pytest.mark.asyncio
async def test_get_all_funding_returns_all_three_venues():
    """All three venues respond 200 → dict has 3 entries, all floats."""
    _clear_cache_for_tests()
    fake_get = _make_fake_get({
        "deribit.com": _deribit_payload(0.0005),
        "okx.com":     _okx_payload(0.0003),
        "bybit.com":   _bybit_payload(0.0006),
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await get_all_funding("BTC")
    assert set(result.keys()) == set(VENUES)
    assert result["deribit"] == pytest.approx(0.0005)
    assert result["okx"]     == pytest.approx(0.0003)
    assert result["bybit"]   == pytest.approx(0.0006)


@pytest.mark.asyncio
async def test_get_all_funding_one_venue_failure_other_two_succeed():
    """One venue returns 500 → its entry is None, other two still populated."""
    _clear_cache_for_tests()
    fake_get = _make_fake_get(
        per_url_payload={
            "deribit.com": _deribit_payload(0.0005),
            "okx.com":     {},
            "bybit.com":   _bybit_payload(0.0006),
        },
        per_url_status={"okx.com": 500},
    )
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await get_all_funding("BTC")
    assert result["deribit"] == pytest.approx(0.0005)
    assert result["okx"] is None
    assert result["bybit"] == pytest.approx(0.0006)
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
pytest tests/test_funding_rates_multi.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'core.funding_rates_multi'`.

- [ ] **Step 3: Implement `core/funding_rates_multi.py`**

Create the file with the following content:

```python
"""Cross-venue perpetual funding-rate fetcher (Phase 0).

Reads BTC/ETH funding rates from Deribit, OKX, and Bybit in parallel
using httpx.AsyncClient + asyncio.gather. Each venue's fetch is
isolated: one venue failing returns None for that key, the other
venues still populate.

Per [[sync_in_async_sweep_2026-05-19]] this module uses async httpx
exclusively. Do NOT mix in sync httpx.get inside the crypto_observer
harness scan_loop — it will block other observers.

API:
  VENUES = ("deribit", "okx", "bybit")
  get_funding(asset, venue) -> Optional[float]   # 8h-normalized decimal
  get_all_funding(asset)    -> dict[str, Optional[float]]   # 3 keys

All return values are signed 8h-decimal funding rates
(e.g. 0.0005 = 0.05%/8h ≈ 55% APR for longs paying shorts).
TTL-cached at 60s — funding rate updates every 8h on Deribit and
4-8h on OKX/Bybit, so polling more often is wasted.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("funding_rates_multi")

VENUES: tuple[str, ...] = ("deribit", "okx", "bybit")
_TTL_S: float = 60.0
_HTTP_TIMEOUT_S: float = 5.0

# Cache: (venue, asset) -> (epoch_fetched, value | None)
_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}


def _clear_cache_for_tests() -> None:
    """Wipe the cache (test-only helper). Not part of public API."""
    _CACHE.clear()


# Venue endpoint templates. Each parser must return the 8h-normalized
# funding rate as a signed decimal float.
_VENUE_ENDPOINTS: dict[str, str] = {
    "deribit": "https://www.deribit.com/api/v2/public/ticker"
               "?instrument_name={asset}-PERPETUAL",
    "okx":     "https://www.okx.com/api/v5/public/funding-rate"
               "?instId={asset}-USDT-SWAP",
    "bybit":   "https://api.bybit.com/v5/market/tickers"
               "?category=linear&symbol={asset}USDT",
}


def _parse_deribit(payload: dict) -> Optional[float]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    v = result.get("funding_8h")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_okx(payload: dict) -> Optional[float]:
    # OKX V5 returns data[0].fundingRate as a string. fundingRate is
    # the rate for the next funding interval (8h for USDT-SWAP, the
    # default product). Treat as already-8h.
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    try:
        return float(data[0].get("fundingRate"))
    except (TypeError, ValueError):
        return None


def _parse_bybit(payload: dict) -> Optional[float]:
    # Bybit V5 returns result.list[0].fundingRate as a string. Linear
    # USDT perpetuals fund every 8h.
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    legs = result.get("list")
    if not isinstance(legs, list) or not legs:
        return None
    try:
        return float(legs[0].get("fundingRate"))
    except (TypeError, ValueError):
        return None


_VENUE_PARSERS = {
    "deribit": _parse_deribit,
    "okx":     _parse_okx,
    "bybit":   _parse_bybit,
}


async def _fetch_one(client: httpx.AsyncClient, asset: str,
                     venue: str) -> Optional[float]:
    """Fetch and parse one venue. Returns None on any failure."""
    url = _VENUE_ENDPOINTS[venue].format(asset=asset)
    try:
        r = await client.get(url, timeout=_HTTP_TIMEOUT_S)
    except Exception as e:
        logger.warning(f"funding fetch {venue}/{asset} HTTP error: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"funding fetch {venue}/{asset} status {r.status_code}")
        return None
    try:
        payload = r.json()
    except Exception as e:
        logger.warning(f"funding fetch {venue}/{asset} json error: {e}")
        return None
    return _VENUE_PARSERS[venue](payload)


async def get_funding(asset: str, venue: str) -> Optional[float]:
    """Fetch one venue's funding rate for the given asset.

    Returns 8h-normalized signed decimal. None on failure.
    Cached at _TTL_S per (venue, asset).
    """
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}; expected one of {VENUES}")
    now = time.time()
    cached = _CACHE.get((venue, asset))
    if cached is not None and (now - cached[0]) < _TTL_S:
        return cached[1]
    async with httpx.AsyncClient() as client:
        val = await _fetch_one(client, asset, venue)
    _CACHE[(venue, asset)] = (now, val)
    return val


async def get_all_funding(asset: str) -> dict[str, Optional[float]]:
    """Fetch all VENUES in parallel. Each venue independent.

    Returns dict with one entry per VENUES name. Values are signed
    8h-decimal funding rate or None on per-venue failure.
    """
    now = time.time()

    # Decide which venues need a live fetch vs cache hit.
    fetched: dict[str, Optional[float]] = {}
    to_fetch: list[str] = []
    for v in VENUES:
        cached = _CACHE.get((v, asset))
        if cached is not None and (now - cached[0]) < _TTL_S:
            fetched[v] = cached[1]
        else:
            to_fetch.append(v)

    if to_fetch:
        async with httpx.AsyncClient() as client:
            coros = [_fetch_one(client, asset, v) for v in to_fetch]
            results = await asyncio.gather(*coros, return_exceptions=False)
        for v, val in zip(to_fetch, results):
            _CACHE[(v, asset)] = (now, val)
            fetched[v] = val

    return {v: fetched.get(v) for v in VENUES}
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
pytest tests/test_funding_rates_multi.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/funding_rates_multi.py tests/test_funding_rates_multi.py
git commit -m "$(cat <<'EOF'
feat(core): async cross-venue funding-rate fetcher

Deribit + OKX + Bybit parallel fetcher using httpx.AsyncClient +
asyncio.gather. Each venue fetch is isolated (one failure → that
venue's value is None, others still populate). 60s TTL cache to
respect the 8h funding cadence. Per sync_in_async_sweep_2026-05-19
this is async-native — must not be mixed with sync httpx inside the
crypto_observer scan_loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `EventContext.funding_8h_by_venue` field

**Files:**
- Modify: `strategies/crypto_observer.py` (one new field on `EventContext`)

The dataclass `EventContext` lives around lines 318-350. The existing single-venue `funding_8h` field is kept (Plan #1 / existing `FundingRateFadeObserver` consumer).

- [ ] **Step 1: Add a failing test that observes the new field**

Append to `tests/test_crypto_observer.py` (this test will compile to a meaningful assertion once the field exists):

```python
def test_event_context_has_funding_8h_by_venue_default_empty():
    """EventContext.funding_8h_by_venue defaults to empty dict."""
    ctx = _ctx(b_legs=[], close_s=600.0)
    assert hasattr(ctx, "funding_8h_by_venue")
    assert ctx.funding_8h_by_venue == {}
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_crypto_observer.py::test_event_context_has_funding_8h_by_venue_default_empty -v
```

Expected: FAIL with `AttributeError` (the field doesn't exist yet).

- [ ] **Step 3: Add the new field to `EventContext`**

In `strategies/crypto_observer.py`, locate the `EventContext` dataclass (around line 318). Find the existing cross-asset block (`spot_btc`, `spot_eth`, `corr_5min`, etc., around lines 345-355). Add the new field immediately after the cross-asset block, right before the `book_mid_cache` field (or at the end of the cross-asset block — whichever is the natural end of the existing additions). Use this exact line:

```python
    # 2026-05-20 cross-venue funding (Deribit / OKX / Bybit)
    funding_8h_by_venue: dict[str, Optional[float]] = field(default_factory=dict)
```

Verify the `field` import is already present at top of file — it is (used by existing `Leg.raw: dict = field(repr=False)`). No new import required.

- [ ] **Step 4: Run the test to confirm it passes**

```bash
pytest tests/test_crypto_observer.py::test_event_context_has_funding_8h_by_venue_default_empty -v
```

Expected: PASS.

Also re-run the full existing test suite to confirm no regression:

```bash
pytest tests/test_crypto_observer.py -v
```

Expected: all previously-passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): EventContext.funding_8h_by_venue field

Additive cross-venue funding field, populated by the scan-tick harness
in a later commit. Existing funding_8h (Deribit-only) is kept for
backward compat with FundingRateFadeObserver.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add observer constants + class + consensus_fade happy-path test

**Files:**
- Modify: `tests/test_crypto_observer.py` (import + 1 new test)
- Modify: `strategies/crypto_observer.py` (5 constants + class)

- [ ] **Step 1: Add import + happy-path test**

In `tests/test_crypto_observer.py`, modify the import block to add `CrossVenueFundingConsensusObserver`:

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
    CrossVenueFundingConsensusObserver,
    _parse_suffix_threshold, _suffix_kind, _event_key, _build_leg,
    _lognormal_p_above, _flip,
)
```

(Only add `CrossVenueFundingConsensusObserver` — leave the rest of the import as-is.)

Append the happy-path test:

```python
# ── cross_venue_funding_consensus observer ────────────────────────


def _ctx_for_funding(funding_by_venue, *, spot=70_000.0, close_s=300.0,
                     b_legs=None):
    """Build an EventContext with the cross-venue funding field set.

    The standard _ctx() helper doesn't accept funding_8h_by_venue
    because the field is new; this wrapper sets it after construction.
    """
    if b_legs is None:
        b_legs = [_b(70_000, ya=0.10, yb=0.08, yb_size=200),
                  _b(71_000, ya=0.10, yb=0.08, yb_size=200)]
    ctx = _ctx(b_legs=b_legs, spot=spot, close_s=close_s)
    ctx.funding_8h_by_venue = funding_by_venue
    return ctx


def test_cross_venue_funding_consensus_fires_on_agreement():
    """All 3 venues funding > +0.0002 → consensus_fade signal,
    predicted='no' on strikes above spot (fade upside)."""
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0005, "okx": 0.0004, "bybit": 0.0006},
        spot=70_000.0, close_s=300.0,
        b_legs=[_b(70_000, ya=0.10, yb=0.08, yb_size=200),
                _b(71_000, ya=0.10, yb=0.08, yb_size=200)],
    )
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)

    consensus = [s for s in sigs if s.features["kind"] == "consensus_fade"]
    assert len(consensus) >= 1, f"expected consensus_fade signal, got {sigs}"
    sig = consensus[0]
    assert sig.predicted_outcome == "no"
    assert sig.features["consensus_sign"] == 1   # all positive
    assert set(sig.features["venues_reporting"]) == {"deribit", "okx", "bybit"}
    assert sig.features["funding_deribit"] == pytest.approx(0.0005)
    assert sig.features["funding_okx"]     == pytest.approx(0.0004)
    assert sig.features["funding_bybit"]   == pytest.approx(0.0006)
    assert sig.features["min_abs"] == pytest.approx(0.0004, abs=1e-6)
    # Strike must be above spot for positive-funding fade (fade upside)
    assert sig.features["side"] == "fade_upside"
    assert sig.features["strike"] > sig.features["spot"]
```

- [ ] **Step 2: Run to confirm import failure**

```bash
pytest tests/test_crypto_observer.py::test_cross_venue_funding_consensus_fires_on_agreement -v
```

Expected: FAIL with `ImportError: cannot import name 'CrossVenueFundingConsensusObserver'`.

- [ ] **Step 3: Add the 5 module-level constants**

In `strategies/crypto_observer.py`, locate a stable insertion point near the existing `FUNDING_RATE_*` constants block (look for `FUNDING_RATE_MIN_ABS` etc, around lines 195-210). Add immediately after the last `FUNDING_RATE_*` constant:

```python
# cross_venue_funding_consensus (2026-05-20): multi-venue agreement /
# divergence observer. consensus_fade fires when all reporting venues
# (≥2) agree in direction AND the minimum |rate| ≥ MIN_ABS. divergence
# fires when max-min spread ≥ DIVERGENCE_MIN (placeholder predicted_outcome
# pending 5/27 analysis). Phase 0 — no Phase 1 trader yet.
CROSS_VENUE_FUNDING_MIN_VENUES = 2
CROSS_VENUE_FUNDING_CONSENSUS_ABS_MIN = 0.0002    # ≥0.02%/8h on agreeing side
CROSS_VENUE_FUNDING_DIVERGENCE_MIN = 0.0005       # ≥0.05%/8h max-min spread
CROSS_VENUE_FUNDING_MTS_MAX_S = 7200              # ≤2hr to close
CROSS_VENUE_FUNDING_MIN_YA = 0.05
```

- [ ] **Step 4: Add the `CrossVenueFundingConsensusObserver` class**

In `strategies/crypto_observer.py`, locate `FundingRateFadeObserver` (currently around line 1395). Insert the new class immediately after it (between `FundingRateFadeObserver` and `VolSkewFadeObserver`):

```python
class CrossVenueFundingConsensusObserver(BaseObserver):
    """Phase 0: multi-venue funding agreement / divergence.

    Two output kinds:
      consensus_fade : All reporting venues (≥ MIN_VENUES) agree on
                       sign AND min(|rate|) ≥ CONSENSUS_ABS_MIN.
                       Per-leg signal fading the agreed direction
                       (positive → fade upside, negative → fade
                       downside). predicted='no'.
      divergence     : max-min spread ≥ DIVERGENCE_MIN. One event-level
                       signal recording the disagreement. predicted='yes'
                       is a placeholder pending 5/27 analysis of whether
                       divergences precede a directional move.

    Sibling to FundingRateFadeObserver (Deribit-only). This observer
    consumes funding_8h_by_venue populated by the scan-tick harness via
    core.funding_rates_multi.get_all_funding.
    """
    name = "cross_venue_funding_consensus"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs <= 0 or ctx.close_time_secs > CROSS_VENUE_FUNDING_MTS_MAX_S:
            return []
        # Filter out None values
        rates = {v: r for v, r in (ctx.funding_8h_by_venue or {}).items()
                 if r is not None}
        if len(rates) < CROSS_VENUE_FUNDING_MIN_VENUES:
            return []

        values = list(rates.values())
        signs = [1 if r > 0 else (-1 if r < 0 else 0) for r in values]
        nonzero_signs = [s for s in signs if s != 0]
        all_agree = (len(nonzero_signs) == len(signs)
                     and len(set(nonzero_signs)) == 1)
        min_abs = min(abs(r) for r in values)
        spread = max(values) - min(values)
        consensus_sign = nonzero_signs[0] if all_agree else 0

        # Helpful feature-shared dict for both kinds
        base_features = {
            "venues_reporting": sorted(rates.keys()),
            "funding_deribit": rates.get("deribit"),
            "funding_okx":     rates.get("okx"),
            "funding_bybit":   rates.get("bybit"),
            "min_abs":   round(min_abs, 6),
            "spread":    round(spread, 6),
            "consensus_sign": consensus_sign,
            "spot":      ctx.spot,
            "mts_s":     round(ctx.close_time_secs, 1),
        }

        out: list[Signal] = []

        # consensus_fade
        if all_agree and min_abs >= CROSS_VENUE_FUNDING_CONSENSUS_ABS_MIN:
            for leg in ctx.b_legs:
                if leg.ya < CROSS_VENUE_FUNDING_MIN_YA:
                    continue
                # consensus_sign > 0 → fade upside → strike must be > spot
                # consensus_sign < 0 → fade downside → strike must be < spot
                if consensus_sign > 0 and leg.threshold <= ctx.spot:
                    continue
                if consensus_sign < 0 and leg.threshold >= ctx.spot:
                    continue
                features = dict(base_features)
                features["kind"] = "consensus_fade"
                features["ya"] = round(leg.ya, 4)
                features["no_ask"] = round(leg.na, 4)
                features["strike"] = leg.threshold
                features["side"] = (
                    "fade_upside" if consensus_sign > 0 else "fade_downside"
                )
                out.append(Signal(
                    observer=self.name, series=ctx.series,
                    event_key=ctx.event_key, market_id=leg.ticker,
                    predicted_outcome="no", features=features,
                ))

        # divergence (one signal per event, anchored to ATM leg if available)
        if spread >= CROSS_VENUE_FUNDING_DIVERGENCE_MIN:
            atm_idx = ctx.atm_bucket_index()
            anchor_leg = ctx.b_legs[atm_idx] if atm_idx is not None else None
            anchor_ticker = anchor_leg.ticker if anchor_leg is not None else ctx.event_key
            features = dict(base_features)
            features["kind"] = "divergence"
            features["ya"] = round(anchor_leg.ya, 4) if anchor_leg else None
            features["strike"] = (
                anchor_leg.threshold if anchor_leg is not None else None
            )
            features["side"] = "divergence"
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=anchor_ticker,
                predicted_outcome="yes", features=features,
            ))

        return out
```

- [ ] **Step 5: Run the happy-path test**

```bash
pytest tests/test_crypto_observer.py::test_cross_venue_funding_consensus_fires_on_agreement -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): add cross_venue_funding_consensus Phase 0 observer

Multi-venue funding agreement/divergence observer. Two signal kinds:
consensus_fade (all venues agree, fade direction) and divergence
(sharp disagreement, event-level signal). Sibling to single-venue
FundingRateFadeObserver. Phase 0 — no Phase 1 trader pending 5/27 +
6/02 eval per fee-adjusted-EV methodology.

Spec: docs/superpowers/specs/2026-05-20-cross-venue-funding-consensus-observer-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: divergence-kind test

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

The Task 3 implementation handles divergence — this task adds a test exercising that branch.

- [ ] **Step 1: Append the divergence test**

```python
def test_cross_venue_funding_divergence_fires():
    """One venue +0.0008, another -0.0003 → spread 0.0011 ≥ 0.0005,
    divergence event-level signal."""
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0008, "okx": -0.0003, "bybit": 0.0001},
        spot=70_000.0, close_s=300.0,
        b_legs=[_b(69_500, ya=0.45, yb=0.43, yb_size=200),
                _b(70_000, ya=0.50, yb=0.48, yb_size=200),
                _b(70_500, ya=0.45, yb=0.43, yb_size=200)],
    )
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)

    div_sigs = [s for s in sigs if s.features["kind"] == "divergence"]
    assert len(div_sigs) == 1, (
        f"expected exactly 1 divergence signal, got {len(div_sigs)}: {sigs}"
    )
    sig = div_sigs[0]
    # spread = 0.0008 - (-0.0003) = 0.0011
    assert sig.features["spread"] == pytest.approx(0.0011, abs=1e-6)
    # consensus_sign = 0 (not all agree)
    assert sig.features["consensus_sign"] == 0
    # Divergence anchors to ATM bucket (70000)
    assert sig.market_id.endswith("B70000")
    # No consensus_fade in the same evaluation (signs disagree)
    assert not any(s.features["kind"] == "consensus_fade" for s in sigs)
```

- [ ] **Step 2: Run all cross_venue_funding tests**

```bash
pytest tests/test_crypto_observer.py -k cross_venue_funding -v
```

Expected: 2 PASS (1 from Task 3 + 1 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin cross_venue_funding divergence path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: min_venues gate test

**Files:**
- Modify: `tests/test_crypto_observer.py` (1 new test)

- [ ] **Step 1: Append the min_venues test**

```python
def test_cross_venue_funding_skips_below_min_venues():
    """Only 1 venue reporting (< MIN_VENUES=2) → no signal."""
    # Only deribit reports; okx and bybit are None
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0005, "okx": None, "bybit": None},
        spot=70_000.0, close_s=300.0,
    )
    assert CrossVenueFundingConsensusObserver().evaluate(ctx) == []

    # Empty dict
    ctx_empty = _ctx_for_funding(funding_by_venue={}, spot=70_000.0, close_s=300.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_empty) == []


def test_cross_venue_funding_skips_outside_mts_window():
    """mts_s > MAX (7200) or ≤ 0 → no signal."""
    funding = {"deribit": 0.0005, "okx": 0.0004, "bybit": 0.0006}
    # Way past the 2-hour window
    ctx_far = _ctx_for_funding(funding_by_venue=funding, close_s=10_000.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_far) == []
    # Already closed
    ctx_zero = _ctx_for_funding(funding_by_venue=funding, close_s=0.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_zero) == []
    # None
    ctx_none = _ctx_for_funding(funding_by_venue=funding, close_s=None)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_none) == []
```

- [ ] **Step 2: Run all cross_venue_funding tests**

```bash
pytest tests/test_crypto_observer.py -k cross_venue_funding -v
```

Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
test(crypto_observer): pin cross_venue_funding min_venues + mts gates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire `get_all_funding` into the scan-tick harness

**Files:**
- Modify: `strategies/crypto_observer.py` (~5 line change in `scan_and_execute`)

The existing harness fetches single-venue Deribit funding around line 1841-1850 via `from core.deribit_extras import get_funding_rate`. Add a parallel multi-venue fetch that populates `ctx.funding_8h_by_venue`.

- [ ] **Step 1: Locate the harness fetch block**

Find this block in `strategies/crypto_observer.py` (around line 1839-1851):

```python
if ctx.asset not in funding_by_asset:
    try:
        from core.deribit_extras import get_funding_rate, get_iv_skew
        funding_by_asset[ctx.asset] = get_funding_rate(ctx.asset)
        skew_by_asset[ctx.asset] = get_iv_skew(ctx.asset)
    except Exception as e:
        logger.warning(f"deribit_extras fetch failed {ctx.asset}: {e}")
        funding_by_asset[ctx.asset] = None
        skew_by_asset[ctx.asset] = None
ctx.funding_8h = funding_by_asset[ctx.asset]
ctx.iv_skew = skew_by_asset[ctx.asset]
```

Just before the loop iterates contexts (or right after the existing single-venue fetch — placement that minimizes refactor), we need a multi-venue fetch cached per asset.

Find the dict-init lines just before the loop. They look like:

```python
iv_by_asset: dict[str, Optional[float]] = {}
funding_by_asset: dict[str, Optional[float]] = {}
skew_by_asset: dict[str, Optional[float]] = {}
```

(Look for them around line ~1820-1830. If structure differs, adapt — the goal is to add one new cache-dict per scan tick plus one new fetch.)

- [ ] **Step 2: Add the new per-asset cache dict and fetch**

Add a new cache dict alongside the existing ones:

```python
funding_by_venue_by_asset: dict[str, dict[str, Optional[float]]] = {}
```

Then inside the loop, alongside the existing single-venue fetch, add the multi-venue fetch:

```python
if ctx.asset not in funding_by_venue_by_asset:
    try:
        from core.funding_rates_multi import get_all_funding
        funding_by_venue_by_asset[ctx.asset] = await get_all_funding(ctx.asset)
    except Exception as e:
        logger.warning(f"funding_rates_multi fetch failed {ctx.asset}: {e}")
        funding_by_venue_by_asset[ctx.asset] = {}
ctx.funding_8h_by_venue = funding_by_venue_by_asset[ctx.asset]
```

Place this fetch immediately after the existing single-venue `ctx.funding_8h = funding_by_asset[ctx.asset]` line, so both fetches share the same loop iteration. The exact text to insert after the existing block:

```python
# 2026-05-20 multi-venue funding (Deribit + OKX + Bybit) — async-native
# per sync_in_async_sweep_2026-05-19. Cached per asset per tick.
if ctx.asset not in funding_by_venue_by_asset:
    try:
        from core.funding_rates_multi import get_all_funding
        funding_by_venue_by_asset[ctx.asset] = await get_all_funding(ctx.asset)
    except Exception as e:
        logger.warning(f"funding_rates_multi fetch failed {ctx.asset}: {e}")
        funding_by_venue_by_asset[ctx.asset] = {}
ctx.funding_8h_by_venue = funding_by_venue_by_asset[ctx.asset]
```

- [ ] **Step 3: Run the full crypto_observer test suite**

```bash
pytest tests/test_crypto_observer.py tests/test_funding_rates_multi.py -v
```

Expected: all tests PASS — the harness wire-up does not affect existing tests (the observer tests directly populate `ctx.funding_8h_by_venue`; the harness wire-up is only exercised in live runtime).

- [ ] **Step 4: Commit**

```bash
git add strategies/crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): wire get_all_funding into scan-tick harness

Adds async multi-venue funding fetch alongside existing Deribit-only
single-venue fetch. Async-native per sync_in_async_sweep_2026-05-19;
cached per asset per tick.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Register observer in `OBSERVER_CLASSES` + final smoke

**Files:**
- Modify: `strategies/crypto_observer.py` (1 line addition in OBSERVER_CLASSES tuple)
- Modify: `tests/test_crypto_observer.py` (1 registry smoke test)

- [ ] **Step 1: Append `CrossVenueFundingConsensusObserver` to OBSERVER_CLASSES**

In `strategies/crypto_observer.py`, locate the `OBSERVER_CLASSES` tuple (it begins with `OBSERVER_CLASSES: tuple[type[BaseObserver], ...] = (`). The tuple currently ends with several observer entries before the closing `)`. Insert these two lines immediately before the closing `)`:

```python
    # 2026-05-20 multi-venue Deribit+OKX+Bybit funding consensus/divergence
    CrossVenueFundingConsensusObserver,
```

- [ ] **Step 2: Add the registry smoke test**

Append to `tests/test_crypto_observer.py`:

```python
def test_cross_venue_funding_consensus_registered_in_observer_classes():
    """Harness picks up cross_venue_funding_consensus via OBSERVER_CLASSES."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert CrossVenueFundingConsensusObserver in OBSERVER_CLASSES
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/test_crypto_observer.py tests/test_crypto_observer_phase1.py tests/test_funding_rates_multi.py -v
```

Expected: all tests PASS, including the new registry smoke test.

- [ ] **Step 4: Verify wiring with a runtime import check**

```bash
python3 -c "from strategies.crypto_observer import OBSERVER_CLASSES, CrossVenueFundingConsensusObserver; assert CrossVenueFundingConsensusObserver in OBSERVER_CLASSES; obs = CrossVenueFundingConsensusObserver(); print(f'name={obs.name!r} ok')"
```

Expected output:
```
name='cross_venue_funding_consensus' ok
```

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto_observer.py tests/test_crypto_observer.py
git commit -m "$(cat <<'EOF'
feat(crypto_observer): register cross_venue_funding_consensus

Wires the observer into the scan-tick harness via OBSERVER_CLASSES.
Without this commit the class exists but is never instantiated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Out of scope (per spec)

- Binance USDT-M perp (4th venue). Same pattern, additive. Defer until 3-venue volume data shows whether more venues help.
- WebSocket subscriptions for funding rate. Funding is 8h cadence — 60s REST polling is adequate.
- Funding-rate-history time series. Defer until single-tick signal shows alpha.
- Predicting a directional sign for the divergence kind (currently placeholder `"yes"`). Add post-5/27 if divergence events show consistent followups.
- Phase 1 trader / market_order routing — gated on 5/27 + 6/02 eval per spec promotion decision tree.

---

## Expected commit graph after plan complete

```
* feat(crypto_observer): register cross_venue_funding_consensus
* feat(crypto_observer): wire get_all_funding into scan-tick harness
* test(crypto_observer): pin cross_venue_funding min_venues + mts gates
* test(crypto_observer): pin cross_venue_funding divergence path
* feat(crypto_observer): add cross_venue_funding_consensus Phase 0 observer
* feat(crypto_observer): EventContext.funding_8h_by_venue field
* feat(core): async cross-venue funding-rate fetcher
* docs(plan): cross_venue_funding_consensus observer implementation plan
```

7 implementation commits.

---

## Open question (per spec, defer to deploy time)

OKX and Bybit's `fundingRate` value is the rate for the next funding interval. For their default USDT-SWAP / linear-USDT products this is 8h, but on some products it can be 4h. The parsers in `funding_rates_multi.py` assume 8h-native (no normalization). Verify against live API docs at deploy time — if any venue's product differs, add a per-venue scalar in `_VENUE_PARSERS` to normalize to 8h.
