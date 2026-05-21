# settlement_edge v2 + depth_skew_flip observer design

**Date:** 2026-05-21
**Author:** brainstorm session w/ Claude
**Status:** Draft for review

## Background

`SettlementEdgeObserver` was deployed 2026-05-20 as part of the brainstorm arc
([[2026-05-20_evening_brainstorm_arc]]). Thesis: as market makers withdraw
quotes in the final 30 seconds before settlement, the per-leg bid/ask depth
asymmetry signals the resolution direction.

A 24h cohort (n=207 fires, n=201 settled) was audited 2026-05-21. The raw WR
is 98.5%, but the audit revealed the WR is misleading:

| Metric | Value |
| --- | --- |
| Total fires | 207 |
| Settled | 201 |
| Raw WR | 98.5% |
| Predicted-side price = $1.00 at fire | 187 / 201 (93%) |
| Tradable fires (0 < entry < 1) | 14 |
| Tradable WR | 78.6% |
| Average entry price | $0.74 |
| Break-even WR @ avg price (FEE 0.07) | 78.8% |
| Simulated ROI (fee-adjusted) | **−0.31%** |

The thesis is technically correct — bid/ask depth-skew does predict the
resolution at very high accuracy. But by `mts_s ≤ 30s`, the market has
already collapsed the predicted side to $1.00. We can observe the signal
but cannot trade it profitably.

Further empirical findings:

- 188 / 201 fires (94%) clustered in `mts_s ∈ [20, 30]` — the *outer* edge
  of the window. MMs only pull in the very last seconds, so widening the
  window must be paired with a price gate to be meaningful.
- 197 / 201 fires (98%) had `|skew| ≥ 0.80` — the existing `0.20` threshold
  is non-binding noise floor. Tightening loses almost nothing.
- 15 unique events — essentially BTC/ETH hourly close. Universe is narrow
  but adequate for a paper observer.

## Goals

1. **Retain the validated thesis** (per-leg depth-skew predicts direction)
   while moving the observation point to where trades are actually possible.
2. **Add a sister observer** that captures the dynamic, event-driven
   counterpart — the *moment* of depth-skew sign flip, distinct from the
   static state. Justification: [[feedback-sister-strategy-disjoint-signal]]
   — static-state and dynamic-event are disjoint signal classes deserving
   independent Phase 0 accountability.

## Non-goals

- Promotion to Phase 1 is out of scope for this design. Both observers
  ship as Phase 0 paper-only.
- Live trading thresholds, dashboard wiring, and `LIVE_PROMOTABLE_OBSERVERS`
  updates are deferred until the 1-week eval results.
- Cross-asset signals (spot move triggers) are out of scope —
  `KalshiBookLagObserver` already covers that for the price dimension.

## Design

### Part B: `settlement_edge_v2` (in-place retune + rename)

Modify `SettlementEdgeObserver` in `strategies/crypto_observer.py`:

```python
# Constants (replace existing settlement_edge block)
SETTLEMENT_EDGE_MTS_MIN_S = 60       # exclude collapse zone
SETTLEMENT_EDGE_MTS_MAX_S = 600      # 10-minute pre-close window
SETTLEMENT_EDGE_ATM_RADIUS = 2       # unchanged
SETTLEMENT_EDGE_MIN_SIDE_SIZE = 5    # raised from 1 — phantom guard
SETTLEMENT_EDGE_ABS_SKEW_MIN = 0.40  # raised from 0.20 — non-binding floor
SETTLEMENT_EDGE_ENTRY_PRICE_MIN = 0.40
SETTLEMENT_EDGE_ENTRY_PRICE_MAX = 0.85

# Observer name
name = "settlement_edge_v2"
```

`evaluate()` body:

1. Reject if `close_time_secs` outside `[MTS_MIN_S, MTS_MAX_S]`.
2. Find ATM bucket. For each leg in `ATM ± RADIUS`:
   - Skip if `ya_size + yb_size ≤ MIN_SIDE_SIZE`.
   - Compute `skew = (ya_size - yb_size) / (ya_size + yb_size)`.
   - Skip if `|skew| < ABS_SKEW_MIN`.
   - Determine `predicted` from skew sign (same rule as v1):
     `skew > 0 → "no"`, `skew < 0 → "yes"`.
   - Compute `entry_price = leg.ya if predicted == "yes" else leg.na`.
   - Skip if `entry_price` outside `[ENTRY_PRICE_MIN, ENTRY_PRICE_MAX]`.
   - Emit signal with features including `skew, ask_size, bid_size, ya,
     yb, na, mts_s, bucket_dist_from_atm, spot, strike, entry_price`.

**Rationale for `entry_price` filter:** v1 data proved 93% of fires sit
at the predicted-side $1.00 wall. The `[0.40, 0.85]` band is wide enough
to admit both moderately-skewed mid-range bets and high-conviction near-
resolution bets while excluding the un-tradeable wall on either end. This
overlaps with `atm_fade` (0.40-0.65) and `deribit_atm_fade` (0.30-0.70)
in price space, but settlement_edge_v2 stays distinct via its **per-leg
depth-skew** trigger (atm_fade gates on price alone; `bid_imbalance` sums
sizes across legs rather than per-leg ratios).

**Observer rename:** v1 signals stay in the DB under `observer =
'settlement_edge'` for historical lookup. v2 fires under
`observer = 'settlement_edge_v2'`. Phase 0 audit scripts (`phase0_observer_eval_alarm.py`
and any future ones) treat them as separate cohorts. v1 is effectively
retired — its raw signals remain queryable, but no further audit will
roll up across the v1 / v2 boundary.

**v1 dashboard:** the dashboard currently has no special wiring for
`settlement_edge`; new entries with the v2 name will appear naturally
in any Phase 0 listing that groups by `observer`. The comment on
`LITERAL_MTS_MIN_S` in `crypto_observer.py:167` ("exclude settlement_edge
window overlap") remains accurate because v2 still starts at `MTS_MIN_S =
60` and the existing `60` overlap floor is unchanged.

### Part C: `DepthSkewFlipObserver` (new sister observer)

A dynamic counterpart to settlement_edge_v2. Where v2 fires on **steady-
state** depth skew, this observer fires on a **sign-flip event** in
per-leg depth skew over a rolling window.

**Thesis:** when a market maker reverses its lopsided depth (e.g. ask was
much thicker than bid 30s ago, but now bid is much thicker than ask), the
reversal itself is the alpha-bearing event — independent of the absolute
magnitude. The latest direction is more informative than the prior one.

**Trigger:** a leg's current skew has opposite sign to its skew 30s ago,
with both endpoints satisfying a magnitude floor.

#### New module-level helper

Add `_DepthSkewCache` to `strategies/crypto_observer.py`, mirroring the
`_BookMidCache` pattern in `crypto_cross_asset_observer.py`:

```python
class _DepthSkewCache:
    """Rolling (series, strike) → deque[(ts, skew)] for sign-flip detection."""
    DEQUE_MAXLEN = 60                # ~60s window @ 1Hz + slack
    DEFAULT_HARD_CAP = 20_000
    STALE_GUARD_MULTIPLIER = 2.0

    # update(series, strike, ts, skew) — appends, evicts on maxlen, hard-caps
    # n_seconds_ago(series, strike, lookback_s, now_ts) — same semantics as BookMidCache
    # prune_stale(now_ts, max_age_s=120.0) — drop stale keys
```

Wire the cache into `CryptoObserverStrategy.__init__` next to the existing
`_BookMidCache` instance. Update it once per `EventContext` (in the same
loop that updates BookMidCache), before observers evaluate.

The cache lives on the strategy instance and is passed via `EventContext`
the same way `book_mid_cache` is already plumbed (see line 1651 comment).

`EventContext` gains an optional `depth_skew_cache: Optional[_DepthSkewCache]`
field. Backward-compatible default `None`.

#### New constants

```python
DEPTH_SKEW_FLIP_LOOKBACK_S = 30          # how far back to compare
DEPTH_SKEW_FLIP_MIN_BEFORE = 0.20        # |skew_old| ≥ this
DEPTH_SKEW_FLIP_MIN_AFTER  = 0.20        # |skew_now| ≥ this
# Rationale: settlement_edge_v2 uses 0.40 because steady-state skew must be
# strong to carry information; a full 0.40→0.40 flip is rare event-wise
# (v1 data has few such flips at 1Hz). The flip observer accepts moderate-
# to-moderate transitions (0.20 floor) because the *event* of crossing
# zero is the alpha — magnitude pre/post is secondary.
DEPTH_SKEW_FLIP_MTS_MIN_S = 60           # align with settlement_edge_v2
DEPTH_SKEW_FLIP_MTS_MAX_S = 600          # align with settlement_edge_v2
DEPTH_SKEW_FLIP_ATM_RADIUS = 2
DEPTH_SKEW_FLIP_MIN_SIDE_SIZE = 5
DEPTH_SKEW_FLIP_ENTRY_PRICE_MIN = 0.40
DEPTH_SKEW_FLIP_ENTRY_PRICE_MAX = 0.85
```

#### New observer class

```python
class DepthSkewFlipObserver(BaseObserver):
    """Phase 0: per-leg depth-skew sign-flip event detector.

    Sibling to SettlementEdgeObserver (static state). This observer measures
    the **change**: skew flipped sign over LOOKBACK_S seconds with both
    endpoints exceeding the magnitude floor. Prediction follows the sign of
    the CURRENT skew (the new direction the MM is biased toward).
    """
    name = "depth_skew_flip"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        # 1. mts window gate
        # 2. atm cluster
        # 3. for each leg:
        #    a. compute skew_now
        #    b. fetch skew_old from ctx.depth_skew_cache.n_seconds_ago(...)
        #    c. require |skew_old| ≥ MIN_BEFORE, |skew_now| ≥ MIN_AFTER
        #    d. require sign(skew_old) ≠ sign(skew_now)
        #    e. predicted = "no" if skew_now > 0 else "yes"
        #    f. entry_price gate (0.40 ≤ ya/na ≤ 0.85)
        # 4. emit signal with features: skew_now, skew_old, ask_size, bid_size,
        #    ya, yb, na, mts_s, bucket_dist_from_atm, spot, strike, entry_price,
        #    lookback_s
```

#### Cache update integration

The strategy's per-tick loop must update `_DepthSkewCache` for each
B-leg in every observed event before invoking observers. The update value
is the leg's current skew (`(ya_size - yb_size) / (ya_size + yb_size)`,
with `ya_size + yb_size > MIN_SIDE_SIZE` guard — degenerate empty books
do not produce cache entries). Update site: the same loop in
`CryptoObserverStrategy` that calls `self._book_mid_cache.update(...)`
(currently around `strategies/crypto_observer.py:1843`).

`prune_stale` is invoked alongside the existing BookMidCache prune call
(`crypto_observer.py:1850`). `max_age_s = 120.0` to match.

The hard-cap WARN log path mirrors `_BookMidCache` (`crypto_observer.py:1858`)
— add a parallel `[DEPTH_SKEW_CACHE_SIZE_WARN]` at the same threshold
(`size() > 10_000`).

### Files touched

| File | Changes |
| --- | --- |
| `strategies/crypto_observer.py` | constants rewrite for settlement_edge block; `SettlementEdgeObserver.name = "settlement_edge_v2"` + body update; add `_DepthSkewCache` + `DepthSkewFlipObserver`; register in `OBSERVER_CLASSES`; wire cache into `CryptoObserverStrategy` and `EventContext`; add per-tick update + prune calls. |
| `tests/test_crypto_observer.py` | rename existing settlement_edge tests for v2 + add coverage for new constraints (mts band, entry-price gate, raised skew threshold); add new test module section for `DepthSkewFlipObserver` (cache hit/miss, flip detection, mts/entry gates, registration). |
| `scripts/phase0_observer_eval_alarm.py` | add `settlement_edge_v2` + `depth_skew_flip` to the eval list when ready for the 1-week verdict. Anchor `COHORT_START_ISO` = deploy timestamp of v2 commit. |
| `docs/superpowers/specs/2026-05-21-settlement-edge-v2-and-depth-skew-flip-design.md` | this file |

No dashboard, no `LIVE_PROMOTABLE_OBSERVERS`, no systemd timers in this change.

### Test plan

**Unit tests for settlement_edge_v2:**

1. fires when `mts_s = 300` with valid skew + entry in band
2. skips when `mts_s = 30` (below MIN)
3. skips when `mts_s = 700` (above MAX)
4. skips when `|skew| = 0.30` (below new threshold)
5. fires when `|skew| = 0.40` (at threshold)
6. skips when predicted-side price = $0.95 (above MAX)
7. skips when predicted-side price = $0.20 (below MIN)
8. fires when entry price = $0.60 (in band)
9. skips when total leg size = 5 (at MIN_SIDE_SIZE — boundary is `≤`)
10. fires when total leg size = 6
11. negative-skew → predicts "yes"; positive-skew → predicts "no"
12. registered in `OBSERVER_CLASSES`

**Unit tests for depth_skew_flip:**

1. cache miss → no fire
2. cache returns matching-sign skew → no fire
3. cache returns opposite-sign skew with both magnitudes ≥ 0.20 → fires
4. cache returns opposite-sign skew with old magnitude 0.15 → no fire
5. cache returns opposite-sign skew with current magnitude 0.15 → no fire
6. mts/entry-price/atm-radius gates mirror settlement_edge_v2 tests
7. predicted direction follows CURRENT skew sign (not old)
8. registered in `OBSERVER_CLASSES`

**Unit tests for `_DepthSkewCache`:**

1. update/n_seconds_ago round-trip
2. STALE_GUARD_MULTIPLIER rejection when gap > 2 × lookback
3. hard-cap flush triggers
4. prune_stale drops keys older than `max_age_s`

**Existing test maintenance:**

The 9 existing settlement_edge tests need updates — all of them currently
expect `mts_s = 25` and skew threshold 0.20. Migrate to v2 semantics
rather than keep a v1 test surface (v1 observer is effectively retired —
no v1 code path remains).

### Evaluation plan

T+7d (2026-05-28):

- Add both observers to `phase0_observer_eval_alarm.py` `OBSERVERS` list
  (or run an equivalent ad-hoc audit) with `COHORT_START_ISO` set to the
  v2 deploy timestamp.
- Apply existing fee-adjusted-EV criteria
  ([[fee-adjusted-promotion-criteria-2026-05-20]]): WR alone is not
  sufficient; require pooled fee-adjusted ROI ≥ +5% and `n_settled ≥ 30`.
- Per-skew-bucket and per-mts-bucket breakdowns same as v1 audit script.
- Decision tree:
  - both observers ROI ≥ +5% → propose Phase 1 promotion plan
  - one positive / one negative → keep positive in Phase 0, retire negative
  - both negative → retire both, document negative result

T+14d (2026-06-04):

- Re-eval if n was insufficient at T+7d.
- Update [[phase1-lifetime-audit-sweep-2026-05-21]] memory entry.

### Risks

1. **Universe still narrow.** The 24h v1 cohort hit only 15 unique events
   (BTC/ETH hourly). Widening `mts` to 600s may multiply fires per event
   but does not expand the universe. ROI signals from <10 unique events
   are sample-thin; the eval must report `unique_events` count.
2. **Overlap with `atm_fade` / `deribit_atm_fade` in price space.** Both
   sit in `mts ≤ 300-600s` with `ya ∈ [0.30, 0.70]`. The depth-skew gate
   is the differentiator. If after 1 week, settlement_edge_v2's fires are
   nearly all also-fires of atm_fade, the marginal alpha is questionable
   — eval must compute the same-tick overlap with sibling observers.
3. **Cache memory growth.** `_DepthSkewCache` adds another dict in the
   strategy process. Memory footprint is bounded by the hard cap
   (20 000 keys × 60 deque entries × ~24 bytes ≈ 30 MB worst case).
   Acceptable on the EC2 t3.small. The cap-flush alert mirrors the
   BookMidCache pattern.
4. **Sign-flip noise on small skews.** A leg whose `skew` oscillates around
   0 between −0.21 and +0.21 would fire repeatedly. The 0.20 magnitude
   floor on both endpoints filters most of this. If post-deploy data shows
   >5 fires per leg per hour, raise the floor.

### Open questions

None at design time. All param values are committed (see Background and
Design sections). Tuning belongs in the T+7d eval, not in this design.

### Memory entries to create after merge

- `settlement_edge_v2_retune_2026-05-21.md` — links this design to the
  audit, the v1 cohort numbers, and the eval window.
- `depth_skew_flip_observer_2026-05-21.md` — new sister, links to
  [[feedback-sister-strategy-disjoint-signal]].
