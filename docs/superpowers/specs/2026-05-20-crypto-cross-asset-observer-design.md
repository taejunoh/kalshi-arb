# Crypto Cross-Asset Observer Suite — Design

**Date**: 2026-05-20
**Owner**: taejun
**Status**: Spec — pending implementation plan
**Predecessors**: [[mid_otm_tail_observer_2026-05-20]], [[universe_expansion_2026-05-20]]
**Related**: [[new_paradigm_portfolio_2026-05-19]] (Track 1 ceiling optimization)

## Motivation

Current crypto observer portfolio (19 observers) is 100% within-market-state. None
of them compare BTC vs ETH price action or detect cross-asset information leakage.
deribit_tail_fade_v1 (the only successfully promoted Phase 1 trader) is heavily
mega-event dependent — quiet crypto days produce 5-10 fires/day vs 100+ during
BTC moves. Adding alpha sources independent of the existing
"Deribit IV — Kalshi mispricing" thesis diversifies the portfolio.

Three cross-asset hypotheses to test simultaneously in Phase 0:

1. **BTC → ETH spot lead-lag**: BTC jumps, ETH spot hasn't caught up yet,
   Kalshi ETH market underprices the catch-up direction
2. **Correlation regime break**: BTC-ETH 5-min correlation drops sharply from
   baseline → regime change, ATM buckets may mispricе
3. **Kalshi book lag**: Spot moved but Kalshi book hasn't refreshed —
   bucket-mid lag fade

Phase 0 logs all three observations with rich features. Promotion decision after
1 week of data based on per-observer cohort analysis.

## Architecture

### New module

`strategies/crypto_cross_asset_observer.py` (new file, ~250 LOC). Three
observer classes share two utility caches living in the observer harness
in `crypto_observer.py`.

Rationale for new file: `crypto_observer.py` is already 1100 LOC. Adding
3 observers + 2 utility classes + EventContext extensions would push it
to ~1400 LOC. Cross-asset thesis warrants its own namespace.

### Utility components (added to `crypto_observer.py` harness)

```python
class _CrossAssetCache:
    """Rolling per-asset price history + correlation calculation.

    Stores (ts, price) tuples for 'BTC' and 'ETH'. Window 15 min max.
    """
    def update(self, asset: str, ts: float, price: float) -> None
    def n_seconds_ago(self, asset: str, lookback_s: float, now_ts: float) -> Optional[float]
    def correlation(self, lookback_s: float, now_ts: float) -> Optional[float]


class _BookMidCache:
    """Rolling (series, strike) → deque[(ts, mid)] for book_lag observer.

    Per-bucket maxlen=60 (covers 30s window with 1s scan cadence + 30s slack).
    Stale-key pruning every 60s removes entries last-updated >120s ago.
    Hard cap 20,000 keys triggers force-flush + Telegram alert.
    """
    def update(self, series: str, strike: float, ts: float, mid: float) -> None
    def n_seconds_ago(self, series: str, strike: float, lookback_s: float, now_ts: float) -> Optional[float]
    def prune_stale(self, now_ts: float, max_age_s: float = 120.0) -> int  # returns removed count
    def size(self) -> int
```

### EventContext extensions

```python
@dataclass
class EventContext:
    # ... existing fields ...
    # NEW: cross-asset (filled identically on every ctx — both BTC and ETH series ctxs)
    spot_btc: Optional[float] = None
    spot_eth: Optional[float] = None
    spot_btc_30s_ago: Optional[float] = None
    spot_eth_30s_ago: Optional[float] = None
    spot_btc_10s_ago: Optional[float] = None
    spot_eth_10s_ago: Optional[float] = None
    corr_5min: Optional[float] = None
    corr_15min: Optional[float] = None
    book_mid_cache: Optional["_BookMidCache"] = None  # passed by reference
```

### Data flow

```
Every scan iter:
  1. CrossAssetCache.update("BTC", now, spot_btc)
     CrossAssetCache.update("ETH", now, spot_eth)
  2. corr_5min = CrossAssetCache.correlation(300, now)
     corr_15min = CrossAssetCache.correlation(900, now)
  3. For each market m in scan:
     - mid = (m.yes_bid + m.yes_ask) / 2
     - BookMidCache.update(series, strike, now, mid)
  4. _build_contexts populates cross-asset fields on every ctx
  5. observer.evaluate(ctx) iterates as before
  6. Every 60s: BookMidCache.prune_stale(); log size
```

### Observer registration

```python
# crypto_observer.py
from strategies.crypto_cross_asset_observer import (
    BtcEthLeadLagObserver, CrossAssetCorrBreakObserver, KalshiBookLagObserver,
)

OBSERVER_CLASSES = (
    # ... existing 19 ...
    BtcEthLeadLagObserver, CrossAssetCorrBreakObserver, KalshiBookLagObserver,
)
```

### Dashboard

`monitoring/dashboard/api.py` — add to `KNOWN_OBSERVERS` tuple. New health
widget `cross_asset_health` shows BTC/ETH spot age, last correlation
compute time, BookMidCache size + last prune.

## Observer specifications

### 1. `btc_eth_lead_lag`

**Thesis**: BTC moves first, ETH spot hasn't reacted yet → fade ETH Kalshi
market on the catch-up direction.

**Trigger gates**:
| Gate | Value |
|---|---|
| Lead movement | abs(Δspot_BTC over 30s) ≥ 0.3% |
| Lag movement | abs(Δspot_ETH over 30s) ≤ 0.15% |
| Target series | KXETH (primary) — log KXBTC fires too for reverse-direction analysis |
| ya | [0.05, 0.60] |
| mts_s | ≥ 300 (5 min remaining for catch-up) |
| dist_pct | |strike - spot_eth| / spot_eth < 2.0% |
| Leg kind | b_legs only |
| yb_size | ≥ 30 |

**Predicted outcome**: BTC up → bucket above ETH spot, predicted='yes'.
BTC down → bucket below, predicted='yes'.

**Features**:
```python
{
    "ya", "yb", "na", "yb_size",
    "spot_btc_now", "spot_btc_30s_ago", "spot_btc_10s_ago",
    "spot_eth_now", "spot_eth_30s_ago", "spot_eth_10s_ago",
    "delta_btc_30s_pct", "delta_eth_30s_pct",
    "delta_btc_10s_pct", "delta_eth_10s_pct",   # secondary trigger reference
    "vol_btc", "vol_eth",
    "corr_5min", "corr_15min",
    "strike", "dist_pct", "mts_s",
    "kind", "predicted_direction",
    "source_signal_hash",   # hash(now_truncated_to_15s, sign(delta_btc)) for dedup
}
```

### 2. `cross_asset_corr_break`

**Thesis**: BTC-ETH 5-min rolling correlation drops sharply from baseline →
regime break. Directionally ambiguous — log features for post-hoc analysis,
provisional predicted='no' on ATM bucket as conservative default.

**Trigger gates**:
| Gate | Value |
|---|---|
| Correlation | corr_5min < 0.4 |
| Baseline check | corr_15min ≥ 0.6 (must have come from high-correlation regime) |
| Movement | abs(Δspot_BTC 30s) ≥ 0.2% OR abs(Δspot_ETH 30s) ≥ 0.2% |
| Target series | KXBTC and KXETH both |
| Bucket | ATM bucket (\|strike - spot\| / spot < 0.5%) |
| mts_s | ≥ 300 |
| Leg kind | b_legs |

**Predicted outcome**: 'no' on ATM bucket (conservative — "ATM doesn't get
hit in regime break"). Real value is in features for post-hoc analysis.

**Features**: same as lead_lag + `is_atm_bucket` flag.

### 3. `kalshi_book_lag`

**Thesis**: Spot moved meaningfully but Kalshi bucket-mid hasn't updated
yet → bucket-mid lag fade.

**Trigger gates**:
| Gate | Value |
|---|---|
| Spot movement | abs(Δspot over 30s) ≥ 0.3% (asset of the series) |
| Book lag | abs(bucket_mid_now - bucket_mid_30s_ago) < $0.01 (1¢) |
| Target series | KXBTC + KXETH (each compares against its own spot) |
| Target bucket | Bucket whose strike is closest to current spot_now — i.e. the bucket that *should* be the ATM bucket after the spot move but whose mid hasn't repriced |
| ya | [0.10, 0.50] |
| mts_s | ≤ 1200 (close window — book lag biggest near expiry) |
| yb_size | ≥ 30 |
| Leg kind | b_legs only |

**Predicted outcome**: 'yes' on the lagging bucket (expected Kalshi catch-up).

**Features**: same as lead_lag + `bucket_mid_now`, `bucket_mid_30s_ago`,
`book_lag_cents`.

### Common Phase 0 features philosophy

All three observers log the SAME core feature set. This allows:
- Post-hoc cross-method analysis ("what if lead_lag had vol-normalized trigger?")
- Comparison cohort matching across observers
- Simple SQL queries for performance review

## Operational defenses

### Memory leak (BookMidCache)

| Layer | Mechanism |
|---|---|
| Soft watchdog | Log size every 60s, WARNING if > 10,000 keys |
| Hard cap | If > 20,000 keys, flush + Telegram alert |
| Auto-prune | deque maxlen=60 + 120s stale-key removal |
| Test | `test_book_mid_cache_hard_cap_flush`, `test_book_mid_cache_stale_key_pruning` |

### Scan latency

| Layer | Mechanism |
|---|---|
| Per-observer timing | Existing [SLOW_STRAT] pattern. Each evaluate() > 500ms logs WARNING |
| Scan cycle p95 | 60s rolling p95 > 2s → WARNING |
| Hard timeout | Cycle > 10s → skip this iter (next cycle proceeds) |
| Test | `test_observer_completes_under_100ms_with_synthetic_load` |

### Stale data

| Function | Guard |
|---|---|
| `CrossAssetCache.n_seconds_ago(asset, 30s, now)` | Nearest sample > 60s away → None |
| `CrossAssetCache.correlation` | sample count < 10 → None |
| `BookMidCache.n_seconds_ago` | Nearest sample > 60s away → None |
| Observer evaluate() | Any feature None → skip fire + log reason |
| Test | `test_n_seconds_ago_returns_none_on_data_gap` |

### Observer crash isolation

Verify and (if not present) add to scan loop:
```python
for obs_cls in OBSERVER_CLASSES:
    try:
        signals = obs_cls().evaluate(ctx)
    except Exception as e:
        logger.error(f"Observer {obs_cls.name} crashed: {e}", exc_info=True)
        continue
```
Test: `test_one_observer_crash_does_not_break_scan`.

### Dual-series double-fire

A single BTC jump can fire on both KXBTC and KXETH contexts. To distinguish
"true 2x fires" from "1 event observed in 2 series", include
`source_signal_hash` in features.

Definition: `hash((bucket_time_15s, sign(delta_btc_30s_pct), round(delta_btc_30s_pct, 3)))`
where `bucket_time_15s = int(now_ts // 15) * 15` (15-second bucketing of the
event timestamp).

Two fires sharing the same hash represent the same underlying spot event.
Post-hoc analysis can dedup or count both.

### Cross-asset data health dashboard

New widget `cross_asset_health` shows:
- BTC spot age (sec since last update)
- ETH spot age (sec)
- corr_5min last computed (sec ago)
- BookMidCache: total keys, last prune time, last prune removed count
- Health status: degraded if any spot age > 5min

## Tests

Add `tests/test_crypto_cross_asset_observer.py`:

**Utility tests:**
- `test_cross_asset_cache_correlation_basic`
- `test_cross_asset_cache_correlation_returns_none_on_sparse_data`
- `test_cross_asset_cache_n_seconds_ago`
- `test_cross_asset_cache_n_seconds_ago_returns_none_on_data_gap`
- `test_book_mid_cache_rolling_window`
- `test_book_mid_cache_stale_key_pruning`
- `test_book_mid_cache_n_seconds_ago`
- `test_book_mid_cache_hard_cap_flush`
- `test_observer_completes_under_100ms_with_synthetic_load`
- `test_one_observer_crash_does_not_break_scan`

**Observer tests (per observer):**
- `test_X_fires_on_main_thesis`
- `test_X_skips_when_thesis_invalid`
- `test_X_skips_outside_<each_gate>`
- `test_X_logs_expected_features`

Total: ~25 new tests. Combined suite: 78 existing + 25 = ~103.

## Phase 0 → Phase 1 promotion gates

Eval after 1 week (target 2026-05-27, or later if n insufficient):

| Observer | Trigger criteria |
|---|---|
| `btc_eth_lead_lag` | n ≥ 50 settled, WR ≥ 70% in mts_s ≤ 1800 cohort with fee-positive ya (≤ 0.50) sub-cohort |
| `cross_asset_corr_break` | Features distribution analysis. If alpha sub-cohort identified (e.g., post-break ATM bucket WR ≥ 70%), revise thesis + log directional → re-promote |
| `kalshi_book_lag` | n ≥ 50, WR ≥ 75% (cleaner thesis warrants higher bar) with fee-positive ya |

Below threshold:
- n insufficient → continue Phase 0 (add 1 week)
- WR insufficient → archive features, observer deprecated. Features stay in DB
  for offline backtesting on alternative triggers.

Above threshold:
- Phase 1 paper trader following the existing pattern
  (depth-aware sizing, LIMIT_TTL routing, paper-only at launch)
- Phase 2 eval after additional 2 weeks paper data

## Effort estimate

| Task | Time |
|---|---|
| EventContext extensions + CrossAssetCache | 0.5 day |
| BookMidCache (with hard cap defenses) | 0.5 day |
| Observer 1 (lead_lag) | 0.5 day |
| Observer 2 (corr_break) | 0.5 day |
| Observer 3 (book_lag) | 0.5 day |
| Tests (25) | 0.5 day |
| Dashboard health widget | 0.25 day |
| Deploy + telemetry verify | 0.25 day |
| **Total** | **~3.5 days** |

## Out of scope

- Phase 1 paper trader implementation (separate spec after promotion gate)
- T-leg variant (cross-asset thesis on daily contracts) — defer until B-leg
  data proves the thesis
- Multi-asset (SOL/XRP) extension — out of scope for v1
- Vol-normalized trigger as primary (logged as feature for sub-cohort
  analysis)
- Trading on Polymarket / external venues (NY resident blocks)

## Open questions resolved during brainstorm

1. **Why 3 separate observers, not 1 with rich features?** — corr_break and
   book_lag have genuinely different trigger conditions (corr deviation w/o
   spot move, book lag w/o spot lag asymmetry). lead_lag stays unified
   with vol-normalized as a logged feature, not a separate observer.

2. **Why b_legs only, not t_legs?** — Cross-asset catch-up bets need a
   specific bucket near the expected post-move spot. Cumulative T markets
   don't map naturally to "catch-up bucket" thesis. T-leg variant deferred
   to v2 if v1 succeeds.

3. **Why dual-series logging instead of single-series?** — Allows reverse
   lead-lag observation (ETH leading BTC during ETH-specific catalysts).
   Post-hoc dedup via source_signal_hash.

4. **Why 5-min correlation window?** — Short enough to detect regime
   changes, long enough for stable estimate (>10 samples at kline 1m
   cadence). Logged 15-min as baseline reference.

5. **Why memory hard-cap defenses for BookMidCache?** — t3.small has 2GB
   RAM. Bot RSS currently ~530-585MB. A pruning bug could leak hundreds
   of MB before noticed. Hard cap + Telegram alert ensures bounded blast
   radius.
