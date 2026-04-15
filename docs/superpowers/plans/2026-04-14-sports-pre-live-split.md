# Sports Pre/Live Entry Tagging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every sports trade with `minutes_to_start` (minutes from trade time to scheduled game start) and expose pre-game vs live P&L splits per strategy on the dashboard, without yet gating live entries.

**Architecture:** Add a nullable `REAL` column to `trades`. Sports strategies compute the value at trade time from Odds API `commence_time` (already in `_fetch_odds` response) and pass it through `order_manager` to `record_trade`. A one-shot backfill script populates existing rows using either direct MLB ticker parsing or Kalshi `close_time − nominal_duration` for NBA/NHL. Dashboard API groups paper trades into `pre` (`minutes_to_start > 0`) and `live` (`<= 0`) sub-buckets on sports strategies only. UI renders two additional rows under existing paper totals.

**Tech Stack:** Python 3, aiosqlite, FastAPI, vanilla JS, sqlite3.

**Spec:** [docs/superpowers/specs/2026-04-14-sports-pre-live-split-design.md](../specs/2026-04-14-sports-pre-live-split-design.md)

---

## File map

| File | Responsibility | Create/Modify |
|---|---|---|
| `core/database.py` | `trades.minutes_to_start` column, idempotent migration, `record_trade` signature | Modify |
| `core/order_manager.py` | Plumb `minutes_to_start` through public + private order methods | Modify |
| `strategies/sports_trader.py` | `_commence_time_to_minutes` helper + pass-through at trade call site | Modify |
| `scripts/backfill_entry_minutes.py` | One-shot backfill for existing sports trades | Create |
| `monitoring/dashboard/api.py` | Split sports paper bucket into `pre` / `live` | Modify |
| `monitoring/dashboard/static/index.html` | Render `pre` / `live` rows on sports cards | Modify |
| `tests/test_database.py` | Extend for new column + signature | Modify |
| `tests/test_sports_entry_minutes.py` | Unit tests for `_commence_time_to_minutes` | Create |
| `tests/test_dashboard_api.py` | Extend for `pre` / `live` buckets | Modify |

---

## Task 1: DB column + record_trade signature

**Files:**
- Modify: `core/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1.1: Write failing test for new column + signature**

Append to `tests/test_database.py`:

```python
@pytest.mark.asyncio
async def test_record_trade_persists_minutes_to_start(db):
    await db.record_trade(
        strategy="sports_trader", market_id="KXMLBGAME-26APR102140TEX-TEX",
        direction="yes", price=0.50, size=2.0, fee=0.07, status="paper_filled",
        minutes_to_start=37.5,
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["minutes_to_start"] == 37.5


@pytest.mark.asyncio
async def test_record_trade_defaults_minutes_to_start_null(db):
    await db.record_trade(
        strategy="market_maker", market_id="KXMKT-X", direction="yes",
        price=0.50, size=10.0, fee=0.0, status="paper_filled",
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["minutes_to_start"] is None


@pytest.mark.asyncio
async def test_initialize_adds_minutes_to_start_to_legacy_db(tmp_path):
    # Simulate a pre-migration DB: create trades table WITHOUT the column
    import aiosqlite
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, strategy TEXT NOT NULL,
                market_id TEXT NOT NULL, direction TEXT NOT NULL,
                price REAL NOT NULL, size REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0, pnl REAL DEFAULT 0,
                status TEXT NOT NULL
            )
        """)
        await conn.commit()
    database = Database(db_path)
    await database.initialize()
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(trades)")
        cols = {row[1] for row in await cursor.fetchall()}
    assert "minutes_to_start" in cols
```

- [ ] **Step 1.2: Run tests, expect failures**

```bash
pytest tests/test_database.py -k minutes_to_start -v
```

Expected: 3 failures (column doesn't exist, record_trade doesn't accept kwarg).

- [ ] **Step 1.3: Update `trades` CREATE TABLE + add idempotent migration**

In `core/database.py`, replace the existing `CREATE TABLE IF NOT EXISTS trades (...)` block and add a migration helper. The `initialize()` body becomes:

```python
async def initialize(self):
    async with aiosqlite.connect(self.db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0,
                pnl REAL DEFAULT 0,
                status TEXT NOT NULL,
                minutes_to_start REAL
            )
        """)
        await self._add_column_if_missing(conn, "trades", "minutes_to_start", "REAL")
        # ... (rest of initialize unchanged: orders, clv_snapshots, bot_state, indexes) ...
        await conn.commit()

@staticmethod
async def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cursor.fetchall()}
    if column not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
```

- [ ] **Step 1.4: Update `record_trade` signature + INSERT**

Replace `record_trade` with:

```python
async def record_trade(self, strategy, market_id, direction, price, size, fee, status,
                      pnl=0.0, minutes_to_start: Optional[float] = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(self.db_path) as conn:
        cursor = await conn.execute(
            """INSERT INTO trades
               (timestamp, strategy, market_id, direction, price, size, fee, pnl, status, minutes_to_start)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, strategy, market_id, direction, price, size, fee, pnl, status, minutes_to_start),
        )
        await conn.commit()
        return cursor.lastrowid
```

- [ ] **Step 1.5: Run tests, expect pass**

```bash
pytest tests/test_database.py -v
```

Expected: all pass, including existing ones.

- [ ] **Step 1.6: Commit**

```bash
git add core/database.py tests/test_database.py
git commit -m "feat(db): add minutes_to_start column on trades"
```

---

## Task 2: `_commence_time_to_minutes` helper

**Files:**
- Modify: `strategies/sports_trader.py`
- Test: `tests/test_sports_entry_minutes.py` (create)

- [ ] **Step 2.1: Write failing tests**

Create `tests/test_sports_entry_minutes.py`:

```python
from datetime import datetime, timedelta, timezone
from strategies.sports_trader import _commence_time_to_minutes


def test_future_commence_time_returns_positive(monkeypatch):
    fixed_now = datetime(2026, 4, 14, 18, 0, 0, tzinfo=timezone.utc)
    _patch_now(monkeypatch, fixed_now)
    # commence_time 30 min in the future
    ct = (fixed_now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _commence_time_to_minutes(ct) == pytest.approx(30.0, abs=0.01)


def test_past_commence_time_returns_negative(monkeypatch):
    fixed_now = datetime(2026, 4, 14, 18, 0, 0, tzinfo=timezone.utc)
    _patch_now(monkeypatch, fixed_now)
    ct = (fixed_now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _commence_time_to_minutes(ct) == pytest.approx(-15.0, abs=0.01)


def test_missing_commence_time_returns_none():
    assert _commence_time_to_minutes(None) is None
    assert _commence_time_to_minutes("") is None


def test_malformed_commence_time_returns_none():
    assert _commence_time_to_minutes("not-a-date") is None
    assert _commence_time_to_minutes("2026-13-99T99:99:99Z") is None


def _patch_now(monkeypatch, fixed):
    import strategies.sports_trader as mod
    monkeypatch.setattr(mod, "_now_utc", lambda: fixed)


import pytest
```

- [ ] **Step 2.2: Run tests, expect failures**

```bash
pytest tests/test_sports_entry_minutes.py -v
```

Expected: ImportError (`_commence_time_to_minutes` doesn't exist yet) or `_now_utc` missing.

- [ ] **Step 2.3: Implement helper + `_now_utc` indirection**

In `strategies/sports_trader.py`, at module level (near other module helpers, above `class SportsTraderStrategy`):

```python
def _now_utc() -> datetime:
    """Indirection so tests can patch the clock."""
    return datetime.now(timezone.utc)


def _commence_time_to_minutes(commence_time: Optional[str]) -> Optional[float]:
    """Convert Odds API ISO-8601 commence_time to minutes-until-start from now.
    Positive = pre-game, zero/negative = live. Returns None if unparseable."""
    if not commence_time:
        return None
    try:
        # Odds API format: "2026-04-14T23:10:00Z"
        iso = commence_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return (dt - _now_utc()).total_seconds() / 60.0
```

Ensure `Optional` is imported from `typing` (already is; verify).

- [ ] **Step 2.4: Run tests, expect pass**

```bash
pytest tests/test_sports_entry_minutes.py -v
```

Expected: 4 passing.

- [ ] **Step 2.5: Commit**

```bash
git add strategies/sports_trader.py tests/test_sports_entry_minutes.py
git commit -m "feat(sports): add _commence_time_to_minutes helper"
```

---

## Task 3: Plumb `minutes_to_start` through order_manager

**Files:**
- Modify: `core/order_manager.py`

No new tests — behavior change is purely pass-through, covered by Task 4's integration wiring + existing order_manager tests.

- [ ] **Step 3.1: Extend public methods**

In `core/order_manager.py`, change signatures of `market_order` and `limit_order` to accept `minutes_to_start: Optional[float] = None` and pass it to the private methods:

```python
async def market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                      minutes_to_start: Optional[float] = None):
    if self.bankroll < size_usdc:
        logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
        return {"status": "skipped", "reason": "insufficient_bankroll"}
    if self._is_live(strategy):
        logger.info(f"[LIVE] {strategy} {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
        return await self._live_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                             minutes_to_start=minutes_to_start)
    return await self._paper_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                          minutes_to_start=minutes_to_start)

async def limit_order(self, ticker, side, price, size_usdc, strategy, market_id,
                     minutes_to_start: Optional[float] = None):
    # ... existing live path (unchanged except _live_limit_order call)
    # ... existing paper path (unchanged except _paper_limit_order call)
```

Inside each branch, replace the call to `_live_limit_order(...)` / `_paper_limit_order(...)` to pass `minutes_to_start=minutes_to_start`.

- [ ] **Step 3.2: Extend private methods**

For each of `_paper_market_order`, `_live_market_order`, `_paper_limit_order`, `_live_limit_order`:
- Add `minutes_to_start: Optional[float] = None` kwarg.
- Pass it to `self._db.record_trade(...)` call inside that method.

Example for `_paper_market_order`:

```python
async def _paper_market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                              minutes_to_start: Optional[float] = None):
    # ... existing body unchanged until record_trade ...
    trade_id = await self._db.record_trade(
        strategy=strategy, market_id=market_id, direction=side,
        price=price, size=size_usdc, fee=fee, status="paper_filled",
        minutes_to_start=minutes_to_start,
    )
    # ... rest unchanged ...
```

Apply the same pattern to the other three internal order methods. `_paper_limit_order` does NOT call `record_trade` directly (it writes to `orders`); its fill-side `record_trade` is in the limit-fill path. **For now only extend methods that directly call `record_trade`** — confirm which via:

```bash
grep -n "record_trade" core/order_manager.py
```

For each match, add `minutes_to_start=minutes_to_start` to the kwargs.

Also ensure `Optional` is imported at the top of `core/order_manager.py` (`from typing import Optional`) — add if missing.

- [ ] **Step 3.3: Run existing tests to confirm no regression**

```bash
pytest tests/test_order_manager.py -v
```

Expected: all existing pass (no behavior change for callers not passing the kwarg).

- [ ] **Step 3.4: Commit**

```bash
git add core/order_manager.py
git commit -m "feat(orders): plumb minutes_to_start through order_manager"
```

---

## Task 4: Call site — sports_trader passes `minutes_to_start`

**Files:**
- Modify: `strategies/sports_trader.py`

- [ ] **Step 4.1: Locate trade call site**

Currently at `strategies/sports_trader.py` ~line 575:

```python
order_result = await self._order_manager.market_order(
    ticker=ticker, side=signal["side"], size_usdc=size,
    price=limit_price, strategy=self.STRATEGY_NAME, market_id=ticker,
)
```

`matched_game` (from `self._match_kalshi_to_odds(title, odds_games)`) is in scope and has `commence_time`.

- [ ] **Step 4.2: Compute + pass `minutes_to_start`**

Replace the call site with:

```python
minutes_to_start = _commence_time_to_minutes(matched_game.get("commence_time"))
order_result = await self._order_manager.market_order(
    ticker=ticker, side=signal["side"], size_usdc=size,
    price=limit_price, strategy=self.STRATEGY_NAME, market_id=ticker,
    minutes_to_start=minutes_to_start,
)
```

No import change needed (helper defined in same module).

- [ ] **Step 4.3: Smoke-check other sports subclasses don't shadow the trade call**

```bash
grep -n "market_order\|limit_order" strategies/sports_*.py
```

Expected: only `strategies/sports_trader.py` contains the call. If any subclass overrides the trade logic, extend the same pattern there.

- [ ] **Step 4.4: Run all sports tests**

```bash
pytest tests/test_sports_*.py -v
```

Expected: existing tests still pass (no behavior change for tests that don't care about `minutes_to_start`).

- [ ] **Step 4.5: Commit**

```bash
git add strategies/sports_trader.py
git commit -m "feat(sports): tag trades with minutes_to_start at entry"
```

---

## Task 5: Backfill script

**Files:**
- Create: `scripts/backfill_entry_minutes.py`

No automated test (external Kalshi dependency). Manual smoke test in Step 5.3.

- [ ] **Step 5.1: Write the script**

Create `scripts/backfill_entry_minutes.py`:

```python
"""One-shot backfill: populate trades.minutes_to_start for existing sports trades.

Sources:
- MLB: ticker parse (exact, e.g. KXMLBGAME-26APR102140TEX-TEX -> 2026-04-10 21:40 ET).
- NBA/NHL: Kalshi get_market(ticker).close_time - nominal_duration.

Re-run safe: only updates rows where minutes_to_start IS NULL.
"""
from __future__ import annotations
import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from core.config import Config
from core.kalshi_client import KalshiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_entry_minutes")

MONTH = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
NOMINAL_DURATION = {
    "KXNBA": timedelta(hours=2, minutes=30),
    "KXNHL": timedelta(hours=2, minutes=40),
}


def parse_mlb_start_utc(ticker: str) -> datetime | None:
    m = re.search(r"KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})", ticker or "")
    if not m:
        return None
    yy, mon, dd, hh, mm = m.groups()
    try:
        # MLB ticker time is America/New_York (EDT/EST). Use EDT offset for
        # April-November. Acceptable ±1h error doesn't affect pre/live classification.
        return datetime(2000+int(yy), MONTH[mon], int(dd), int(hh), int(mm),
                       tzinfo=timezone.utc) + timedelta(hours=4)
    except (ValueError, KeyError):
        return None


def resolve_start_utc(ticker: str, client: KalshiClient) -> datetime | None:
    s = parse_mlb_start_utc(ticker)
    if s is not None:
        return s
    prefix = ticker[:5]  # "KXNBA" / "KXNHL"
    dur = NOMINAL_DURATION.get(prefix)
    if dur is None:
        return None
    try:
        md = client.get_market(ticker)
        if not md:
            return None
        ct = md.get("close_time")
        if not ct:
            return None
        close = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        return close - dur
    except Exception as e:
        logger.warning(f"get_market failed for {ticker}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = Config()
    client = KalshiClient(config)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, timestamp, market_id FROM trades
           WHERE strategy LIKE 'sports_%' AND minutes_to_start IS NULL"""
    ).fetchall()
    logger.info(f"candidates: {len(rows)}")

    start_cache: dict[str, datetime | None] = {}
    updated = 0
    skipped = 0
    for r in rows:
        mkt = r["market_id"]
        if mkt not in start_cache:
            start_cache[mkt] = resolve_start_utc(mkt, client)
        start = start_cache[mkt]
        if start is None:
            skipped += 1
            continue
        trade_ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=timezone.utc)
        minutes = (start - trade_ts).total_seconds() / 60.0
        if args.dry_run:
            logger.info(f"[DRY] trade {r['id']} {mkt} -> {minutes:+.1f} min")
        else:
            conn.execute(
                "UPDATE trades SET minutes_to_start=? WHERE id=?",
                (minutes, r["id"]),
            )
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    logger.info(f"updated: {updated}  skipped: {skipped}  unique_markets: {len(start_cache)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5.2: Dry-run locally**

```bash
python3 scripts/backfill_entry_minutes.py --db data/bot.db --dry-run 2>&1 | head -20
```

Expected: "candidates: 0" locally (local DB is unused per earlier inspection), OR non-zero count with per-trade log lines. No errors.

- [ ] **Step 5.3: Commit**

```bash
git add scripts/backfill_entry_minutes.py
git commit -m "feat(scripts): backfill minutes_to_start for existing sports trades"
```

---

## Task 6: Dashboard API — split paper into pre/live

**Files:**
- Modify: `monitoring/dashboard/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 6.1: Write failing test**

Append to `tests/test_dashboard_api.py`:

```python
@pytest.mark.asyncio
async def test_performance_splits_sports_paper_pre_live(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    await database.initialize()
    # Pre-game winner
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-x", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=4.0,
        minutes_to_start=45.0,
    )
    # Pre-game loser
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-y", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
        minutes_to_start=120.0,
    )
    # Live loser
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-z", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
        minutes_to_start=-30.0,
    )
    # Untagged (still counts in top-level total only)
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-w", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
    )
    # Non-sports strategy should NOT get pre/live keys
    await database.record_trade(
        strategy="market_maker", market_id="m1", direction="yes",
        price=0.5, size=5.0, fee=0.07, status="paper_filled", pnl=0.0,
    )
    app = create_app(database, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    longshot_paper = data["strategies"]["sports_longshot"]["paper"]
    assert longshot_paper["trade_count"] == 4          # total unchanged
    assert longshot_paper["pre"]["trade_count"] == 2
    assert longshot_paper["pre"]["total_pnl"] == pytest.approx(2.0)
    assert longshot_paper["pre"]["wins"] == 1
    assert longshot_paper["pre"]["losses"] == 1
    assert longshot_paper["live"]["trade_count"] == 1
    assert longshot_paper["live"]["total_pnl"] == pytest.approx(-2.0)
    assert longshot_paper["live"]["wins"] == 0
    assert longshot_paper["live"]["losses"] == 1
    # Non-sports: no pre/live keys
    mm_paper = data["strategies"]["market_maker"]["paper"]
    assert "pre" not in mm_paper
    assert "live" not in mm_paper
```

- [ ] **Step 6.2: Run test, expect failure**

```bash
pytest tests/test_dashboard_api.py::test_performance_splits_sports_paper_pre_live -v
```

Expected: `KeyError: 'pre'` or similar.

- [ ] **Step 6.3: Update `empty_stats` and paper loop in dashboard API**

In `monitoring/dashboard/api.py`, modify the `/api/performance` handler. Change `empty_stats()` to take a flag for sports:

```python
def empty_stats():
    return {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0,
            "wins": 0, "losses": 0, "clv_positive_rate": 0.0}

def empty_split():
    return {"trade_count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

SPORTS_PREFIX = "sports_"
```

Inside the strategies dict construction (existing line ~77):

```python
strategies = {}
for s in all_strategy_names:
    paper = empty_stats()
    if s.startswith(SPORTS_PREFIX):
        paper["pre"] = empty_split()
        paper["live"] = empty_split()
    strategies[s] = {"live": empty_stats(), "paper": paper}
```

In the paper-bucket aggregation loop (existing line ~80), after the existing increments, add pre/live split for sports rows:

```python
for trade in all_trades:
    status = trade.get("status", "")
    if not status.startswith("paper"):
        continue
    strat = trade["strategy"]
    if strat not in strategies:
        paper = empty_stats()
        if strat.startswith(SPORTS_PREFIX):
            paper["pre"] = empty_split()
            paper["live"] = empty_split()
        strategies[strat] = {"live": empty_stats(), "paper": paper}
    bucket = strategies[strat]["paper"]
    pnl = trade.get("pnl", 0) or 0
    size = trade.get("size", 0) or 0
    bucket["trade_count"] += 1
    bucket["total_pnl"] += pnl
    bucket["total_size"] += size
    if pnl > 0:
        bucket["wins"] += 1
    elif pnl < 0:
        bucket["losses"] += 1
    # Sports pre/live split
    if strat.startswith(SPORTS_PREFIX):
        mts = trade.get("minutes_to_start")
        if mts is not None:
            sub = bucket["pre"] if mts > 0 else bucket["live"]
            sub["trade_count"] += 1
            sub["total_pnl"] += pnl
            if pnl > 0:
                sub["wins"] += 1
            elif pnl < 0:
                sub["losses"] += 1
```

Note: delete the old inline `bucket["trade_count"] += 1`, `bucket["total_pnl"] += pnl`, `bucket["total_size"] += size`, and wins/losses increments — the block above subsumes them. Verify by grep after.

- [ ] **Step 6.4: Verify `get_trades` returns `minutes_to_start`**

Open `core/database.py`, find `get_trades`, confirm `SELECT * FROM trades` returns the new column via `aiosqlite.Row`. No change expected since it's `SELECT *`, but verify by running:

```bash
pytest tests/test_database.py::test_record_trade_persists_minutes_to_start -v
```

Should still pass.

- [ ] **Step 6.5: Run dashboard test**

```bash
pytest tests/test_dashboard_api.py -v
```

Expected: all pass including new `test_performance_splits_sports_paper_pre_live`.

- [ ] **Step 6.6: Commit**

```bash
git add monitoring/dashboard/api.py tests/test_dashboard_api.py
git commit -m "feat(dashboard): split sports paper bucket into pre/live"
```

---

## Task 7: Dashboard UI — render pre/live rows

**Files:**
- Modify: `monitoring/dashboard/static/index.html`

No automated test (vanilla JS in static HTML; manual smoke in Step 7.3).

- [ ] **Step 7.1: Add pre/live rows to card template**

In `monitoring/dashboard/static/index.html`, find the performance-card template string (around line 817-849). After the existing `Volume` row (`<div class="perf-row"> ... Volume ... </div>`), insert a conditional block:

```javascript
        <div class="perf-row">
          <span class="perf-key">Volume</span>
          <span class="perf-val">${fmtMoney(d.total_size)}</span>
        </div>
        ${d.pre ? `
        <div class="perf-row" style="border-top:1px solid var(--border-dim);padding-top:6px;margin-top:4px;">
          <span class="perf-key">Pre-game</span>
          <span class="perf-val">
            n=${d.pre.trade_count}
            <span class="${d.pre.total_pnl >= 0 ? 'positive' : 'negative'}">${fmtMoney(d.pre.total_pnl)}</span>
            ${d.pre.wins + d.pre.losses > 0 ? `(${((d.pre.wins / (d.pre.wins + d.pre.losses)) * 100).toFixed(0)}%)` : ''}
          </span>
        </div>
        <div class="perf-row">
          <span class="perf-key">Live</span>
          <span class="perf-val">
            n=${d.live.trade_count}
            <span class="${d.live.total_pnl >= 0 ? 'positive' : 'negative'}">${fmtMoney(d.live.total_pnl)}</span>
            ${d.live.wins + d.live.losses > 0 ? `(${((d.live.wins / (d.live.wins + d.live.losses)) * 100).toFixed(0)}%)` : ''}
          </span>
        </div>
        ` : ''}
```

The `${d.pre ? ... : ''}` guard means non-sports cards (where `d.pre` is undefined) render nothing new.

- [ ] **Step 7.2: Static CSS check**

`--border-dim` is used elsewhere in the file; verify:

```bash
grep -c "border-dim" monitoring/dashboard/static/index.html
```

Expected: ≥ 1. If 0, replace with `var(--text-dim)` or plain `#333`.

- [ ] **Step 7.3: Local smoke test**

Run dashboard locally:

```bash
python3 -m uvicorn monitoring.dashboard.api:app --reload --port 8000
```

(If that module path differs, use the project's standard dashboard entry point — `grep -rn "uvicorn" scripts/ main.py` should surface it.) Open `http://localhost:8000`, confirm:
- Sports cards show Pre-game + Live rows.
- Non-sports cards (market_maker, etc.) are unchanged.
- Browser console has no JS errors.

- [ ] **Step 7.4: Commit**

```bash
git add monitoring/dashboard/static/index.html
git commit -m "feat(dashboard): render pre/live rows on sports cards"
```

---

## Task 8: Deploy + verify

**Files:** none modified — deploy + verification only.

- [ ] **Step 8.1: Deploy to EC2**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "cd kalshi-arb && git pull && sudo systemctl restart kalshi-bot"
```

Expected: service restarts cleanly. Migration runs inside `initialize()` → ALTER TABLE adds column.

- [ ] **Step 8.2: Confirm column added in prod**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "cd ~/kalshi-arb && ./venv/bin/python3 -c \"
import sqlite3
c=sqlite3.connect('data/bot.db')
for r in c.execute('PRAGMA table_info(trades)'):
    if r[1]=='minutes_to_start': print('OK:', r)
\""
```

Expected: `OK: (10, 'minutes_to_start', 'REAL', 0, None, 0)`.

- [ ] **Step 8.3: Run backfill on prod**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "cd ~/kalshi-arb && ./venv/bin/python3 scripts/backfill_entry_minutes.py --db data/bot.db --dry-run 2>&1 | tail -20"
```

Expected: `candidates: ~445` and sample dry-run lines. Then for real:

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "cd ~/kalshi-arb && ./venv/bin/python3 scripts/backfill_entry_minutes.py --db data/bot.db 2>&1 | tail -5"
```

Expected: `updated: ~445  skipped: ~0  unique_markets: ~154`.

- [ ] **Step 8.4: Verify coverage per strategy**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "cd ~/kalshi-arb && ./venv/bin/python3 -c \"
import sqlite3
c=sqlite3.connect('data/bot.db')
for r in c.execute('''SELECT strategy, COUNT(*) total,
    SUM(CASE WHEN minutes_to_start IS NULL THEN 1 ELSE 0 END) null_cnt,
    SUM(CASE WHEN minutes_to_start > 0 THEN 1 ELSE 0 END) pre_cnt,
    SUM(CASE WHEN minutes_to_start <= 0 THEN 1 ELSE 0 END) live_cnt
  FROM trades WHERE strategy LIKE \\\"sports_%\\\"
  GROUP BY strategy ORDER BY total DESC'''): print(r)
\""
```

Expected: `null_cnt` small (< 5% per strategy), `pre_cnt + live_cnt ≈ total - null_cnt`.

- [ ] **Step 8.5: Verify dashboard**

Open https://<dashboard-url>/ (or ssh tunnel + localhost). Confirm:
- Sports cards now show Pre-game / Live rows.
- Non-sports cards unchanged.
- `sports_longshot` paper Pre-game PnL ≈ +$14, Live ≈ -$46.75 (matches prior analysis).

- [ ] **Step 8.6: Tail service log to confirm new trades get tagged**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "sudo journalctl -u kalshi-bot -n 100 --no-pager | grep -i 'paper\|sports' | tail -20"
```

After at least one new sports trade settles, re-run Step 8.4 and confirm `null_cnt` didn't grow.

- [ ] **Step 8.7: Final commit (version/tag optional)**

No code commit — deployment verification only. If you want a marker:

```bash
git tag pre-live-split-deployed
git push --tags
```

(Skip if the repo doesn't use tags.)

---

## Future work (tracked — not in this plan)

See `docs/superpowers/specs/2026-04-14-sports-pre-live-split-design.md` §Future Work:

1. 4-bucket edge-cliff analysis once each strategy has ~50+ settled trades.
2. `commence_time` pre-game gating in shared sports base class.
3. Telegram alert on live PnL drawdown.
