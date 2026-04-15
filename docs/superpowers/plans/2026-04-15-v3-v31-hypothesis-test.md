# V3 vs V31 Hypothesis Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument sports strategies to capture parallel us/eu consensus + Pinnacle freshness at every entry, add closing Pinnacle snapshot, resume freshness logging, and produce a retrospective analysis report — all to enable a data-driven decision in 2 weeks between hypotheses A (Pinnacle signal quality) and C (stale-filter artifact).

**Architecture:** Additive schema migration (5 new NULL columns on `clv_snapshots`). A new `PinnacleAgeTracker` module maintains `(region, sport, pinnacle_hash) → first_seen_ts` map used to compute `pinnacle_age_s`. Entry path calls `_fetch_odds` for both regions (cached ≈ free) and records both consensus values. Closing settle hook adds a final Pinnacle fetch. A 5-minute freshness task re-enables ongoing `odds_freshness_log.jsonl` sampling. A retrospective analysis script generates `docs/research/v3_v31_overlap_<YYYY-MM-DD>.md`.

**Tech Stack:** Python 3, aiosqlite, httpx, pytest/pytest-asyncio, SQLite.

**Spec:** [docs/superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md](../specs/2026-04-15-v3-v31-hypothesis-test-design.md)

---

## File Structure

**Create:**
- `core/pinnacle_age.py` — `PinnacleAgeTracker` class (hash → first_seen_ts)
- `tests/test_pinnacle_age.py` — unit tests for age tracker
- `scripts/research/__init__.py` — package marker
- `scripts/research/v3_v31_overlap_analysis.py` — retrospective analysis

**Modify:**
- `core/database.py` — add 5 columns to `clv_snapshots`, extend `record_clv_snapshot` signature
- `strategies/sports_trader.py` — dual-region fetch + tracker integration + pass new fields through snapshot write
- `main.py` — 5-min freshness periodic task
- `tests/test_database.py` — migration assertion for new columns
- `tests/test_clv_snapshots.py` or `tests/test_clv_tracker.py` — signature change

---

## Task 1 — Schema migration: add 5 columns to `clv_snapshots`

**Files:**
- Modify: `core/database.py` around line 53 (after `CREATE TABLE IF NOT EXISTS clv_snapshots` block, within `initialize`)
- Test: `tests/test_database.py`

- [ ] **Step 1.1 — Write the failing test**

Append to `tests/test_database.py`:

```python
import pytest
import aiosqlite
from core.database import Database


@pytest.mark.asyncio
async def test_clv_snapshots_has_new_instrumentation_columns(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(clv_snapshots)")
        cols = {r[1] for r in await cur.fetchall()}
    expected = {"us_consensus_prob", "eu_consensus_prob",
                "pinnacle_age_s", "kalshi_yes_bid", "kalshi_yes_ask"}
    assert expected.issubset(cols), f"missing: {expected - cols}"
```

- [ ] **Step 1.2 — Run test to verify it fails**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
source venv/bin/activate
pytest tests/test_database.py::test_clv_snapshots_has_new_instrumentation_columns -v
```

Expected: FAIL with `missing: {'us_consensus_prob', ...}`.

- [ ] **Step 1.3 — Add migration in `core/database.py`**

In `Database.initialize()`, right after `CREATE INDEX IF NOT EXISTS idx_clv_trade ...` on line 63 (before `await conn.commit()` on line 64), add:

```python
            await self._add_column_if_missing(conn, "clv_snapshots", "us_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "eu_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "pinnacle_age_s", "INTEGER")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_bid", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_ask", "REAL")
```

- [ ] **Step 1.4 — Run test to verify it passes**

```bash
pytest tests/test_database.py::test_clv_snapshots_has_new_instrumentation_columns -v
```

Expected: PASS.

- [ ] **Step 1.5 — Commit**

```bash
git add core/database.py tests/test_database.py
git commit -m "feat(db): add instrumentation columns on clv_snapshots

Adds us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
kalshi_yes_bid, kalshi_yes_ask. All NULL-allowed, additive
migration for V3 vs V31 hypothesis test.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — Extend `record_clv_snapshot` signature

**Files:**
- Modify: `core/database.py:149-160`
- Test: `tests/test_clv_snapshots.py` (existing)

- [ ] **Step 2.1 — Add test for new fields round-trip**

Append to `tests/test_clv_snapshots.py`:

```python
@pytest.mark.asyncio
async def test_record_clv_snapshot_writes_new_instrumentation_fields(tmp_path):
    from core.database import Database
    import aiosqlite
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    trade_id = await db.record_trade(
        "sports_longshot_v31", "KX-GAME-TEST", "yes", 0.45, 10.0, 0.0, "paper_filled"
    )
    await db.record_clv_snapshot(
        trade_id=trade_id, snapshot_at="entry",
        yes_ask=0.46, no_ask=0.54, pinnacle_prob=0.40,
        us_consensus_prob=0.43, eu_consensus_prob=0.40,
        pinnacle_age_s=42, kalshi_yes_bid=0.45, kalshi_yes_ask=0.46,
    )
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM clv_snapshots WHERE trade_id=?", (trade_id,)
        )
        row = await cur.fetchone()
    assert row["us_consensus_prob"] == 0.43
    assert row["eu_consensus_prob"] == 0.40
    assert row["pinnacle_age_s"] == 42
    assert row["kalshi_yes_bid"] == 0.45
    assert row["kalshi_yes_ask"] == 0.46
```

- [ ] **Step 2.2 — Run test to verify it fails**

```bash
pytest tests/test_clv_snapshots.py::test_record_clv_snapshot_writes_new_instrumentation_fields -v
```

Expected: FAIL with `TypeError: record_clv_snapshot() got an unexpected keyword argument 'us_consensus_prob'`.

- [ ] **Step 2.3 — Extend the method signature and INSERT**

Replace `core/database.py:149-160` with:

```python
    async def record_clv_snapshot(
        self,
        trade_id: int,
        snapshot_at: str,
        yes_ask: Optional[float],
        no_ask: Optional[float],
        pinnacle_prob: Optional[float] = None,
        us_consensus_prob: Optional[float] = None,
        eu_consensus_prob: Optional[float] = None,
        pinnacle_age_s: Optional[int] = None,
        kalshi_yes_bid: Optional[float] = None,
        kalshi_yes_ask: Optional[float] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO clv_snapshots
                   (trade_id, snapshot_at, snapshot_time, yes_ask, no_ask, pinnacle_prob,
                    us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                    kalshi_yes_bid, kalshi_yes_ask)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, snapshot_at, now, yes_ask, no_ask, pinnacle_prob,
                 us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                 kalshi_yes_bid, kalshi_yes_ask),
            )
            await conn.commit()
```

- [ ] **Step 2.4 — Run test and ensure all existing clv tests still pass**

```bash
pytest tests/test_clv_snapshots.py tests/test_clv_tracker.py -v
```

Expected: all PASS (new kwargs are optional; existing callers unaffected).

- [ ] **Step 2.5 — Commit**

```bash
git add core/database.py tests/test_clv_snapshots.py
git commit -m "feat(db): extend record_clv_snapshot with instrumentation fields

Adds 5 optional kwargs (us_consensus_prob, eu_consensus_prob,
pinnacle_age_s, kalshi_yes_bid, kalshi_yes_ask). Backward compatible —
existing callers need no changes.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — `PinnacleAgeTracker` module

**Files:**
- Create: `core/pinnacle_age.py`
- Create: `tests/test_pinnacle_age.py`

- [ ] **Step 3.1 — Write the failing tests**

Create `tests/test_pinnacle_age.py`:

```python
import time
from core.pinnacle_age import PinnacleAgeTracker


def test_first_observation_age_is_zero():
    t = PinnacleAgeTracker()
    age = t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    assert age == 0


def test_repeated_hash_returns_elapsed():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    age = t.observe("eu", "basketball_nba", "hashA", now=1042.0)
    assert age == 42


def test_new_hash_resets_age():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    t.observe("eu", "basketball_nba", "hashA", now=1042.0)
    age = t.observe("eu", "basketball_nba", "hashB", now=1050.0)
    assert age == 0


def test_per_region_sport_isolation():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    age = t.observe("us", "basketball_nba", "hashA", now=1042.0)
    assert age == 0  # different region, independent tracker


def test_none_hash_returns_none():
    t = PinnacleAgeTracker()
    age = t.observe("eu", "basketball_nba", None, now=1000.0)
    assert age is None
```

- [ ] **Step 3.2 — Run tests to verify they fail**

```bash
pytest tests/test_pinnacle_age.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'core.pinnacle_age'`.

- [ ] **Step 3.3 — Create `core/pinnacle_age.py`**

```python
"""Tracks time since each (region, sport, pinnacle_hash) triple was first seen.

Used to measure Pinnacle feed freshness: when consensus is recomputed, we
record how long the same Pinnacle payload has been live. Older age = stale.
"""
import time
from typing import Optional


class PinnacleAgeTracker:
    def __init__(self):
        # key: (region, sport, hash) -> first_seen_monotonic_seconds
        self._first_seen: dict[tuple[str, str, str], float] = {}

    def observe(
        self,
        region: str,
        sport: str,
        pinnacle_hash: Optional[str],
        now: Optional[float] = None,
    ) -> Optional[int]:
        """Return integer seconds since this hash was first seen, or None
        if `pinnacle_hash` is None. Records `now` for unseen hashes."""
        if pinnacle_hash is None:
            return None
        t = now if now is not None else time.monotonic()
        key = (region, sport, pinnacle_hash)
        first = self._first_seen.get(key)
        if first is None:
            self._first_seen[key] = t
            return 0
        return int(t - first)
```

- [ ] **Step 3.4 — Run tests to verify they pass**

```bash
pytest tests/test_pinnacle_age.py -v
```

Expected: 5 PASS.

- [ ] **Step 3.5 — Commit**

```bash
git add core/pinnacle_age.py tests/test_pinnacle_age.py
git commit -m "feat(core): add PinnacleAgeTracker for feed-freshness measurement

Maintains a (region, sport, pinnacle_hash) -> first_seen map.
observe() returns elapsed seconds since the same hash was first
recorded, or 0 for new hashes, or None when hash is unknown.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — Compute Pinnacle hash in `sports_trader.py` + wire tracker

**Files:**
- Modify: `strategies/sports_trader.py`
- Test: `tests/test_pinnacle_probability.py` (existing)

Context: `_fetch_odds` at line 188 returns the raw API response. `_pinnacle_probability` at line 292 extracts Pinnacle odds per game. We need the Pinnacle markets payload hash per (region, sport) to feed `PinnacleAgeTracker`. The simplest stable hash: SHA1 of JSON-dumped list of `(game_id, pinnacle_outcomes)` tuples sorted by game_id, per region+sport response.

- [ ] **Step 4.1 — Write the failing test**

Append to `tests/test_pinnacle_probability.py`:

```python
def test_pinnacle_payload_hash_stable_when_unchanged():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [
        {"id": "g1", "bookmakers": [
            {"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8},
                                             {"name": "B", "price": 2.1}]}]}]},
        {"id": "g2", "bookmakers": [
            {"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": "X", "price": 1.5},
                                             {"name": "Y", "price": 2.6}]}]}]},
    ]
    h1 = _compute_pinnacle_payload_hash(games)
    # Same content, different game order -> same hash
    h2 = _compute_pinnacle_payload_hash(list(reversed(games)))
    assert h1 == h2

def test_pinnacle_payload_hash_changes_on_price_move():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [{"id": "g1", "bookmakers": [
        {"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8},
                                         {"name": "B", "price": 2.1}]}]}]}]
    h1 = _compute_pinnacle_payload_hash(games)
    games[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.85
    h2 = _compute_pinnacle_payload_hash(games)
    assert h1 != h2

def test_pinnacle_payload_hash_none_when_no_pinnacle():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [{"id": "g1", "bookmakers": [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": []}]}]}]
    assert _compute_pinnacle_payload_hash(games) is None
```

- [ ] **Step 4.2 — Run tests to verify they fail**

```bash
pytest tests/test_pinnacle_probability.py -v -k "pinnacle_payload_hash"
```

Expected: FAIL with `ImportError: cannot import name '_compute_pinnacle_payload_hash'`.

- [ ] **Step 4.3 — Add helper at module level in `strategies/sports_trader.py`**

Near the top of the file (after imports, before the first class), add:

```python
import hashlib
import json


def _compute_pinnacle_payload_hash(games: list[dict]) -> Optional[str]:
    """Deterministic hash of Pinnacle h2h outcomes across all games in a response.

    Returns None if no game has Pinnacle odds. Game order-independent via sort.
    """
    entries = []
    for g in games:
        gid = g.get("id") or g.get("commence_time") or ""
        for bm in g.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                outs = tuple(
                    (o.get("name"), o.get("price"))
                    for o in mkt.get("outcomes", [])
                )
                entries.append((gid, outs))
    if not entries:
        return None
    entries.sort()
    payload = json.dumps(entries, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]
```

Ensure `Optional` is imported (it likely already is; if not, `from typing import Optional`).

- [ ] **Step 4.4 — Run tests to verify they pass**

```bash
pytest tests/test_pinnacle_probability.py -v -k "pinnacle_payload_hash"
```

Expected: 3 PASS.

- [ ] **Step 4.5 — Instantiate `PinnacleAgeTracker` as module-level singleton**

In `strategies/sports_trader.py`, after the existing module-level `_ODDS_CACHE` definition (grep for `_ODDS_CACHE = {}` or similar), add:

```python
from core.pinnacle_age import PinnacleAgeTracker
_PINNACLE_AGE = PinnacleAgeTracker()
```

- [ ] **Step 4.6 — Extend `_fetch_odds` to observe hash**

At the very end of the successful-fetch branch in `_fetch_odds` (after `_ODDS_CACHE[cache_key] = ...` around line 221), add:

```python
                pinnacle_hash = _compute_pinnacle_payload_hash(data)
                _PINNACLE_AGE.observe(region, sport, pinnacle_hash)
```

Do the same for the cached-stale branch if there was no fresh fetch (i.e. on cache hit, still call `_PINNACLE_AGE.observe(region, sport, _compute_pinnacle_payload_hash(data))`). Add immediately before the `return data` inside the cache-hit block (around line 200):

```python
                _PINNACLE_AGE.observe(region, sport, _compute_pinnacle_payload_hash(data))
```

- [ ] **Step 4.7 — Run all tests**

```bash
pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 4.8 — Commit**

```bash
git add strategies/sports_trader.py tests/test_pinnacle_probability.py
git commit -m "feat(sports): compute Pinnacle payload hash + feed freshness tracker

Adds _compute_pinnacle_payload_hash helper (SHA1 of sorted h2h
outcomes) and wires module-level PinnacleAgeTracker into _fetch_odds
on both fresh-fetch and cache-hit paths.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 — Dual-region consensus at entry + pass through snapshot

**Files:**
- Modify: `strategies/sports_trader.py` (entry path around `_record_entry_clv` call on line 279, and `scan_and_execute` around line 515-567)

Context: the entry path currently calls `_consensus_probability(game)` once with the strategy's region. We need to also compute the **opposite region** consensus, and the Pinnacle age for the eu region (which contains Pinnacle). Both us and eu `_fetch_odds` responses are typically already in `_ODDS_CACHE` because the full strategy roster calls both — so this is nearly free.

- [ ] **Step 5.1 — Read `scan_and_execute` around line 429-600 and `_record_entry_clv` around line 279**

```bash
sed -n '260,310p' /Users/taejunoh/Desktop/LFG/kalshi-arb/strategies/sports_trader.py
sed -n '429,620p' /Users/taejunoh/Desktop/LFG/kalshi-arb/strategies/sports_trader.py
```

Identify:
- exact call site of `_record_entry_clv(...)` after a market order fills
- the `game` dict in scope at entry (used for `_consensus_probability` and `_pinnacle_probability`)
- the Kalshi orderbook call — likely `self._client.get_market_prices(ticker)` or similar — find any bid/ask retrieval to know what's available

- [ ] **Step 5.2 — Write test for dual-region helper**

Append to `tests/test_region_override.py` (existing file exercises region switching):

```python
@pytest.mark.asyncio
async def test_entry_clv_receives_dual_consensus(tmp_path, monkeypatch):
    """At entry, snapshot should include us_consensus_prob and eu_consensus_prob
    populated from BOTH region fetches, plus pinnacle_age_s."""
    # This test asserts the integration contract. Implementation uses
    # a helper `await self._dual_region_consensus(sport, game)`.
    from strategies.sports_trader import SportsTraderStrategy
    # ... harness: seed _ODDS_CACHE for both us and eu with fake data,
    # invoke the helper, assert returned tuple shape:
    # (us_prob, eu_prob, pinnacle_prob, pinnacle_age_s)
```

NOTE: exact harness depends on existing test fixtures in `test_region_override.py`. Read that file first and mirror its setup pattern. If the file uses specific mock helpers, use them; otherwise build a minimal fake `config`, `order_manager`, `kalshi_client`, `scanner` with `MagicMock` as done in `tests/test_shutdown_fade.py`.

- [ ] **Step 5.3 — Run test to verify it fails**

```bash
pytest tests/test_region_override.py::test_entry_clv_receives_dual_consensus -v
```

Expected: FAIL (helper not yet implemented or import error).

- [ ] **Step 5.4 — Add `_dual_region_consensus` helper on `SportsTraderStrategy`**

In `strategies/sports_trader.py`, add method on the base class (before `_fetch_odds`):

```python
    async def _dual_region_consensus(
        self, sport: str, game: dict
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[int]]:
        """Return (us_consensus, eu_consensus, pinnacle_prob, pinnacle_age_s).

        Pulls both regions (cached when possible). Pinnacle data is pulled
        from the eu response (eu regions include Pinnacle).
        """
        current_region = self._get_odds_region()
        other_region = "eu" if current_region == "us" else "us"

        # Ensure both regions' responses are in cache; reuse _fetch_odds.
        current_data = await self._fetch_odds(sport)
        # temporarily swap region for opposite fetch
        self.REGION_OVERRIDE, saved = other_region, self.REGION_OVERRIDE
        try:
            other_data = await self._fetch_odds(sport)
        finally:
            self.REGION_OVERRIDE = saved

        us_data = current_data if current_region == "us" else other_data
        eu_data = current_data if current_region == "eu" else other_data

        us_game = next((g for g in us_data if g.get("id") == game.get("id")), None)
        eu_game = next((g for g in eu_data if g.get("id") == game.get("id")), None)

        us_prob = self._consensus_probability(us_game) if us_game else None
        eu_prob = self._consensus_probability(eu_game) if eu_game else None
        pinnacle_prob = self._pinnacle_probability(eu_game) if eu_game else None

        eu_hash = _compute_pinnacle_payload_hash(eu_data) if eu_data else None
        pinnacle_age_s = _PINNACLE_AGE.observe("eu", sport, eu_hash)

        return us_prob, eu_prob, pinnacle_prob, pinnacle_age_s
```

- [ ] **Step 5.5 — Call helper at entry and thread into snapshot write**

Find the entry path. Around the current `_record_entry_clv(trade_id, ticker, pinnacle_prob)` call (line ~279), replace with:

```python
    async def _record_entry_clv(self, trade_id: int, ticker: str, game: dict, sport: str,
                                pinnacle_prob: Optional[float]):
        """Record entry snapshot with parallel us/eu consensus + freshness."""
        yes_ask, no_ask = self._client.get_market_prices(ticker)
        bid_ask = getattr(self._client, "get_top_of_book", lambda t: (None, None))(ticker)
        kalshi_yes_bid, kalshi_yes_ask = bid_ask if bid_ask else (None, None)
        us_prob, eu_prob, _pin, age_s = await self._dual_region_consensus(sport, game)
        await self._db.record_clv_snapshot(
            trade_id=trade_id, snapshot_at="entry",
            yes_ask=yes_ask, no_ask=no_ask, pinnacle_prob=pinnacle_prob,
            us_consensus_prob=us_prob, eu_consensus_prob=eu_prob,
            pinnacle_age_s=age_s, kalshi_yes_bid=kalshi_yes_bid, kalshi_yes_ask=kalshi_yes_ask,
        )
```

Then update the call site in `scan_and_execute` to pass `game` and `sport`:

```python
await self._record_entry_clv(trade_id, ticker, game=game, sport=sport_key, pinnacle_prob=pinnacle_prob)
```

NOTE on `get_top_of_book`: if `core/kalshi_client.py` does not expose this, use a fallback — record `(None, yes_ask)` so at least the ask side is captured. Do NOT add a new Kalshi API call for bid/ask in this task; log a follow-up in the plan post-section.

- [ ] **Step 5.6 — Run tests**

```bash
pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 5.7 — Commit**

```bash
git add strategies/sports_trader.py tests/test_region_override.py
git commit -m "feat(sports): record parallel us/eu consensus + pinnacle_age_s at entry

Adds _dual_region_consensus helper and routes entry-CLV snapshot writes
through it. Opposite-region fetch hits the shared _ODDS_CACHE, so quota
impact is negligible.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 — Closing snapshot: capture Pinnacle prob

**Files:**
- Modify: `core/settlement.py` (around the `record_clv_snapshot(..., snapshot_at="closing", ...)` call)

Context: settlement currently records closing snapshot with `yes_ask`/`no_ask` from Kalshi but `pinnacle_prob=None`. Pull Odds API eu for the sport+game one final time and extract Pinnacle prob.

- [ ] **Step 6.1 — Locate the closing snapshot write**

```bash
grep -n "snapshot_at=.closing." /Users/taejunoh/Desktop/LFG/kalshi-arb/core/settlement.py
```

- [ ] **Step 6.2 — Write a test that pinnacle_prob is populated when Odds data is available**

Append to `tests/test_settlement.py`:

```python
@pytest.mark.asyncio
async def test_closing_snapshot_includes_pinnacle_prob(tmp_path, monkeypatch):
    """Settlement should record closing snapshot with pinnacle_prob
    when odds provider still returns data for the game."""
    # Use existing harness in test_settlement.py; add a mock odds
    # provider that returns a Pinnacle price for the ticker, verify
    # the row in clv_snapshots has pinnacle_prob non-NULL.
    # Mirror the structure of any nearby existing test in this file.
```

NOTE: mirror the existing `test_settlement.py` fixture pattern (likely a `mock_kalshi` + `mock_db` harness). Read first 100 lines of the file before writing the test body.

- [ ] **Step 6.3 — Run test to verify it fails**

```bash
pytest tests/test_settlement.py -k "closing_snapshot_includes_pinnacle" -v
```

Expected: FAIL.

- [ ] **Step 6.4 — Thread odds provider into settlement for closing Pinnacle pull**

`core/settlement.py` currently doesn't hold a reference to an odds provider. Two options:

A. Pass the base sports strategy into settlement — tight coupling, reject.
B. Add a `pinnacle_prob_fn: Optional[Callable[[str, str], Awaitable[Optional[float]]]]` parameter to `Settlement.__init__` and call it when writing closing snapshots.

Implement (B). `main.py` wires the lambda: `pinnacle_prob_fn=lambda ticker, sport: sports_base_strategy._pinnacle_prob_for_ticker(ticker, sport)`.

Because this requires a new helper `_pinnacle_prob_for_ticker` on `SportsTraderStrategy` that maps a Kalshi ticker back to an Odds API game, and that mapping already happens in `scan_and_execute`, extract it:

```python
    async def _pinnacle_prob_for_ticker(self, ticker: str, sport: str) -> Optional[float]:
        """Retrieve current Pinnacle implied probability for a Kalshi ticker.
        Returns None if ticker can't be mapped to an Odds API game or no
        Pinnacle data present."""
        # use existing ticker->game mapping logic; wrap in try/except
```

Exact implementation depends on the existing ticker-to-game mapping (likely via team names in the Kalshi ticker). If the mapping is not readily extractable in < 30 lines, deferring closing Pinnacle is acceptable — document as a follow-up and only keep `us_consensus_prob`/`eu_consensus_prob`/`pinnacle_age_s` in scope for this task.

- [ ] **Step 6.5 — If mapping extraction exceeds 30 lines, SKIP closing Pinnacle**

Document in plan: "Closing Pinnacle pull deferred. Decision: forward entry instrumentation is sufficient for A/C discrimination; closing Pinnacle is nice-to-have." Mark Task 6 complete, proceed to Task 7.

- [ ] **Step 6.6 — Otherwise: implement, run tests, verify pass**

```bash
pytest tests/ -q
```

- [ ] **Step 6.7 — Commit**

```bash
git add core/settlement.py strategies/sports_trader.py tests/test_settlement.py
git commit -m "feat(settle): optional pinnacle_prob capture on closing snapshot

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

OR (if deferred):

```bash
git commit --allow-empty -m "chore(spec): defer closing Pinnacle capture — entry instrumentation sufficient

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 — Periodic freshness logging (5-min task)

**Files:**
- Modify: `main.py`
- Modify: `scripts/measure_odds_freshness.py` (factor core loop into importable function)

- [ ] **Step 7.1 — Refactor `scripts/measure_odds_freshness.py` to expose a single-sample fn**

Current `collect(api_key, duration_min)` runs a loop. Extract:

```python
async def sample_once(api_key: str, tick: int = 1) -> list[dict]:
    """One pass over (region, sport) matrix. Returns list of records
    identical in shape to the jsonl rows. Caller handles appending."""
```

Keep the existing `collect` fn calling `sample_once` in a loop for CLI usage.

- [ ] **Step 7.2 — Write test**

Append to `tests/` a new test file `tests/test_freshness_sampler.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from scripts.measure_odds_freshness import sample_once


@pytest.mark.asyncio
async def test_sample_once_returns_rows_per_region_sport(monkeypatch):
    async def fake_get(*a, **k):
        class R:
            def raise_for_status(self): pass
            headers = {"x-requests-remaining": "10000"}
            def json(self): return [{"id": "g1", "bookmakers": [
                {"key": "pinnacle", "markets": [
                    {"key": "h2h", "outcomes": [{"name": "A", "price": 1.9}]}
                ]}]}]
        return R()
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        rows = await sample_once("fakekey", tick=7)
    assert len(rows) >= 1
    assert all("region" in r and "sport" in r and "ts" in r and "tick" in r for r in rows)
    assert all(r["tick"] == 7 for r in rows)
```

- [ ] **Step 7.3 — Run test, verify fails**

```bash
pytest tests/test_freshness_sampler.py -v
```

Expected: FAIL (import error).

- [ ] **Step 7.4 — Refactor + make test pass**

Extract `sample_once` from existing `collect()` body. Keep `REGIONS`, `SPORTS`, hash-computation logic intact. Verify existing jsonl format unchanged.

- [ ] **Step 7.5 — Schedule 5-min task in main.py**

Find the `_strategy_loop` / asyncio task creation section in `main.py`. Add a new periodic task:

```python
    async def _freshness_sample_loop(self):
        import json as _json
        import os
        from scripts.measure_odds_freshness import sample_once
        out_path = os.path.join(os.path.dirname(__file__), "data", "odds_freshness_log.jsonl")
        tick = 0
        while not self._shutdown:
            tick += 1
            try:
                api_key = self.config.ODDS_API_KEY
                if api_key:
                    rows = await sample_once(api_key, tick=tick)
                    with open(out_path, "a") as f:
                        for r in rows:
                            f.write(_json.dumps(r) + "\n")
            except Exception as e:
                logger.warning(f"freshness sample failed: {e}")
            await asyncio.sleep(300)
```

And register it alongside other tasks in the bot's start sequence (mirror how `_settlement_check_task` is scheduled).

- [ ] **Step 7.6 — Smoke run tests**

```bash
pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 7.7 — Commit**

```bash
git add scripts/measure_odds_freshness.py main.py tests/test_freshness_sampler.py
git commit -m "feat(main): 5-min odds freshness sampling task

Factors sample_once out of scripts/measure_odds_freshness.py and
schedules a periodic bot task to resume freshness logging that was
run only once on 2026-04-14.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 — Retrospective analysis script

**Files:**
- Create: `scripts/research/__init__.py` (empty)
- Create: `scripts/research/v3_v31_overlap_analysis.py`

- [ ] **Step 8.1 — Create empty package marker**

```bash
touch /Users/taejunoh/Desktop/LFG/kalshi-arb/scripts/research/__init__.py
```

- [ ] **Step 8.2 — Implement the analysis script**

Create `scripts/research/v3_v31_overlap_analysis.py`:

```python
"""Retrospective V3 vs V31 overlap analysis.

Generates docs/research/v3_v31_overlap_<YYYY-MM-DD>.md with:
  (a) CLV direction per bucket (overlap us, overlap eu, us-only)
  (b) V31 entry Pinnacle-Kalshi gap distribution
  (c) Pinnacle-predicts-closing convergence rate

Usage:
    python -m scripts.research.v3_v31_overlap_analysis [--db path]
"""
import argparse
import datetime as dt
import os
import sqlite3
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO / "data" / "bot.db"
OUT_DIR = REPO / "docs" / "research"

PAIRS = [
    ("sports_longshot", "sports_longshot_v31"),
    ("sports_midshot", "sports_midshot_v31"),
    ("sports_underdog", "sports_underdog_v31"),
]


def bucket_trades(conn):
    """Return dict: {pair_name: {'overlap_us':[], 'overlap_eu':[], 'us_only':[]}}"""
    out = {}
    for us_strat, eu_strat in PAIRS:
        us = list(conn.execute(
            "SELECT id, market_id, direction, price, COALESCE(pnl,0), status "
            "FROM trades WHERE strategy=? AND status='paper_settled'", (us_strat,)))
        eu = list(conn.execute(
            "SELECT id, market_id, direction, price, COALESCE(pnl,0), status "
            "FROM trades WHERE strategy=? AND status='paper_settled'", (eu_strat,)))
        us_tix = {r[1] for r in us}
        eu_tix = {r[1] for r in eu}
        overlap = us_tix & eu_tix
        us_only = us_tix - eu_tix
        out[f"{us_strat}/{eu_strat}"] = {
            "overlap_us": [r for r in us if r[1] in overlap],
            "overlap_eu": [r for r in eu if r[1] in overlap],
            "us_only": [r for r in us if r[1] in us_only],
        }
    return out


def clv_direction(conn, trade_id, direction):
    """+1 if closing moved toward entry direction, -1 if away, 0 if unknown."""
    rows = list(conn.execute(
        "SELECT snapshot_at, yes_ask, no_ask FROM clv_snapshots WHERE trade_id=?",
        (trade_id,),
    ))
    snaps = {r[0]: (r[1], r[2]) for r in rows}
    entry = snaps.get("entry")
    close = snaps.get("pre_close") or snaps.get("closing")
    if not entry or not close or entry[0] is None or close[0] is None:
        return 0
    if direction == "yes":
        return 1 if close[0] > entry[0] else (-1 if close[0] < entry[0] else 0)
    return 1 if close[1] > entry[1] else (-1 if close[1] < entry[1] else 0)


def bucket_stats(conn, trades, direction_key=2, pnl_key=4):
    pnl = sum(r[pnl_key] for r in trades)
    wins = sum(1 for r in trades if r[pnl_key] > 0)
    clvs = [clv_direction(conn, r[0], r[direction_key]) for r in trades]
    clv_pos = sum(1 for c in clvs if c > 0)
    clv_neg = sum(1 for c in clvs if c < 0)
    return {
        "n": len(trades),
        "pnl_total": round(pnl, 2),
        "pnl_per_trade": round(pnl / len(trades), 3) if trades else 0,
        "win_rate": round(wins / len(trades), 3) if trades else 0,
        "clv_positive_rate": round(clv_pos / len(trades), 3) if trades else 0,
        "clv_negative_rate": round(clv_neg / len(trades), 3) if trades else 0,
    }


def v31_pinnacle_gap(conn):
    """V31 entries: |pinnacle_prob - entry_price| distribution."""
    rows = list(conn.execute("""
        SELECT t.direction, t.price, s.pinnacle_prob
        FROM trades t JOIN clv_snapshots s ON s.trade_id=t.id
        WHERE t.strategy LIKE 'sports_%_v31' AND s.snapshot_at='entry'
          AND s.pinnacle_prob IS NOT NULL
    """))
    gaps = []
    for direction, price, pin in rows:
        implied = price if direction == "yes" else 1 - price
        gaps.append(abs(pin - implied))
    if not gaps:
        return {}
    gaps.sort()
    return {
        "n": len(gaps),
        "mean": round(statistics.mean(gaps), 4),
        "median": round(statistics.median(gaps), 4),
        "p25": round(gaps[len(gaps) // 4], 4),
        "p75": round(gaps[(3 * len(gaps)) // 4], 4),
    }


def pinnacle_predicts_closing(conn):
    """V31 overlap: did closing move toward Pinnacle entry view?"""
    rows = list(conn.execute("""
        SELECT t.id, t.direction, t.price, se.pinnacle_prob
        FROM trades t JOIN clv_snapshots se ON se.trade_id=t.id
        WHERE t.strategy LIKE 'sports_%_v31' AND se.snapshot_at='entry'
          AND se.pinnacle_prob IS NOT NULL
    """))
    converged = 0
    diverged = 0
    moves = []
    for tid, direction, price, pin in rows:
        implied_entry = price if direction == "yes" else 1 - price
        close = list(conn.execute(
            "SELECT yes_ask, no_ask FROM clv_snapshots "
            "WHERE trade_id=? AND snapshot_at IN ('pre_close','closing') "
            "ORDER BY snapshot_at LIMIT 1", (tid,)))
        if not close:
            continue
        close_yes, close_no = close[0]
        implied_close = close_yes if direction == "yes" else close_no
        if implied_close is None:
            continue
        # Did market move toward Pinnacle?
        toward = (pin - implied_entry) * (implied_close - implied_entry)
        if toward > 0:
            converged += 1
        elif toward < 0:
            diverged += 1
        moves.append(implied_close - implied_entry)
    total = converged + diverged
    return {
        "n": total,
        "convergence_rate": round(converged / total, 3) if total else 0,
        "avg_move": round(statistics.mean(moves), 4) if moves else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    out_path = OUT_DIR / f"v3_v31_overlap_{today}.md"

    buckets = bucket_trades(conn)
    gap_stats = v31_pinnacle_gap(conn)
    predict_stats = pinnacle_predicts_closing(conn)

    lines = [f"# V3 vs V31 Overlap Analysis — {today}", ""]
    lines.append("## (a) CLV direction per bucket\n")
    lines.append("| pair | bucket | n | PnL | per-trade | win rate | CLV+ rate | CLV- rate |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for pair_name, b in buckets.items():
        for bkt, trades in b.items():
            s = bucket_stats(conn, trades)
            lines.append(
                f"| {pair_name} | {bkt} | {s['n']} | {s['pnl_total']} | "
                f"{s['pnl_per_trade']} | {s['win_rate']} | "
                f"{s['clv_positive_rate']} | {s['clv_negative_rate']} |"
            )
    lines.append("")
    lines.append("## (b) V31 entry Pinnacle-Kalshi gap")
    lines.append(f"```\n{gap_stats}\n```")
    lines.append("")
    lines.append("## (c) Pinnacle-predicts-closing")
    lines.append(f"```\n{predict_stats}\n```")
    lines.append("")
    lines.append("## Interpretation (1 paragraph)")
    lines.append(
        "Placeholder — fill in based on numbers above. "
        "Key questions: (1) Is us-only CLV adverse? (2) Is V31 overlap "
        "CLV positive? (3) Is convergence_rate > 0.55 (Pinnacle predicts)?"
    )

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.3 — Run the script against EC2 DB copy**

```bash
scp -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52:~/kalshi-arb/data/bot.db /tmp/bot_ec2.db
python -m scripts.research.v3_v31_overlap_analysis --db /tmp/bot_ec2.db
cat docs/research/v3_v31_overlap_$(date +%F).md
```

Expected: markdown file with populated tables.

- [ ] **Step 8.4 — Commit (script + generated report)**

```bash
git add scripts/research/__init__.py scripts/research/v3_v31_overlap_analysis.py docs/research/
git commit -m "feat(research): retrospective V3 vs V31 overlap analysis

Generates docs/research/v3_v31_overlap_<date>.md with bucket CLV
breakdown, V31 entry Pinnacle-Kalshi gap distribution, and Pinnacle-
predicts-closing convergence rate. First run against EC2 snapshot
included.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9 — Deploy and post-deploy verification

**Files:** none (deploy + DB checks only)

- [ ] **Step 9.1 — Push to origin**

```bash
git push origin master
```

- [ ] **Step 9.2 — Deploy to EC2**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 \
  "cd kalshi-arb && git pull && sudo systemctl restart kalshi-bot && sleep 5 && sudo systemctl is-active kalshi-bot"
```

Expected: `active`.

- [ ] **Step 9.3 — Verify schema migration ran**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "python3 -c '
import sqlite3
c = sqlite3.connect(\"/home/ubuntu/kalshi-arb/data/bot.db\")
cols = [r[1] for r in c.execute(\"PRAGMA table_info(clv_snapshots)\")]
print(cols)
assert \"us_consensus_prob\" in cols
assert \"eu_consensus_prob\" in cols
assert \"pinnacle_age_s\" in cols
print(\"migration OK\")
'"
```

Expected: prints column list with all 5 new columns + `migration OK`.

- [ ] **Step 9.4 — Wait 30 minutes, then verify first entry populated**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "python3 -c '
import sqlite3
c = sqlite3.connect(\"/home/ubuntu/kalshi-arb/data/bot.db\")
rows = list(c.execute(\"\"\"
SELECT t.strategy, s.us_consensus_prob, s.eu_consensus_prob, s.pinnacle_age_s,
       s.kalshi_yes_ask
FROM trades t JOIN clv_snapshots s ON s.trade_id=t.id
WHERE s.snapshot_at=\\\"entry\\\" AND s.us_consensus_prob IS NOT NULL
ORDER BY s.id DESC LIMIT 5
\"\"\"))
for r in rows: print(r)
print(\"count:\", len(rows))
'"
```

Expected: ≥ 1 row with non-NULL `us_consensus_prob` and `eu_consensus_prob`.

- [ ] **Step 9.5 — Verify freshness log resumed**

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "tail -3 ~/kalshi-arb/data/odds_freshness_log.jsonl && wc -l ~/kalshi-arb/data/odds_freshness_log.jsonl"
```

Expected: recent timestamps (within last 10 min), line count higher than the pre-deploy 360.

- [ ] **Step 9.6 — Update memory**

Append to `/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/strategy_sports_v31.md` (or create `strategy_v3_v31_hypothesis_test.md`) noting:
- Instrumentation deployed YYYY-MM-DD
- Forward decision target: YYYY-MM-DD + 14 days
- Spec reference: `docs/superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md`
- Plan reference: `docs/superpowers/plans/2026-04-15-v3-v31-hypothesis-test.md`

Also add one-line index entry to `MEMORY.md`.

- [ ] **Step 9.7 — Final commit (memory already separate repo; only applies if README or CHANGELOG updated)**

No commit required for memory files (they live outside repo).

---

## Task 10 — 2-week forward analysis (deferred)

**Files:**
- Modify: `scripts/research/v3_v31_overlap_analysis.py` (add sections d/e/f/g)

Not executed now. Schedule reminder: 2026-04-29.

Extension tasks on that date:

- [ ] Extend script with:
  - (d) us-only entries + `pinnacle_prob` / `eu_consensus_prob` 3-case classification
  - (e) `pinnacle_age_s` histogram for V31 vs us-only
  - (f) CLV × freshness quartile breakdown
  - (g) A_score / C_score / Recommendation auto-output per spec Part 1
- [ ] Run, generate `docs/research/v3_v31_forward_<date>.md`
- [ ] Act on recommendation (DISABLE_V3 / HOLD / MORE_DATA) — separate spec if action needed

---

## Self-Review Notes

**Spec coverage check:**
- Part 1 decision framework → implemented in Task 10 (g) output + Task 8 interpretation
- Part 2 schema migration → Task 1
- Part 2 odds_client age return → Task 4 (via PinnacleAgeTracker + payload hash)
- Part 2 entry snapshot extension → Tasks 2, 5
- Part 2 closing pinnacle_prob → Task 6 (with deferral fallback)
- Part 2 freshness 5-min task → Task 7
- Part 3 immediate script → Task 8
- Part 3 forward script → Task 10 (deferred)
- Part 3 decision template → Task 10 (g)

**Type consistency check:**
- `record_clv_snapshot` signature (Task 2): `us_consensus_prob`, `eu_consensus_prob`, `pinnacle_age_s`, `kalshi_yes_bid`, `kalshi_yes_ask` — same names used throughout Tasks 5, 8.
- `PinnacleAgeTracker.observe(region, sport, pinnacle_hash, now=None)` — Task 3 def, Task 4 usage match.
- `_compute_pinnacle_payload_hash(games)` — Task 4 def, Task 5 reuse in `_dual_region_consensus`.
- `_dual_region_consensus(sport, game) → (us, eu, pin, age_s)` — Task 5 def, Task 5 consumer wire-up match.

**Gap:** Spec mentions `odds_client.py` explicitly but codebase keeps odds logic inside `strategies/sports_trader.py`. Plan follows reality (odds logic stays in sports_trader). No separate client extraction in scope.
