# Sports Pre/Live Entry Tagging — Design

**Date**: 2026-04-14
**Status**: Draft (design approved, not yet implemented)

## Problem

Sports strategies have been silently entering both pre-game and in-play (live) markets without any filter. Analysis of V3 settled trades (last 30 days, 191 trades with 97/97 unique markets resolved via Kalshi `close_time`) shows:

- **Pre-game: 94 trades, W% 44.7, +$13.36 (+$0.142/trade)**
- **Live: 97 trades, W% 17.5, -$122.63 (-$1.264/trade)**

Live entries account for essentially 100% of sports losses. V31 / consolidated / liquidity_filtered / pinnacle_edge / v4_edge are newer and have no settled trades yet, but entry-timing analysis shows several of them (longshot_v31 67% live, midshot_v31 48% live, liquidity_filtered 48% live) inherit the same live-entry pattern.

Before deciding whether to block live entries, we want to **tag entries with pre/live context, collect more data (including V31/new strategies once settled), and compare performance over time.**

## Goals

- Record a numeric entry-phase signal on every sports trade: `minutes_to_start` = `commence_time − trade_timestamp` (minutes, float, positive = pre-game).
- Expose `pre` vs `live` PnL/W% splits per sports strategy on the dashboard.
- Backfill existing sports trades so the dashboard immediately shows meaningful history.
- **Do not block live entries** in this spec. Gating is a follow-up decision once data is collected.

## Non-goals

- Automatic live-entry blocking (future work).
- Telegram alerts on live PnL drawdown (future work).
- Non-sports strategies (no `commence_time` concept — column stays NULL).

## Design

### 1. Data model

Add one column to `trades`:

```sql
ALTER TABLE trades ADD COLUMN minutes_to_start REAL;
```

- Value: `(commence_time_utc − trade_timestamp_utc).total_seconds() / 60`.
- Positive → pre-game. Zero or negative → live.
- NULL for non-sports strategies and for sports trades predating backfill failure.
- Classification derived at query time:
  `CASE WHEN minutes_to_start > 0 THEN 'pre' ELSE 'live' END`.

**Cutoff decision**: `0 min` (scheduled `commence_time`). Rationale and alternatives considered in "Design decisions" below.

### 2. Trade-time tagging (new trades)

All sports strategies subclass `SportsTraderStrategy`, which already fetches Odds API data via `_fetch_odds(sport)`. The Odds API response includes `commence_time` per game — currently unused.

Changes:

- `core/database.py` — `record_trade()` signature extended with optional `minutes_to_start: Optional[float] = None` parameter; INSERT statement updated.
- `strategies/sports_trader.py` — add shared helper:
  ```python
  def _compute_minutes_to_start(game: dict) -> Optional[float]:
      """Given an Odds API game dict, return minutes until commence_time
      from 'now'. None if commence_time missing or unparseable."""
  ```
  All sports subclasses call this immediately before `record_trade(...)` and pass the result.

### 3. Backfill (existing ~445 sports trades)

One-shot script: `scripts/backfill_entry_minutes.py`.

Behavior:

1. Query trades where `strategy LIKE 'sports_%' AND minutes_to_start IS NULL`.
2. For each trade, resolve game start time:
   - **MLB** (`KXMLBGAME-YYMMMDDHHMM...`): parse ticker directly (exact, 0 error).
   - **NBA / NHL** (`KXNBAGAME-YYMMMDD...`, `KXNHLGAME-YYMMMDD...`): call `client.get_market(ticker)`, use `close_time − nominal_duration` (NBA 2h 30m, NHL 2h 40m).
3. Compute `minutes_to_start` and `UPDATE` the row.
4. Cache Kalshi results per unique `market_id` (≈154 unique markets for 445 trades).
5. On parse failure or Kalshi 404: leave NULL, log warning, continue.

**Re-run safety**: `IS NULL` filter skips already-backfilled rows.
**Expected cost**: ~15s for ~154 Kalshi GETs (read-only, no quota concern).

### 4. Dashboard API

`monitoring/dashboard/api.py` — `/api/performance` response, sports strategies only:

```python
strategies[strat]["paper"] = {
    # existing totals, UNCHANGED
    "trade_count": ..., "total_pnl": ..., "total_size": ...,
    "wins": ..., "losses": ..., "clv_positive_rate": ...,
    # NEW, sports-only
    "pre":  {"trade_count": N, "total_pnl": X, "wins": W, "losses": L},
    "live": {"trade_count": N, "total_pnl": X, "wins": W, "losses": L},
}
```

Classification:
- `minutes_to_start > 0` → `pre`.
- `minutes_to_start <= 0` → `live`.
- `minutes_to_start IS NULL` → neither bucket (still in the existing top-level totals).

Non-sports strategies: no `pre` / `live` keys (existing shape unchanged).

### 5. Dashboard UI

`monitoring/dashboard/static/index.html` — sports cards render two extra rows beneath the existing paper totals when `paper.pre` / `paper.live` keys are present:

```
[sports_longshot]  paper
  trades: 67   pnl: -$29.72   W: 13   L: 42   CLV: 45%
  ├─ pre  : n=22  pnl +$14.00  W 33.3%
  └─ live : n=37  pnl -$46.75  W 10.8%
```

- Reuses existing formatters (`fmtMoney`, W% helper).
- Existing pnl color rules (green/red) apply.
- Non-sports cards: unchanged (no `pre` / `live` keys → skip block).

### 6. Testing

**Unit**:
- `tests/test_sports_entry_minutes.py` (new): `_compute_minutes_to_start` — valid future commence_time → positive; past → negative; malformed / missing → `None`.
- `tests/test_database.py` (extend): `record_trade(..., minutes_to_start=X)` persists X; omission stores NULL.

**Integration**:
- `tests/test_dashboard_api.py` (extend): seed paper_filled trades with mixed `minutes_to_start`, call `/api/performance`, assert `paper.pre` / `paper.live` buckets split correctly and that non-sports strategies have no such keys.

**Backfill**: no automated test (external Kalshi dependency). Manual smoke on dev DB copy before prod run.

## Design decisions

### Cutoff = 0 min (scheduled commence_time)

Considered:
- `0` (scheduled start): simplest, matches "pre-match signal validity" heuristic.
- `−1` buffer: absorb clock skew.
- `+5` (conservative): pre-match books typically close ~5 min before scheduled start, so signal degrades then.

Chose `0` because:
- Simplest defensible default.
- Numeric storage lets us re-slice retroactively without schema change.

### Source of commence_time

- **New trades**: Odds API `commence_time` (already in `_fetch_odds` response, no extra cost).
- **Backfill**: Kalshi `close_time − nominal_duration` (MLB parsed directly from ticker, exact).

Rejected: Odds API historical endpoint (paid plan $45+/month, only ~5-20 trades would change classification vs Kalshi-derived). Expected ±10-30 min error on NBA/NHL backfill doesn't affect aggregate pre/live pattern.

### Numeric storage over tag string

`minutes_to_start` (float) over `entry_phase` (text). Same storage cost, strictly more information. Enables the 4-bucket edge-cliff analysis in Future Work without another schema migration.

## Future work (tracked here so it isn't forgotten)

1. **4-bucket edge cliff analysis**: once each strategy has ~50+ settled trades, split `minutes_to_start` into `[>+5, +5~0, 0~−5, <−5]` and compute W% / PnL per bucket. Empirically locate where edge collapses. If cliff is not at 0 (e.g. signal already degraded by +5 min), adjust pre/live cutoff accordingly.
2. **Live-entry gating**: after edge-cliff analysis, add a `commence_time`-based pre-game filter in the shared sports base class. Consider per-strategy opt-out (e.g. `sports_trader` had live W% 58.3% — may not need gating).
3. **Telegram alerts**: notify when a strategy's live PnL crosses a drawdown threshold.

## Rollout order

1. DB column + `record_trade` signature.
2. `_compute_minutes_to_start` helper + integration in all sports subclasses.
3. Backfill script: local dev run → prod one-shot.
4. Dashboard API + UI.
5. Deploy verification (5-step per `feedback_verification_process.md`):
   - log grep for new tagging code path firing
   - DB: `SELECT strategy, COUNT(*) FILTER (WHERE minutes_to_start IS NOT NULL) FROM trades WHERE strategy LIKE 'sports_%' GROUP BY strategy`
   - settle: next settlement batch tags correctly
   - dashboard visual
   - telegram: N/A (no new alerts)
