# Cross-Venue Funding Consensus Observer — Design

**Date**: 2026-05-20
**Owner**: taejun
**Status**: Spec — pending implementation plan
**Predecessors**: [[phase0_funding_skew_observers_2026-05-19]]
**Related**: `FundingRateFadeObserver` (single-venue Deribit sibling)

## Motivation

The existing `FundingRateFadeObserver`
([strategies/crypto_observer.py:1101](../../strategies/crypto_observer.py#L1101))
reads a single funding rate from Deribit
([core/deribit_extras.py:45](../../core/deribit_extras.py#L45)). Phase 0
eval 5/26 + 6/02.

Single-venue funding has known noise sources:
- Deribit BTC-PERPETUAL volume is ~10% of Binance / OKX combined.
  Funding can spike for Deribit-specific positioning that does not
  reflect aggregate market state.
- Funding rate cadence is 8h; venue-specific quote freezes during
  illiquid windows produce stale extremes.
- Funding rate is a price of perp basis — venue-specific basis
  reflects venue-specific arb constraints (e.g., US KYC limits at
  Bybit, Deribit's options-heavy user base).

A multi-venue consensus observer should:
1. Detect when **all venues agree** on funding direction +
   magnitude → high-confidence positioning signal, stronger fade thesis.
2. Detect when **venues diverge** sharply → idiosyncratic positioning
   on one venue, weak/noisy signal — should be filtered out, or
   logged as a separate observer for analysis.

This observer is **independent of** `FundingRateFadeObserver`'s
promotion fate: even if the single-venue Deribit signal dies at
6/02, the cross-venue consensus thesis is a distinct hypothesis worth
its own Phase 0 measurement.

**Priority note**: of the three brainstorm follow-ups, this has the
highest effort (2+ days) and the noisiest expected signal (most
moving parts). Sequenced last accordingly.

## Architecture

### New module

`core/funding_rates_multi.py` (new file, ~120 LOC). Venue-agnostic
funding-rate fetcher. Co-exists with `deribit_extras.py` (which
remains the canonical Deribit source for `iv_skew` and the existing
single-venue funding observer).

Rationale for new file: `deribit_extras.py` is purpose-built for
Deribit endpoints. Adding OKX + Bybit branches would mix venue
concerns. A clean venue-agnostic interface is easier to test and
extend (e.g., Binance later).

### Module shape

```python
# core/funding_rates_multi.py
from __future__ import annotations
import asyncio
import time
from typing import Optional

VENUES = ("deribit", "okx", "bybit")
_TTL_S = 60.0

# Cache: (venue, asset) -> (epoch_fetched, value | None)
_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}

# Venue endpoints — funding_8h returned as signed decimal
# Deribit:  /public/ticker?instrument_name=BTC-PERPETUAL → result.funding_8h
# OKX:      /api/v5/public/funding-rate?instId=BTC-USDT-SWAP → data[0].fundingRate (×8 for 8h)
# Bybit:    /v5/market/tickers?category=linear&symbol=BTCUSDT → result.list[0].fundingRate (×8 for 8h)

async def get_funding(asset: str, venue: str) -> Optional[float]: ...
    # Returns funding rate normalized to 8h decimal (signed).
    # OKX/Bybit return per-funding-period rate; we multiply to 8h-equivalent.

async def get_all_funding(asset: str) -> dict[str, Optional[float]]:
    # Parallel fetch via asyncio.gather over VENUES.
    # Each venue isolated: one failure does not block others.
    # Returns {"deribit": 0.0005, "okx": 0.0003, "bybit": None}
```

**Implementation note** (per [[sync_in_async_sweep_2026-05-19]]): use
`httpx.AsyncClient` for fetches, NOT sync `httpx.get()`. The
crypto_observer harness now runs in its own `scan_loop` task; sync
HTTP would block other observers' evaluation.

### EventContext extension

```python
@dataclass
class EventContext:
    # ... existing fields ...
    # 2026-05-20 cross-venue funding (filled by harness once per tick per asset)
    funding_8h_by_venue: dict[str, Optional[float]] = field(default_factory=dict)
```

The existing `funding_8h: Optional[float]` field is **kept** (Deribit
backward compat for `FundingRateFadeObserver`). The new field is
additive. Harness populates both: `funding_8h = funding_8h_by_venue.get("deribit")`.

### Harness integration

In `crypto_observer.py` event-context build path (once per scan tick
per asset, not per leg):

```python
# Existing single-venue path (kept):
funding_8h = await asyncio.to_thread(get_funding_rate, asset)
# New multi-venue path:
funding_by_venue = await get_all_funding(asset)  # async-native
```

Both run concurrently inside the same tick. Cost ≈ 3 parallel HTTP
calls vs 1; with `_TTL_S = 60` and tick cadence ≈ 5s, that's
3 fetches every 60s = 0.05 calls/s/asset = trivial.

### New observer

`CrossVenueFundingConsensusObserver` added to `crypto_observer.py`:

```python
CROSS_VENUE_FUNDING_MIN_VENUES = 2          # need ≥2 venues to report
CROSS_VENUE_FUNDING_CONSENSUS_ABS_MIN = 0.0002  # ≥0.02%/8h on agreeing venues
CROSS_VENUE_FUNDING_DIVERGENCE_MIN = 0.0005     # ≥0.05%/8h max-min spread
CROSS_VENUE_FUNDING_MTS_MAX_S = 7200            # ≤2hr to close (same as funding_rate_fade)
CROSS_VENUE_FUNDING_MIN_YA = 0.05
```

Trigger logic per `EventContext` tick:

1. Skip if `ctx.close_time_secs is None` or `> CROSS_VENUE_FUNDING_MTS_MAX_S` or `≤ 0`.
2. Filter `funding_8h_by_venue` to non-None values → `rates`.
3. Skip if `len(rates) < CROSS_VENUE_FUNDING_MIN_VENUES`.
4. Compute:
   - `signs = [sign(r) for r in rates.values()]`
   - `all_agree_sign = len(set(signs)) == 1`
   - `min_abs = min(abs(r) for r in rates.values())`
   - `spread = max(rates.values()) - min(rates.values())`
   - `consensus_sign = signs[0] if all_agree_sign else 0`
5. **Consensus signal** (when all venues agree):
   - If `all_agree_sign and min_abs ≥ CROSS_VENUE_FUNDING_CONSENSUS_ABS_MIN`:
     - For each `leg` in `ctx.b_legs` with `leg.ya ≥ MIN_YA`:
       - Apply same directional logic as `FundingRateFadeObserver`
         (consensus_sign > 0 → fade upside, pick strikes > spot;
         consensus_sign < 0 → fade downside, pick strikes < spot)
       - Emit `kind="consensus_fade"`, `predicted_outcome="no"`.
6. **Divergence signal** (when venues disagree sharply):
   - If `spread ≥ CROSS_VENUE_FUNDING_DIVERGENCE_MIN`:
     - Emit one signal per event (not per leg) with no directional
       prediction — `predicted_outcome="yes"` (placeholder),
       `kind="divergence"`. Phase 0 records the divergence event for
       post-hoc analysis: does divergence precede a vol expansion?
       A Kalshi-side mispricing?

### Output

```python
Signal(
    observer="cross_venue_funding_consensus",
    series=ctx.series,
    event_key=ctx.event_key,
    market_id=leg.ticker,   # or ctx.b_legs[atm_idx].ticker for divergence
    predicted_outcome="yes" | "no",
    features={
        "kind": "consensus_fade" | "divergence",
        "venues_reporting": list(rates.keys()),  # e.g. ["deribit", "okx", "bybit"]
        "funding_deribit": rates.get("deribit"),
        "funding_okx": rates.get("okx"),
        "funding_bybit": rates.get("bybit"),
        "min_abs": round(min_abs, 6),
        "spread": round(spread, 6),
        "consensus_sign": consensus_sign,  # -1, 0, +1
        # For consensus_fade only:
        "ya": leg.ya,
        "no_ask": leg.na,
        "strike": leg.threshold,
        "side": "fade_upside" | "fade_downside",
        # Always:
        "spot": ctx.spot,
        "mts_s": round(ctx.close_time_secs, 1),
    },
)
```

## Distinction from sibling observer

| | funding_rate_fade | cross_venue_funding_consensus |
|---|---|---|
| Venues | Deribit only | Deribit + OKX + Bybit |
| Gate | `abs(funding) ≥ MIN_ABS` (single rate) | `all_agree AND min_abs ≥ MIN_ABS` |
| Extra signal | none | divergence event |
| Failure mode | Deribit endpoint down → 0 signal | Any 1-venue down → degraded, still fires with ≥2 |

## Data flow

```
crypto_observer scan tick:
  → for asset in [BTC, ETH]:
       → asyncio.gather(
           get_funding_rate_deribit(asset),    # existing sync via to_thread
           get_all_funding(asset),              # new async multi-venue
         )
       → EventContext.funding_8h_by_venue = result
       → CrossVenueFundingConsensusObserver.evaluate(ctx)
       → Signals → db.record_crypto_observer_signal(...)
```

## Error handling

- Any individual venue's HTTP fetch fails → that venue's rate is
  `None`. Other venues still populate. Observer requires
  `MIN_VENUES = 2` non-None to emit.
- All venues fail → empty `funding_8h_by_venue`, observer silent skip.
- Venue API schema change → defensive `float()` cast + `try/except`
  per-venue parse path. Log at WARN once per venue per minute (avoid
  log spam).
- Endpoint rate-limit (HTTP 429) → existing `httpx.AsyncClient`
  default retries + 60s TTL cache absorb most cases. No backoff
  beyond TTL.
- DB write failure → existing path (try/except logging).

## Testing

3 tests in `tests/test_crypto_observer.py` + 2 tests in new
`tests/test_funding_rates_multi.py`:

```python
# test_funding_rates_multi.py
async def test_get_all_funding_parallel_one_failure(monkeypatch):
    # Mock OKX to fail, deribit + bybit succeed → returns dict with okx=None
    ...

async def test_okx_per_funding_period_normalized_to_8h(monkeypatch):
    # OKX returns 0.0001 per 4h period → expect 0.0002 in our 8h-normalized output
    ...

# test_crypto_observer.py
def test_cross_venue_funding_consensus_fires_on_agreement():
    ctx = _build_ctx(funding_8h_by_venue={"deribit": 0.0005, "okx": 0.0004, "bybit": 0.0006},
                     spot=70_000, mts_s=300, b_legs=[_leg(strike=71_000, ya=0.10)])
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)
    assert any(s.features["kind"] == "consensus_fade" for s in sigs)

def test_cross_venue_funding_divergence_fires():
    ctx = _build_ctx(funding_8h_by_venue={"deribit": 0.0008, "okx": -0.0003, "bybit": 0.0001},
                     spot=70_000, mts_s=300, b_legs=[_leg(strike=70_000, ya=0.50)])
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)
    div_sigs = [s for s in sigs if s.features["kind"] == "divergence"]
    assert len(div_sigs) == 1
    assert div_sigs[0].features["spread"] == pytest.approx(0.0011, abs=1e-5)

def test_cross_venue_funding_skips_below_min_venues():
    ctx = _build_ctx(funding_8h_by_venue={"deribit": 0.0005})
    assert CrossVenueFundingConsensusObserver().evaluate(ctx) == []
```

## Promotion criteria (5/27 + 6/02 eval, sequenced after #1 + #2)

After 1-2 weeks of data:

1. **Volume check**: `consensus_fade` fires per day ≥ 30 (need data
   density). If far below, relax `CONSENSUS_ABS_MIN`.
2. **Per-kind WR**: consensus_fade vs `funding_rate_fade` (single-venue
   sibling) — does consensus filter improve WR materially? Target:
   consensus_fade WR ≥ single-venue WR + 10pp on matched cohort.
3. **Divergence eval**: out of all divergence events, what % were
   followed by:
   - A Kalshi market-side mispricing >$0.05?
   - A spot move >1% within 30 min?
   If neither correlation holds, divergence is noise — retire that
   signal kind, keep consensus only.
4. **Fee-adjusted EV per [[fee_adjusted_promotion_criteria_2026-05-20]]**
   on the consensus_fade cohort.

**Decision tree**:
- Consensus_fade WR materially better than single-venue + fee-positive
  sub-cohort → promote `cross_venue_funding_v1` Phase 1 paper trader;
  retire `FundingRateFadeObserver`.
- Both equivalent → retire this observer (multi-venue infra not
  paying for itself).
- Single-venue better → retire this observer, keep
  `FundingRateFadeObserver`.

## Files to touch

1. [core/funding_rates_multi.py](../../core/) — new file (~120 LOC).
2. [strategies/crypto_observer.py](../../strategies/crypto_observer.py)
   — new constants (5 lines), new `CrossVenueFundingConsensusObserver`
   class (~80 LOC), EventContext extension (1 field), harness wire-up
   (~5 lines in scan tick).
3. [tests/test_crypto_observer.py](../../tests/) — 3 new tests.
4. [tests/test_funding_rates_multi.py](../../tests/) — new file
   (~80 LOC), 2 tests.

No DB schema change (signals table absorbs new observer name +
features JSON).

## Out of scope

- Binance USDT-M perp (4th venue). Same pattern, additive. Defer until
  3-venue volume data shows whether more venues help.
- WebSocket subscriptions for funding rate. Funding is 8h cadence —
  60s REST polling is more than adequate. WS would complicate
  reconnect/auth handling for no signal benefit.
- Funding-rate-history time series (e.g., "funding has been positive
  for 3 consecutive 8h periods"). Defer until single-tick signal
  shows alpha.
- Predicting a directional sign for the divergence kind (currently
  placeholder `"yes"`). Add post-5/27 if divergence events show
  consistent followups.

## Open question (RESOLVED 2026-05-20)

All three venues' BTC/ETH default-product funding rates verified 8h-native:
- **Deribit** BTC/ETH-PERPETUAL: `funding_8h` field, explicit in payload.
- **OKX** BTC/ETH-USDT-SWAP: derived from live API response
  `nextFundingTime - fundingTime = 28,800,000 ms = 8h` (verified via
  `curl https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP`).
- **Bybit** BTCUSDT/ETHUSDT linear: docs state `fundingInterval = 480 min`
  and `fundingIntervalHour = "8"` for these symbols.

No normalization scalar required. Comments in `_parse_okx` and `_parse_bybit`
record the verification date and source.

Re-verify if Bybit/OKX ever expand to altcoin perps that use 4h or 2h
intervals — the parser would silently treat those as 8h-native and
distort cross-venue comparisons.

## Sequencing note

This spec is the **third** of three brainstorm follow-ups committed
2026-05-20. Recommended deploy order:

1. [[2026-05-20-settlement-edge-literal-observer-design]] — lowest effort, no external deps.
2. [[2026-05-20-strike-ladder-literal-observer-design]] — medium effort, no external deps.
3. **This spec** — highest effort (new module + new EventContext field +
   3 external venue APIs). Build last; can absorb implementation
   lessons from #1 and #2 (e.g., test fixture patterns).

If time is tight, ship #1 + #2 first and defer #3 until they're
deployed and stable.
