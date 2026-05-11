# Cross-Venue Polymarket Trader (B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single sub-strategy `xvenue_polymarket` that fires directional trades on Kalshi tickers when Polymarket's implied probability (read via gamma-api public REST) deviates from Kalshi's implied probability by ≥ MIN_DEVIATION (default 0.10 = fee 7c + 3c cushion), governed by manual JSON slug↔ticker mapping with direct/reversed outcome semantics.

**Architecture:** Stateless `core/polymarket_scraper.py` + stateless `core/polymarket_mapping.py` (JSON loader). 1-min cron upserts to `polymarket_quotes` table; strategy runs inside existing `_strategy_loop` (60-sec cadence — Polymarket political markets are slow-moving, no separate fast-scan task needed). Single-side execution on Kalshi only (NY resident, Polymarket trading blocked). 9-gate pipeline mirrors C1/B2.

**Tech Stack:** Python 3 + httpx + aiosqlite + pytest. Codebase patterns: `strategies/macro_event.py` (C1, merged PR #2 — 9-gate orchestration), `strategies/crypto_fair_value.py` (B2, PR #3 — fast-scan reference but B1 doesn't need it), `core/order_manager.py:438+` (dedup gate prefix).

**Spec:** `docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md` (commit d99bca6).

---

## File Structure

**Create:**
- `core/polymarket_scraper.py` — gamma-api REST client, single+batch fetch (~120 LOC)
- `core/polymarket_mapping.py` — JSON loader + lookup (~80 LOC)
- `strategies/xvenue_polymarket.py` — single strategy class with 9-gate orchestration (~220 LOC)
- `scripts/polymarket_quote_cron.py` — 1-min refresh entrypoint (~80 LOC)
- `scripts/research/xvenue_signal_scanner.py` — Phase 0 dry-run logger (~130 LOC)
- `data/polymarket_kalshi_mapping.json` — v0 seed 20 mappings (~70 LOC, hand-written)
- `tests/test_polymarket_scraper.py` — mocked HTTP tests (~150 LOC)
- `tests/test_polymarket_mapping.py` — JSON loader tests (~100 LOC)
- `tests/test_polymarket_quotes_db.py` — DB round-trip tests (~100 LOC)
- `tests/test_xvenue_strategy.py` — strategy unit + integration tests (~220 LOC)
- `tests/test_xvenue_dedup.py` — dedup gate prefix integration test (~80 LOC)
- `tests/test_xvenue_config.py` — config defaults + env override (~50 LOC)

**Modify:**
- `core/database.py` — 2 new tables (`polymarket_quotes`, `cross_venue_signals`) + 4 helpers
- `core/order_manager.py` — extend dedup gate prefix to also accept `xvenue_*` (5 sites + canonical comment)
- `core/config.py` — 8 new `XVENUE_*` env knobs
- `main.py` — import + instantiate `XVenuePolymarketStrategy` + add to `_strategy_loop` tuple list

---

## Task 1: polymarket_quotes DB schema + helpers

**Files:**
- Modify: `core/database.py`
- Create: `tests/test_polymarket_quotes_db.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_polymarket_quotes_db.py
import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_and_fetch_polymarket_quote():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_polymarket_quote(
            slug="will-trump-win-2028",
            yes_price=0.42, no_price=0.58,
            volume_24h=125000.0,
        )
        row = await db.fetch_polymarket_quote("will-trump-win-2028")
        assert row is not None
        assert row["yes_price"] == 0.42
        assert row["no_price"] == 0.58
        assert row["volume_24h"] == 125000.0
        assert row["fetched_at"]


@pytest.mark.asyncio
async def test_record_polymarket_quote_upserts():
    """Re-recording same slug updates price + fetched_at in place."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_polymarket_quote("slug-a", 0.42, 0.58, 100.0)
        await db.record_polymarket_quote("slug-a", 0.45, 0.55, 200.0)
        row = await db.fetch_polymarket_quote("slug-a")
        assert row["yes_price"] == 0.45
        assert row["volume_24h"] == 200.0


@pytest.mark.asyncio
async def test_fetch_polymarket_quote_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        assert await db.fetch_polymarket_quote("nonexistent") is None
```

- [ ] **Step 1.2: Run test to verify it fails**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
pytest tests/test_polymarket_quotes_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_polymarket_quote'`.

- [ ] **Step 1.3: Add CREATE TABLE + helpers**

In `core/database.py` `Database.initialize()`, after the `crypto_fair_value_signals` block (added by B2), append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS polymarket_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    yes_price REAL NOT NULL,
                    no_price REAL NOT NULL,
                    volume_24h REAL,
                    fetched_at TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pmq_slug_ts "
                "ON polymarket_quotes(slug, fetched_at)"
            )
```

Add two methods to `Database`:
```python
    async def record_polymarket_quote(
        self, slug: str, yes_price: float, no_price: float,
        volume_24h: float | None = None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO polymarket_quotes "
                "(slug, yes_price, no_price, volume_24h, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET "
                "yes_price = excluded.yes_price, "
                "no_price = excluded.no_price, "
                "volume_24h = excluded.volume_24h, "
                "fetched_at = excluded.fetched_at",
                (slug, yes_price, no_price, volume_24h, ts),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_polymarket_quote(self, slug: str) -> dict | None:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM polymarket_quotes WHERE slug = ?",
                (slug,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 1.4: Run test to verify it passes**

```
pytest tests/test_polymarket_quotes_db.py -v
```
Expected: 3 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add core/database.py tests/test_polymarket_quotes_db.py
git commit -m "$(cat <<'EOF'
feat(db): add polymarket_quotes table + record/fetch helpers

Persistence for B1 cross-venue Polymarket trader — caches gamma-api
quotes keyed by slug. UNIQUE constraint + ON CONFLICT upsert for
idempotent 1-min cron refresh.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: cross_venue_signals DB schema + helpers

**Files:**
- Modify: `core/database.py`
- Modify: `tests/test_polymarket_quotes_db.py`

- [ ] **Step 2.1: Append failing tests**

Append to `tests/test_polymarket_quotes_db.py`:
```python
@pytest.mark.asyncio
async def test_record_cross_venue_signal_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_cross_venue_signal(
            ticker="KXPRES-2028-T_DEM",
            slug="will-trump-win-2028",
            kalshi_implied=0.55,
            polymarket_implied=0.42,
            deviation=-0.13,
            side="no", expected_size_usdc=30.0,
            action_taken="fired",
        )
        rows = await db.fetch_recent_cross_venue_signals(limit=5)
        assert len(rows) == 1
        r = rows[0]
        assert r["slug"] == "will-trump-win-2028"
        assert r["deviation"] == -0.13
        assert r["action_taken"] == "fired"


@pytest.mark.asyncio
async def test_record_cross_venue_signal_allows_skipped_with_null_side():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_cross_venue_signal(
            ticker="KX-A", slug="slug-a",
            kalshi_implied=0.50, polymarket_implied=0.55, deviation=0.05,
            side=None, expected_size_usdc=None, action_taken="skip_min_deviation",
        )
        rows = await db.fetch_recent_cross_venue_signals(limit=5)
        assert rows[0]["action_taken"] == "skip_min_deviation"
        assert rows[0]["side"] is None
```

- [ ] **Step 2.2: Run test to verify it fails**

```
pytest tests/test_polymarket_quotes_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_cross_venue_signal'`.

- [ ] **Step 2.3: Add CREATE TABLE + helpers**

In `core/database.py` `Database.initialize()`, after the `polymarket_quotes` block from Task 1, append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS cross_venue_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    polymarket_implied REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cvs_ticker_ts "
                "ON cross_venue_signals(ticker, ts)"
            )
```

Add two methods to `Database`:
```python
    async def record_cross_venue_signal(
        self, *, ticker: str, slug: str,
        kalshi_implied: float, polymarket_implied: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO cross_venue_signals "
                "(ts, ticker, slug, kalshi_implied, polymarket_implied, "
                " deviation, side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, slug, kalshi_implied, polymarket_implied,
                 deviation, side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_cross_venue_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM cross_venue_signals ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
```

- [ ] **Step 2.4: Run test to verify it passes**

```
pytest tests/test_polymarket_quotes_db.py -v
```
Expected: 5 PASS (3 from Task 1 + 2 new).

- [ ] **Step 2.5: Commit**

```bash
git add core/database.py tests/test_polymarket_quotes_db.py
git commit -m "$(cat <<'EOF'
feat(db): add cross_venue_signals table + helpers

Logging table for B1 Phase 0 dry-run scanner and live strategy. Records
every signal evaluation with action_taken (fired | skip_*) for offline
mapping ROI analysis.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: PolymarketScraper

**Files:**
- Create: `core/polymarket_scraper.py`
- Create: `tests/test_polymarket_scraper.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_polymarket_scraper.py
from unittest.mock import patch, MagicMock

import pytest

from core.polymarket_scraper import PolymarketScraper, parse_market_response


def test_parse_market_response_active_yes_no():
    raw = {
        "slug": "will-trump-win-2028",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.42", "0.58"],
        "volume24hr": "125000",
        "active": True,
        "closed": False,
    }
    out = parse_market_response(raw)
    assert out is not None
    assert out["slug"] == "will-trump-win-2028"
    assert abs(out["yes_price"] - 0.42) < 1e-6
    assert abs(out["no_price"] - 0.58) < 1e-6
    assert out["volume_24h"] == 125000.0


def test_parse_market_response_rejects_closed():
    raw = {
        "slug": "x", "outcomes": ["Yes", "No"], "outcomePrices": ["0.5", "0.5"],
        "active": True, "closed": True,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_rejects_inactive():
    raw = {
        "slug": "x", "outcomes": ["Yes", "No"], "outcomePrices": ["0.5", "0.5"],
        "active": False, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_rejects_multi_outcome():
    """B1 v0 only handles binary Yes/No markets."""
    raw = {
        "slug": "x",
        "outcomes": ["Trump", "Biden", "Other"],
        "outcomePrices": ["0.40", "0.45", "0.15"],
        "active": True, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_rejects_malformed_prices():
    raw = {
        "slug": "x",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["not_a_number", "0.5"],
        "active": True, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_fetch_single_returns_market_on_http_200():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "slug": "test", "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.42", "0.58"], "volume24hr": "100",
        "active": True, "closed": False,
    }
    with patch("core.polymarket_scraper.httpx.get", return_value=fake_response):
        scraper = PolymarketScraper()
        out = scraper.fetch_single("test")
        assert out is not None
        assert out["slug"] == "test"


def test_fetch_single_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.polymarket_scraper.httpx.get", return_value=fake_response):
        scraper = PolymarketScraper()
        assert scraper.fetch_single("test") is None


def test_fetch_batch_returns_dict_keyed_by_slug():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = [
        {"slug": "a", "outcomes": ["Yes", "No"], "outcomePrices": ["0.3", "0.7"],
         "volume24hr": "100", "active": True, "closed": False},
        {"slug": "b", "outcomes": ["Yes", "No"], "outcomePrices": ["0.6", "0.4"],
         "volume24hr": "200", "active": True, "closed": False},
        {"slug": "c-closed", "outcomes": ["Yes", "No"], "outcomePrices": ["0.5", "0.5"],
         "volume24hr": "50", "active": True, "closed": True},
    ]
    with patch("core.polymarket_scraper.httpx.get", return_value=fake_response):
        scraper = PolymarketScraper()
        out = scraper.fetch_batch(["a", "b", "c-closed"])
        assert "a" in out
        assert "b" in out
        assert "c-closed" not in out  # closed market filtered
        assert out["a"]["yes_price"] == 0.3
```

- [ ] **Step 3.2: Run test to verify it fails**

```
pytest tests/test_polymarket_scraper.py -v
```
Expected: FAIL with `ImportError: cannot import name 'PolymarketScraper'`.

- [ ] **Step 3.3: Write minimal implementation**

```python
# core/polymarket_scraper.py
"""Polymarket gamma-api public REST scraper.

No auth required (read-only public market data). Returns None on any
parse/HTTP failure — caller (cron) handles fallback to stale cache.

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("polymarket_scraper")

GAMMA_API_BASE = "https://gamma-api.polymarket.com/markets"


def parse_market_response(raw: dict) -> Optional[dict]:
    """Normalize gamma-api market dict → {slug, yes_price, no_price, volume_24h}.

    Returns None if market is closed, inactive, multi-outcome, or has
    malformed price data.
    """
    if not raw.get("active") or raw.get("closed"):
        return None
    outcomes = raw.get("outcomes") or []
    if outcomes != ["Yes", "No"]:
        return None
    prices = raw.get("outcomePrices") or []
    if len(prices) < 2:
        return None
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (TypeError, ValueError):
        return None
    vol_raw = raw.get("volume24hr") or raw.get("volume")
    try:
        volume_24h = float(vol_raw) if vol_raw is not None else None
    except (TypeError, ValueError):
        volume_24h = None
    slug = raw.get("slug")
    if not slug:
        return None
    return {
        "slug": slug,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume_24h": volume_24h,
    }


class PolymarketScraper:
    """Single-market + batch-market fetcher for Polymarket gamma-api."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_single(self, slug: str) -> Optional[dict]:
        """Returns normalized market dict or None."""
        try:
            response = httpx.get(
                f"{GAMMA_API_BASE}?slug={slug}", timeout=self._timeout,
            )
        except Exception as e:
            logger.warning(f"Polymarket fetch failed for {slug}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Polymarket HTTP {response.status_code} for {slug}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Polymarket JSON parse failed for {slug}: {e}")
            return None
        # gamma-api returns either a list (when filtering) or single dict
        if isinstance(data, list):
            if not data:
                return None
            return parse_market_response(data[0])
        return parse_market_response(data)

    def fetch_batch(self, slugs: list[str]) -> dict[str, dict]:
        """Returns {slug: normalized_dict} for slugs that parse successfully.

        gamma-api batch syntax: ?slugs=a,b,c (comma-separated).
        """
        if not slugs:
            return {}
        slug_param = ",".join(slugs)
        try:
            response = httpx.get(
                f"{GAMMA_API_BASE}?slugs={slug_param}", timeout=self._timeout,
            )
        except Exception as e:
            logger.warning(f"Polymarket batch fetch failed: {e}")
            return {}
        if response.status_code != 200:
            logger.warning(f"Polymarket batch HTTP {response.status_code}")
            return {}
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Polymarket batch JSON parse failed: {e}")
            return {}
        if not isinstance(data, list):
            return {}
        out: dict[str, dict] = {}
        for raw in data:
            parsed = parse_market_response(raw)
            if parsed:
                out[parsed["slug"]] = parsed
        return out
```

- [ ] **Step 3.4: Run test to verify it passes**

```
pytest tests/test_polymarket_scraper.py -v
```
Expected: 8 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/polymarket_scraper.py tests/test_polymarket_scraper.py
git commit -m "$(cat <<'EOF'
feat(xvenue): add PolymarketScraper (gamma-api public REST)

Single + batch fetch with normalize_market_response helper. Rejects
closed/inactive/multi-outcome markets and malformed prices. Returns None
on any HTTP/parse failure — caller (cron) handles fallback to stale cache.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: PolymarketKalshiMapping (JSON loader)

**Files:**
- Create: `core/polymarket_mapping.py`
- Create: `tests/test_polymarket_mapping.py`

- [ ] **Step 4.1: Write the failing test**

```python
# tests/test_polymarket_mapping.py
import json
import os
import tempfile
import pytest

from core.polymarket_mapping import PolymarketKalshiMapping


def _write_mapping(tmp_dir: str, mappings: list[dict]) -> str:
    path = os.path.join(tmp_dir, "mapping.json")
    with open(path, "w") as f:
        json.dump(mappings, f)
    return path


def test_load_and_lookup_direct():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {
                "polymarket_slug": "will-trump-win-2028",
                "kalshi_ticker_prefix": "KXPRES-2028-T",
                "outcome_match": "direct",
                "notes": "test",
            },
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("will-trump-win-2028")
        assert len(entries) == 1
        assert entries[0]["kalshi_ticker_prefix"] == "KXPRES-2028-T"
        assert entries[0]["outcome_match"] == "direct"


def test_load_and_lookup_reversed():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {
                "polymarket_slug": "will-trump-win-2028",
                "kalshi_ticker_prefix": "KXPRES-2028-DEM",
                "outcome_match": "reversed",
                "notes": "",
            },
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("will-trump-win-2028")
        assert entries[0]["outcome_match"] == "reversed"


def test_one_slug_multiple_prefixes():
    """A slug can map to multiple Kalshi prefixes (direct + reversed)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "x", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
            {"polymarket_slug": "x", "kalshi_ticker_prefix": "KX2",
             "outcome_match": "reversed", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("x")
        assert len(entries) == 2
        prefixes = sorted(e["kalshi_ticker_prefix"] for e in entries)
        assert prefixes == ["KX1", "KX2"]


def test_all_slugs():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
            {"polymarket_slug": "b", "kalshi_ticker_prefix": "KX2",
             "outcome_match": "direct", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        assert sorted(m.all_slugs()) == ["a", "b"]


def test_reload_picks_up_new_mappings():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        assert m.all_slugs() == ["a"]
        # Add a new mapping
        with open(path, "w") as f:
            json.dump([
                {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
                 "outcome_match": "direct", "notes": ""},
                {"polymarket_slug": "b", "kalshi_ticker_prefix": "KX2",
                 "outcome_match": "direct", "notes": ""},
            ], f)
        m.reload()
        assert sorted(m.all_slugs()) == ["a", "b"]


def test_missing_file_returns_empty():
    m = PolymarketKalshiMapping("/nonexistent/path.json")
    assert m.all_slugs() == []
    assert m.entries_for_slug("anything") == []
```

- [ ] **Step 4.2: Run test to verify it fails**

```
pytest tests/test_polymarket_mapping.py -v
```
Expected: FAIL with `ImportError: cannot import name 'PolymarketKalshiMapping'`.

- [ ] **Step 4.3: Write minimal implementation**

```python
# core/polymarket_mapping.py
"""Polymarket slug ↔ Kalshi ticker_prefix manual mapping (JSON-backed).

JSON schema (list of objects):
  {
    "polymarket_slug": "...",       # Polymarket URL slug
    "kalshi_ticker_prefix": "...",  # Kalshi ticker prefix (one prefix can
                                    # cover multiple ticker children)
    "outcome_match": "direct" | "reversed",  # Are Yes/No semantics aligned?
    "notes": "..."                  # Free-text for human reviewer
  }

One slug may have multiple entries (e.g., "Trump=YES" maps to both
"KXPRES-2028-T" direct and "KXPRES-2028-DEM" reversed).

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import json
import logging
import os
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("polymarket_mapping")


class PolymarketKalshiMapping:
    """JSON-backed lookup. Hot-reload via reload() — caller may invoke
    periodically (e.g., once per scan_loop iteration) so mapping edits
    take effect without bot restart.
    """

    def __init__(self, path: str):
        self._path = path
        self._by_slug: dict[str, list[dict]] = defaultdict(list)
        self.reload()

    def reload(self) -> None:
        """Re-read the JSON file. On failure (missing file, malformed JSON)
        the in-memory mapping is reset to empty."""
        new_by_slug: dict[str, list[dict]] = defaultdict(list)
        if not os.path.exists(self._path):
            logger.warning(f"Polymarket mapping file not found: {self._path}")
            self._by_slug = new_by_slug
            return
        try:
            with open(self._path) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Polymarket mapping load failed: {e}")
            self._by_slug = new_by_slug
            return
        if not isinstance(raw, list):
            logger.warning("Polymarket mapping JSON must be a list")
            self._by_slug = new_by_slug
            return
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("polymarket_slug")
            prefix = entry.get("kalshi_ticker_prefix")
            match = entry.get("outcome_match", "direct")
            if not slug or not prefix:
                continue
            if match not in ("direct", "reversed"):
                logger.warning(f"Invalid outcome_match for {slug}: {match}")
                continue
            new_by_slug[slug].append({
                "kalshi_ticker_prefix": prefix,
                "outcome_match": match,
                "notes": entry.get("notes", ""),
            })
        self._by_slug = new_by_slug

    def all_slugs(self) -> list[str]:
        return list(self._by_slug.keys())

    def entries_for_slug(self, slug: str) -> list[dict]:
        """Returns list of {kalshi_ticker_prefix, outcome_match, notes} dicts."""
        return list(self._by_slug.get(slug, []))
```

- [ ] **Step 4.4: Run test to verify it passes**

```
pytest tests/test_polymarket_mapping.py -v
```
Expected: 6 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add core/polymarket_mapping.py tests/test_polymarket_mapping.py
git commit -m "$(cat <<'EOF'
feat(xvenue): add PolymarketKalshiMapping (JSON loader with hot-reload)

Manual JSON mapping: (polymarket_slug, kalshi_ticker_prefix,
outcome_match) entries. One slug may map to multiple prefixes (direct +
reversed). reload() supports hot-reload — bot doesn't need restart when
mapping file changes. Missing/malformed JSON degrades to empty mapping
(strategy fires nothing) rather than crashing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: XVENUE_* config knobs

**Files:**
- Modify: `core/config.py`
- Create: `tests/test_xvenue_config.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_xvenue_config.py
import os
from unittest.mock import patch

from core.config import Config


XVENUE_KEYS = (
    "XVENUE_MIN_DEVIATION", "XVENUE_MIN_DEPTH_USDC",
    "XVENUE_BASE_SIZE_USDC", "XVENUE_MAX_POSITION_PER_TICKER",
    "XVENUE_MAX_TICKER_TOTAL_EXPOSURE", "XVENUE_QUOTE_STALE_MINS",
    "XVENUE_MIN_POLYMARKET_VOLUME", "XVENUE_RECENTLY_FIRED_SECS",
)


def test_xvenue_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in XVENUE_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.XVENUE_MIN_DEVIATION == 0.10
        assert cfg.XVENUE_MIN_DEPTH_USDC == 30.0
        assert cfg.XVENUE_BASE_SIZE_USDC == 20.0
        assert cfg.XVENUE_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.XVENUE_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.XVENUE_QUOTE_STALE_MINS == 15.0
        assert cfg.XVENUE_MIN_POLYMARKET_VOLUME == 5000.0
        assert cfg.XVENUE_RECENTLY_FIRED_SECS == 300.0


def test_xvenue_config_env_override():
    with patch.dict(os.environ, {
        "XVENUE_MIN_DEVIATION": "0.15",
        "XVENUE_MIN_POLYMARKET_VOLUME": "10000",
    }):
        cfg = Config()
        assert cfg.XVENUE_MIN_DEVIATION == 0.15
        assert cfg.XVENUE_MIN_POLYMARKET_VOLUME == 10000.0
```

- [ ] **Step 5.2: Run test to verify it fails**

```
pytest tests/test_xvenue_config.py -v
```
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'XVENUE_MIN_DEVIATION'`.

- [ ] **Step 5.3: Add config attributes**

Append to `Config.__init__` body in `core/config.py` (after the CRYPTO_FAIR_* block from B2):
```python
        # Cross-Venue Polymarket Trader (B1) — single sub-strategy xvenue_polymarket.
        # Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
        self.XVENUE_MIN_DEVIATION = float(os.getenv("XVENUE_MIN_DEVIATION", "0.10"))
        self.XVENUE_MIN_DEPTH_USDC = float(os.getenv("XVENUE_MIN_DEPTH_USDC", "30"))
        self.XVENUE_BASE_SIZE_USDC = float(os.getenv("XVENUE_BASE_SIZE_USDC", "20"))
        self.XVENUE_MAX_POSITION_PER_TICKER = float(os.getenv("XVENUE_MAX_POSITION_PER_TICKER", "30"))
        self.XVENUE_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("XVENUE_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.XVENUE_QUOTE_STALE_MINS = float(os.getenv("XVENUE_QUOTE_STALE_MINS", "15"))
        self.XVENUE_MIN_POLYMARKET_VOLUME = float(os.getenv("XVENUE_MIN_POLYMARKET_VOLUME", "5000"))
        self.XVENUE_RECENTLY_FIRED_SECS = float(os.getenv("XVENUE_RECENTLY_FIRED_SECS", "300"))
```

- [ ] **Step 5.4: Run test to verify it passes**

```
pytest tests/test_xvenue_config.py -v
```
Expected: 2 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add core/config.py tests/test_xvenue_config.py
git commit -m "$(cat <<'EOF'
feat(config): add XVENUE_* env knobs (8 thresholds)

Per B1 spec: MIN_DEVIATION=0.10 (fee 0.07 + 3c cushion), depth $30,
base size $20, per-ticker cap $30, cross-strategy cap $80, quote stale
15min, Polymarket volume floor $5000, recently-fired 5min cooldown.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Extend has_existing_trade dedup gate to xvenue_*

**Files:**
- Modify: `core/order_manager.py` (5 sites + comment)
- Create: `tests/test_xvenue_dedup.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_xvenue_dedup.py
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_xvenue_polymarket_blocked_when_paper_filled_exists():
    """xvenue_polymarket re-entry on existing paper_filled trade must be skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="xvenue_polymarket",
            market_id="KXPRES-2028-T_DEM",
            direction="no", price=0.45, size=20.0, fee=0.07,
            status="paper_filled",
        )
        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0

        result = await om._paper_market_order(
            ticker="KXPRES-2028-T_DEM", side="no",
            size_usdc=20.0, price=0.45,
            strategy="xvenue_polymarket",
            market_id="KXPRES-2028-T_DEM",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
```

- [ ] **Step 6.2: Run test to verify it fails**

```
pytest tests/test_xvenue_dedup.py -v
```
Expected: FAIL — current gate is `(sports_*, ladder_*, macro_*, crypto_*)`, xvenue_* skips dedup.

- [ ] **Step 6.3: Extend dedup gate at 5 sites**

Find the 5 sites:
```bash
grep -c 'strategy.startswith("crypto_")' core/order_manager.py
```
Expected: 5.

For each of the 5 sites, change:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
        strategy.startswith("macro_") or strategy.startswith("crypto_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```
to:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
        strategy.startswith("macro_") or strategy.startswith("crypto_") or
        strategy.startswith("xvenue_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```

Verify all 5 updated:
```bash
grep -c 'strategy.startswith("xvenue_")' core/order_manager.py
```
Expected: 5.

Update the canonical comment at `_paper_market_order` to add `xvenue_*`:
```python
        # DB-side dedup applies to:
        #   sports_*  — single game has one outcome, re-entry is always a bug.
        #   ladder_*  — single Kalshi event ladder leg has one outcome; V1
        #               shutdown_fade over-fired 13× on KXNUMSHUTDOWNS by
        #               skipping this guard. See known_bugs.md.
        #   macro_*   — single (FOMC bucket | CPI bucket) outcome — same
        #               one-shot invariant. C1 macro_event sub-strategy.
        #   crypto_*  — single (crypto strike at close) outcome — same
        #               one-shot invariant. B2 crypto_fair_value sub-strategy.
        #   xvenue_*  — single Kalshi market resolved by external signal — one
        #               trade per (strategy, market_id) per Polymarket alpha
        #               cycle. B1 xvenue_polymarket sub-strategy.
        # Other strategies (mentions, market_maker, spread_scanner)
        # legitimately re-enter the same ticker.
```

- [ ] **Step 6.4: Run tests + regression check**

```
pytest tests/test_xvenue_dedup.py tests/test_crypto_fair_value_dedup.py tests/test_macro_event_dedup.py tests/test_ladder_dedup_guard.py -v
```
Expected: All PASS (4 dedup tests).

- [ ] **Step 6.5: Commit**

```bash
git add core/order_manager.py tests/test_xvenue_dedup.py
git commit -m "$(cat <<'EOF'
fix(order_manager): extend has_existing_trade dedup gate to xvenue_* prefix

B1 xvenue_polymarket sub-strategy follows the same one-shot-per-(strategy,
market_id) invariant. Cross-venue signal (Polymarket) → single Kalshi
trade per market per alpha cycle. Extends the gate at 5 sites + updates
the canonical policy comment.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: v0 seed Polymarket↔Kalshi mapping JSON

**Files:**
- Create: `data/polymarket_kalshi_mapping.json`

- [ ] **Step 7.1: Create seed mapping file**

The 20 mappings below are starter pairs covering the major Polymarket↔Kalshi overlap categories. The exact slugs/prefixes will need manual verification on first cron run — if a slug doesn't exist on Polymarket, the cron logs a warning and skips. If a kalshi_ticker_prefix has no current open markets, the strategy simply has no opportunity.

```json
[
  {
    "polymarket_slug": "will-trump-win-2028",
    "kalshi_ticker_prefix": "KXPRES-2028-T",
    "outcome_match": "direct",
    "notes": "Trump = YES on both venues (assumes Kalshi PRES-2028 uses Trump-favored YES)"
  },
  {
    "polymarket_slug": "will-trump-win-2028",
    "kalshi_ticker_prefix": "KXPRES-2028-DEM",
    "outcome_match": "reversed",
    "notes": "Trump=YES (Poly) → Dem=NO (Kalshi)"
  },
  {
    "polymarket_slug": "fed-decision-june-2026",
    "kalshi_ticker_prefix": "KXFEDDECISION-26JUN-",
    "outcome_match": "direct",
    "notes": "FOMC June 2026 — both venues binary bucket per outcome"
  },
  {
    "polymarket_slug": "fed-decision-july-2026",
    "kalshi_ticker_prefix": "KXFEDDECISION-26JUL-",
    "outcome_match": "direct",
    "notes": "FOMC July 2026"
  },
  {
    "polymarket_slug": "us-recession-2026",
    "kalshi_ticker_prefix": "KXRECESSION-26-",
    "outcome_match": "direct",
    "notes": "US recession declaration 2026"
  },
  {
    "polymarket_slug": "cpi-june-2026",
    "kalshi_ticker_prefix": "KXCPI-26JUN-",
    "outcome_match": "direct",
    "notes": "June CPI release — both venues bucketed"
  },
  {
    "polymarket_slug": "cpi-july-2026",
    "kalshi_ticker_prefix": "KXCPI-26JUL-",
    "outcome_match": "direct",
    "notes": "July CPI release"
  },
  {
    "polymarket_slug": "btc-95000-by-june-2026",
    "kalshi_ticker_prefix": "KXBTC-26JUN-T95000",
    "outcome_match": "direct",
    "notes": "BTC ≥ $95k by June 2026 — verify Kalshi exact strike"
  },
  {
    "polymarket_slug": "btc-100000-by-2026",
    "kalshi_ticker_prefix": "KXBTC-2026-T100000",
    "outcome_match": "direct",
    "notes": "BTC ≥ $100k by year-end 2026"
  },
  {
    "polymarket_slug": "eth-4000-by-june-2026",
    "kalshi_ticker_prefix": "KXETH-26JUN-T4000",
    "outcome_match": "direct",
    "notes": "ETH ≥ $4k by June 2026"
  },
  {
    "polymarket_slug": "trump-approval-above-45-june",
    "kalshi_ticker_prefix": "KXTRUMPAPPROVAL-26JUN-45",
    "outcome_match": "direct",
    "notes": "Trump approval > 45% in June 2026 polls"
  },
  {
    "polymarket_slug": "vance-vp-2028",
    "kalshi_ticker_prefix": "KXVP-2028-VANCE",
    "outcome_match": "direct",
    "notes": "JD Vance as VP candidate 2028"
  },
  {
    "polymarket_slug": "harris-2028",
    "kalshi_ticker_prefix": "KXPRES-2028-HARRIS",
    "outcome_match": "direct",
    "notes": "Harris wins 2028"
  },
  {
    "polymarket_slug": "ukraine-russia-ceasefire-2026",
    "kalshi_ticker_prefix": "KXUKRRUSSIACEASE-26-",
    "outcome_match": "direct",
    "notes": "Ceasefire by end-2026"
  },
  {
    "polymarket_slug": "china-taiwan-conflict-2026",
    "kalshi_ticker_prefix": "KXCHINATAIWAN-26-",
    "outcome_match": "direct",
    "notes": "China-Taiwan conflict event 2026"
  },
  {
    "polymarket_slug": "us-government-shutdown-2026",
    "kalshi_ticker_prefix": "KXGOVTSHUTDOWN-26-",
    "outcome_match": "direct",
    "notes": "Govt shutdown by end-2026 (Kalshi listing status: verify)"
  },
  {
    "polymarket_slug": "supreme-court-vacancy-2026",
    "kalshi_ticker_prefix": "KXSCOTUSVACANCY-26-",
    "outcome_match": "direct",
    "notes": "SCOTUS vacancy in 2026"
  },
  {
    "polymarket_slug": "nasdaq-record-high-2026",
    "kalshi_ticker_prefix": "KXNASDAQ-26-NEWHIGH",
    "outcome_match": "direct",
    "notes": "Nasdaq new all-time high in 2026"
  },
  {
    "polymarket_slug": "sp500-7000-2026",
    "kalshi_ticker_prefix": "KXSP500-26-T7000",
    "outcome_match": "direct",
    "notes": "S&P 500 ≥ 7000 by end-2026"
  },
  {
    "polymarket_slug": "oil-100-2026",
    "kalshi_ticker_prefix": "KXOIL-26-T100",
    "outcome_match": "direct",
    "notes": "WTI crude ≥ $100 in 2026"
  }
]
```

- [ ] **Step 7.2: Verify JSON loads cleanly**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python -c "
from core.polymarket_mapping import PolymarketKalshiMapping
m = PolymarketKalshiMapping('data/polymarket_kalshi_mapping.json')
print(f'Loaded {len(m.all_slugs())} slug entries')
for s in m.all_slugs()[:5]:
    print(f'  {s} → {m.entries_for_slug(s)}')
"
```
Expected: `Loaded 19 slug entries` (19 because "will-trump-win-2028" appears twice but `all_slugs()` returns unique keys).

- [ ] **Step 7.3: Commit**

```bash
git add data/polymarket_kalshi_mapping.json
git commit -m "$(cat <<'EOF'
feat(xvenue): add v0 seed Polymarket↔Kalshi mapping JSON (20 entries)

Hand-written seed covering 2028 elections, FOMC, CPI, crypto, geopolitics,
markets. Some Kalshi ticker_prefix values are best-guess and will need
manual verification on first cron run (slug missing → cron warning + skip,
no fire). Phase 0 prevalence scan informs which mappings to retire vs
expand.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: XVenuePolymarketStrategy — universe + mapping resolution + window

**Files:**
- Create: `strategies/xvenue_polymarket.py`
- Create: `tests/test_xvenue_strategy.py`

- [ ] **Step 8.1: Write the failing test**

```python
# tests/test_xvenue_strategy.py
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest

from strategies.xvenue_polymarket import XVenuePolymarketStrategy


def _cfg():
    return MagicMock(
        XVENUE_MIN_DEVIATION=0.10,
        XVENUE_MIN_DEPTH_USDC=30.0,
        XVENUE_BASE_SIZE_USDC=20.0,
        XVENUE_MAX_POSITION_PER_TICKER=30.0,
        XVENUE_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        XVENUE_QUOTE_STALE_MINS=15.0,
        XVENUE_MIN_POLYMARKET_VOLUME=5000.0,
        XVENUE_RECENTLY_FIRED_SECS=300.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_kalshi_markets_for_prefix_filters_correctly():
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXPRES-2028-T_DEM"},
        {"ticker": "KXPRES-2028-T_GOP"},
        {"ticker": "KXFEDDECISION-26JUN-C25"},
        {"ticker": "KXBTC-26MAY1017-T89749.99"},
    ])
    mapping = MagicMock()
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), scanner, MagicMock(),
                                  mapping=mapping)
    out = s._kalshi_markets_for_prefix("KXPRES-2028-")
    tickers = sorted(m["ticker"] for m in out)
    assert tickers == ["KXPRES-2028-T_DEM", "KXPRES-2028-T_GOP"]


def test_kalshi_markets_for_prefix_returns_empty_when_no_match():
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXBTC-X-T1"},
    ])
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), scanner, MagicMock(),
                                  mapping=MagicMock())
    assert s._kalshi_markets_for_prefix("KXSOMETHING-") == []


def test_apply_outcome_match_direct():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    assert s._apply_outcome_match(0.42, "direct") == 0.42


def test_apply_outcome_match_reversed():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Trump=YES at 0.42 on Polymarket → Dem-side semantics = 1 - 0.42 = 0.58
    assert abs(s._apply_outcome_match(0.42, "reversed") - 0.58) < 1e-6


def test_is_quote_fresh_when_recent():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    now = datetime.now(timezone.utc)
    quote_row = {"fetched_at": (now - timedelta(minutes=5)).isoformat()}
    assert s._is_quote_fresh(quote_row, now=now) is True


def test_is_quote_fresh_rejects_stale():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    now = datetime.now(timezone.utc)
    quote_row = {"fetched_at": (now - timedelta(minutes=30)).isoformat()}
    assert s._is_quote_fresh(quote_row, now=now) is False


def test_is_quote_volume_ok():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    assert s._is_quote_volume_ok({"volume_24h": 10000.0}) is True
    assert s._is_quote_volume_ok({"volume_24h": 1000.0}) is False
    assert s._is_quote_volume_ok({"volume_24h": None}) is False
```

- [ ] **Step 8.2: Run test to verify it fails**

```
pytest tests/test_xvenue_strategy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.xvenue_polymarket'`.

- [ ] **Step 8.3: Write minimal implementation**

```python
# strategies/xvenue_polymarket.py
"""Cross-Venue Polymarket Trader (B1) — Polymarket-anchored Kalshi single-side.

Reads Polymarket gamma-api quotes (cached via 1-min cron in
polymarket_quotes table). For each manual mapping entry, compares
Polymarket implied prob to Kalshi orderbook → fires single-side
directional Kalshi trade when |deviation| ≥ MIN_DEVIATION and EV > 0.

Runs in main bot's _strategy_loop (60-sec cadence) — Polymarket
political markets are slow-moving, no separate fast-scan task needed.

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("xvenue_polymarket")


class XVenuePolymarketStrategy:
    STRATEGY_NAME = "xvenue_polymarket"

    def __init__(
        self, config, order_manager, market_scanner, kalshi_client,
        *, mapping,
    ):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._mapping = mapping
        self._recently_fired: dict[str, float] = {}

    def _kalshi_markets_for_prefix(self, prefix: str) -> list[dict]:
        """Return open Kalshi markets whose ticker startswith `prefix`."""
        return [
            m for m in self._scanner.get_all_markets()
            if m.get("ticker", "").startswith(prefix)
        ]

    @staticmethod
    def _apply_outcome_match(yes_prob: float, outcome_match: str) -> float:
        """For 'reversed' mapping, invert the prob to align Kalshi YES semantics."""
        if outcome_match == "reversed":
            return 1.0 - yes_prob
        return yes_prob

    def _is_quote_fresh(
        self, quote_row: dict, now: Optional[datetime] = None,
    ) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        fetched_at_iso = quote_row.get("fetched_at", "")
        if not fetched_at_iso:
            return False
        try:
            fetched_at = datetime.fromisoformat(
                fetched_at_iso.replace("Z", "+00:00")
            )
        except Exception:
            return False
        age_min = (now - fetched_at).total_seconds() / 60.0
        return age_min <= self.config.XVENUE_QUOTE_STALE_MINS

    def _is_quote_volume_ok(self, quote_row: dict) -> bool:
        vol = quote_row.get("volume_24h")
        if vol is None:
            return False
        return float(vol) >= self.config.XVENUE_MIN_POLYMARKET_VOLUME
```

- [ ] **Step 8.4: Run test to verify it passes**

```
pytest tests/test_xvenue_strategy.py -v
```
Expected: 7 PASS.

- [ ] **Step 8.5: Commit**

```bash
git add strategies/xvenue_polymarket.py tests/test_xvenue_strategy.py
git commit -m "$(cat <<'EOF'
feat(xvenue): add XVenuePolymarketStrategy shell — universe + freshness + outcome match

Class skeleton with Kalshi ticker prefix lookup, Polymarket quote freshness
check (15min TTL), volume floor ($5000 24h), and direct/reversed
outcome_match application. Edge math + scan_and_execute in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: XVenuePolymarketStrategy — edge math + sizing

**Files:**
- Modify: `strategies/xvenue_polymarket.py`
- Modify: `tests/test_xvenue_strategy.py`

- [ ] **Step 9.1: Append failing tests**

Append to `tests/test_xvenue_strategy.py`:
```python
def test_compute_edge_buy_yes_when_polymarket_above_kalshi():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Polymarket says 0.50, Kalshi mid 0.30 → buy Kalshi YES
    edge = s._compute_edge(
        polymarket_yes=0.50,
        kalshi_yes_ask=0.32, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.68,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.20) < 1e-6  # 0.50 - 0.30 mid
    assert edge["entry_price"] == 0.32


def test_compute_edge_buy_no_when_polymarket_below_kalshi():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    edge = s._compute_edge(
        polymarket_yes=0.30,
        kalshi_yes_ask=0.62, kalshi_yes_bid=0.58,
        kalshi_no_ask=0.42, kalshi_no_bid=0.38,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert edge["deviation"] < 0
    assert edge["entry_price"] == 0.42  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    edge = s._compute_edge(
        polymarket_yes=0.32,  # Kalshi mid = 0.30, dev = 0.02 < 0.10
        kalshi_yes_ask=0.32, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.68,
    )
    assert edge is None


def test_compute_edge_skips_when_ev_negative():
    cfg = _cfg()
    cfg.XVENUE_MIN_DEVIATION = 0.05  # lower threshold so dev passes
    s = XVenuePolymarketStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Mid 0.20, ask 0.25, polymarket 0.27 → dev 0.07 ≥ 0.05 but
    # EV = 0.27 - 0.25 - 0.07 = -0.05 → skip
    edge = s._compute_edge(
        polymarket_yes=0.27,
        kalshi_yes_ask=0.25, kalshi_yes_bid=0.15,
        kalshi_no_ask=0.85, kalshi_no_bid=0.75,
    )
    assert edge is None


def test_size_for_edge_scales_and_caps():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # base 20, clamp(0.10/0.10, 0.5, 3.0) = 1.0 → 20
    assert s._size_for_edge(0.10) == 20.0
    # base × clamp(0.30/0.10, 0.5, 3.0) = 3.0 → 60, capped at 30
    assert s._size_for_edge(0.30) == 30.0
    # base × clamp(0.05/0.10, 0.5, 3.0) = 0.5 → 10
    assert s._size_for_edge(0.05) == 10.0
```

- [ ] **Step 9.2: Run test to verify it fails**

```
pytest tests/test_xvenue_strategy.py -v
```
Expected: 5 NEW FAIL.

- [ ] **Step 9.3: Append methods to XVenuePolymarketStrategy**

In `strategies/xvenue_polymarket.py`, add to the class:
```python
    def _compute_edge(
        self, *,
        polymarket_yes: float,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
    ) -> Optional[dict]:
        """Returns trade signal dict or None.

        Signal dict: {'side': 'yes'|'no', 'deviation': float (signed),
                      'entry_price': float, 'ev_per_contract': float}
        polymarket_yes is the Kalshi-aligned probability AFTER applying
        outcome_match — caller is responsible for inversion if needed.
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = polymarket_yes - kalshi_yes_mid
        if abs(deviation) < cfg.XVENUE_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            ev = polymarket_yes - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            ev = (1.0 - polymarket_yes) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        """Edge-scaled fire size capped by per-ticker maximum."""
        cfg = self.config
        base = cfg.XVENUE_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.XVENUE_MIN_DEVIATION))
        return min(base * scale, cfg.XVENUE_MAX_POSITION_PER_TICKER)
```

- [ ] **Step 9.4: Run test to verify it passes**

```
pytest tests/test_xvenue_strategy.py -v
```
Expected: 12 PASS (7 from Task 8 + 5 new).

- [ ] **Step 9.5: Commit**

```bash
git add strategies/xvenue_polymarket.py tests/test_xvenue_strategy.py
git commit -m "$(cat <<'EOF'
feat(xvenue): add edge math + edge-scaled sizing

_compute_edge: same math as C1/B2 — returns side + deviation + entry + ev
or None if |deviation| < MIN or EV ≤ 0 post-fee. _size_for_edge: base ×
clamp(|dev|/MIN, 0.5, 3.0) capped at per-ticker max.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: XVenuePolymarketStrategy — scan_and_execute with 9 gates

**Files:**
- Modify: `strategies/xvenue_polymarket.py`
- Modify: `tests/test_xvenue_strategy.py`

- [ ] **Step 10.1: Append failing tests**

Append to `tests/test_xvenue_strategy.py`:
```python
import time


def _kalshi_book_for_ticker(yes_ask, no_ask, depth=100.0):
    """Mock get_market_prices + get_order_book_depth pair."""
    return (yes_ask, no_ask), (depth, depth)


@pytest.mark.asyncio
async def test_scan_fires_buy_yes_when_all_gates_pass():
    """Polymarket 0.50 > Kalshi mid 0.30 → BUY YES at 0.32."""
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXTEST-A"},
    ])

    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct",
        "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)

    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["ticker"] == "KXTEST-A"
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "xvenue_polymarket"


@pytest.mark.asyncio
async def test_scan_skips_when_polymarket_quote_stale():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": (now - timedelta(minutes=30)).isoformat(),  # > 15 min
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_polymarket_volume_too_low():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 1000.0,  # < 5000 floor
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_recently_fired():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    s._recently_fired["KXTEST-A"] = time.time() - 60.0  # fired 60s ago, cap=300s
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_applies_reversed_outcome_match():
    """Polymarket Trump=YES 0.42 with reversed match → Kalshi Dem-side
    interpretation = 1 - 0.42 = 0.58.  If Kalshi DEM mid is 0.30, dev = 0.28
    → BUY YES on KX-DEM."""
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "trump-win", "yes_price": 0.42, "no_price": 0.58,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXPRES-2028-DEM"},
    ])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["trump-win"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXPRES-2028-DEM",
        "outcome_match": "reversed",  # Trump=YES on Poly → Dem=YES on Kalshi flipped
        "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_called_once()
    assert om.market_order.call_args.kwargs["side"] == "yes"
```

- [ ] **Step 10.2: Run test to verify it fails**

```
pytest tests/test_xvenue_strategy.py -v
```
Expected: 5 NEW FAIL (scan_and_execute not yet implemented).

- [ ] **Step 10.3: Implement scan_and_execute orchestration**

In `strategies/xvenue_polymarket.py`, add to the class:
```python
    async def scan_and_execute(self):
        """Single iteration — called by main bot's _strategy_loop (60s)."""
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db — xvenue_polymarket disabled")
            return

        # Hot-reload mapping each scan so JSON edits take effect without restart
        self._mapping.reload()

        cfg = self.config
        now = datetime.now(timezone.utc)
        for slug in self._mapping.all_slugs():
            quote_row = await db.fetch_polymarket_quote(slug)
            if quote_row is None:
                continue
            # Gate 1: quote freshness
            if not self._is_quote_fresh(quote_row, now=now):
                continue
            # Gate 2: volume floor
            if not self._is_quote_volume_ok(quote_row):
                continue
            poly_yes_raw = float(quote_row["yes_price"])
            for entry in self._mapping.entries_for_slug(slug):
                prefix = entry["kalshi_ticker_prefix"]
                outcome_match = entry["outcome_match"]
                poly_yes = self._apply_outcome_match(poly_yes_raw, outcome_match)

                # Gate 3: mapping resolves to open Kalshi markets
                kalshi_markets = self._kalshi_markets_for_prefix(prefix)
                for m in kalshi_markets:
                    await self._handle_market(
                        m, slug=slug, poly_yes=poly_yes, db=db, now=now,
                    )

    async def _handle_market(
        self, market: dict, *, slug: str, poly_yes: float,
        db, now: datetime,
    ):
        cfg = self.config
        ticker = market.get("ticker", "")

        # Fetch Kalshi book
        yes_price, no_price = self._client.get_market_prices(ticker)
        if yes_price is None or no_price is None:
            return
        depths = self._client.get_order_book_depth(ticker)
        yes_depth, no_depth = depths if depths else (0.0, 0.0)
        # Derive true 2-sided book (yes_bid ≈ 1 - no_ask)
        yes_ask = yes_price
        no_ask = no_price
        yes_bid = max(0.0, 1.0 - no_ask)
        no_bid = max(0.0, 1.0 - yes_ask)

        # Gates 4 + 5: deviation + EV (via _compute_edge)
        edge = self._compute_edge(
            polymarket_yes=poly_yes,
            kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
            kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
        )
        if not edge:
            return

        # Gate 6: depth (side we're entering)
        depth_for_side = (yes_depth or 0.0) if edge["side"] == "yes" else (no_depth or 0.0)
        if depth_for_side < cfg.XVENUE_MIN_DEPTH_USDC:
            return

        # Gate 7: per-strategy DB dedup enforced by OrderManager (xvenue_* prefix).
        # Gate 8: cross-strategy exposure cap.
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.XVENUE_MAX_TICKER_TOTAL_EXPOSURE:
            return

        # Gate 9: recently_fired guard
        last = self._recently_fired.get(ticker)
        if last is not None and (time.time() - last) < cfg.XVENUE_RECENTLY_FIRED_SECS:
            return

        logger.info(
            f"[XVENUE] FIRE {ticker} {edge['side']} size=${size:.2f} "
            f"slug={slug} poly={poly_yes:.4f} kalshi_mid="
            f"{(yes_ask + yes_bid) / 2:.4f} dev={edge['deviation']:+.4f} "
            f"ev={edge['ev_per_contract']:+.4f}"
        )
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
        self._recently_fired[ticker] = time.time()
```

- [ ] **Step 10.4: Run test to verify it passes**

```
pytest tests/test_xvenue_strategy.py tests/test_xvenue_dedup.py -v
```
Expected: 18 PASS (12 from Tasks 8-9 + 5 new + 1 dedup integration).

- [ ] **Step 10.5: Commit**

```bash
git add strategies/xvenue_polymarket.py tests/test_xvenue_strategy.py
git commit -m "$(cat <<'EOF'
feat(xvenue): wire XVenuePolymarketStrategy.scan_and_execute with 9 gates

Order: quote freshness → volume floor → mapping resolves → deviation →
EV → depth → DB dedup (via OrderManager) → cross-strategy exposure →
recently-fired → fire. Mapping hot-reloaded each scan for zero-downtime
JSON edits. Direct/reversed outcome_match applied before edge math.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: polymarket_quote_cron.py

**Files:**
- Create: `scripts/polymarket_quote_cron.py`

- [ ] **Step 11.1: Write the script**

```python
# scripts/polymarket_quote_cron.py
"""Polymarket quote refresh cron — runs every 1 minute.

Reads mapping JSON, fetches all mapped slugs via gamma-api, upserts into
polymarket_quotes. Designed to run as a separate process (launchd/systemd)
so scrape failures don't impact the main bot loop.

Usage:
  python scripts/polymarket_quote_cron.py --once
  python scripts/polymarket_quote_cron.py        # loop every 60s
"""
import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import Config
from core.database import Database
from core.polymarket_mapping import PolymarketKalshiMapping
from core.polymarket_scraper import PolymarketScraper

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("polymarket_cron")

MAPPING_PATH = "data/polymarket_kalshi_mapping.json"


async def refresh(db: Database, scraper: PolymarketScraper,
                  mapping: PolymarketKalshiMapping) -> int:
    """Reload mapping, fetch all slugs, upsert quotes. Returns # of updates."""
    mapping.reload()
    slugs = mapping.all_slugs()
    if not slugs:
        log.warning("No slugs in mapping JSON")
        return 0
    # gamma-api can handle ~50 slugs per batch comfortably
    BATCH = 50
    updated = 0
    for i in range(0, len(slugs), BATCH):
        chunk = slugs[i:i + BATCH]
        out = scraper.fetch_batch(chunk)
        for slug, parsed in out.items():
            await db.record_polymarket_quote(
                slug=slug,
                yes_price=parsed["yes_price"],
                no_price=parsed["no_price"],
                volume_24h=parsed.get("volume_24h"),
            )
            updated += 1
        missing = set(chunk) - set(out.keys())
        if missing:
            log.warning(f"Slugs not returned/parsed: {sorted(missing)[:5]}"
                        f"{'...' if len(missing) > 5 else ''}")
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    scraper = PolymarketScraper()
    mapping = PolymarketKalshiMapping(MAPPING_PATH)

    interval = 60.0  # 1 minute

    while True:
        t0 = time.time()
        try:
            n = await refresh(db, scraper, mapping)
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — updated={n}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(5.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 11.2: Smoke test --once**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python scripts/polymarket_quote_cron.py --once
```
Expected: 2-30s runtime; logs `refresh complete ... updated=N` where N is the number of slugs Polymarket actually returned valid quotes for. May be 0 if all v0 seed slugs are stale/wrong — that's acceptable for v0, mapping will iterate over time.

Verify DB:
```
sqlite3 data/bot.db "SELECT slug, yes_price, volume_24h FROM polymarket_quotes;"
```

- [ ] **Step 11.3: Commit**

```bash
git add scripts/polymarket_quote_cron.py
git commit -m "$(cat <<'EOF'
feat(xvenue): add 1-min Polymarket quote refresh cron

Standalone async script — reads mapping JSON, batch-fetches gamma-api,
upserts quotes. Designed as separate process so scrape failures don't
impact main bot loop. Mapping hot-reloaded each iteration so manual edits
take effect within 60s.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: research/xvenue_signal_scanner.py — Phase 0 dry-run

**Files:**
- Create: `scripts/research/xvenue_signal_scanner.py`

- [ ] **Step 12.1: Write the script**

```python
# scripts/research/xvenue_signal_scanner.py
"""Phase 0 prevalence scanner for B1 xvenue_polymarket.

Mirrors strategies/xvenue_polymarket.py signal logic but does NOT fire any
trade. Logs every (slug × ticker) evaluation to cross_venue_signals for
offline analysis. Run for ≥1 week before paper deployment to validate
mapping accuracy + fire rate + EV distribution.

Usage:
  python scripts/research/xvenue_signal_scanner.py --once
  python scripts/research/xvenue_signal_scanner.py --interval 60
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.polymarket_mapping import PolymarketKalshiMapping
from strategies.xvenue_polymarket import XVenuePolymarketStrategy

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("xvenue_scanner")

MAPPING_PATH = "data/polymarket_kalshi_mapping.json"


def fetch_open_markets_for_prefix(client: KalshiClient, prefix: str) -> list[dict]:
    """List open markets whose ticker startswith prefix.

    Iterates open markets paginated. Scanner is research-only so a single
    full-universe pass per iteration is acceptable cost.
    """
    out = []
    cursor = None
    pages = 0
    while pages < 20:
        path = f"/trade-api/v2/markets?status=open&limit=200"
        if cursor:
            path += f"&cursor={cursor}"
        d = client._get(path)
        if not d:
            break
        for m in d.get("markets", []):
            if m.get("ticker", "").startswith(prefix):
                out.append(m)
        cursor = d.get("cursor") or None
        pages += 1
        if not cursor:
            break
    return out


async def scan_once(client: KalshiClient, db: Database, cfg: Config,
                    mapping: PolymarketKalshiMapping) -> dict:
    summary = defaultdict(int)
    strategy = XVenuePolymarketStrategy(cfg, None, None, client, mapping=mapping)
    now = datetime.now(timezone.utc)

    for slug in mapping.all_slugs():
        quote_row = await db.fetch_polymarket_quote(slug)
        if quote_row is None:
            summary[f"{slug}/no_quote"] += 1
            continue
        if not strategy._is_quote_fresh(quote_row, now=now):
            summary[f"{slug}/stale_quote"] += 1
            continue
        if not strategy._is_quote_volume_ok(quote_row):
            summary[f"{slug}/low_volume"] += 1
            continue
        poly_yes_raw = float(quote_row["yes_price"])

        for entry in mapping.entries_for_slug(slug):
            prefix = entry["kalshi_ticker_prefix"]
            outcome_match = entry["outcome_match"]
            poly_yes = strategy._apply_outcome_match(poly_yes_raw, outcome_match)

            markets = fetch_open_markets_for_prefix(client, prefix)
            if not markets:
                summary[f"{slug}/no_kalshi_match"] += 1
                continue

            for m in markets:
                ticker = m.get("ticker", "")
                yes_price, no_price = client.get_market_prices(ticker)
                if yes_price is None or no_price is None:
                    continue
                yes_ask, no_ask = yes_price, no_price
                yes_bid = max(0.0, 1.0 - no_ask)
                no_bid = max(0.0, 1.0 - yes_ask)
                kalshi_yes_mid = (yes_ask + yes_bid) / 2.0
                deviation = poly_yes - kalshi_yes_mid

                edge = strategy._compute_edge(
                    polymarket_yes=poly_yes,
                    kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
                    kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
                )
                if edge is None:
                    action = "skip_min_deviation_or_ev"
                    side = None
                    size = None
                else:
                    action = "would_fire"
                    side = edge["side"]
                    size = strategy._size_for_edge(edge["deviation"])

                await db.record_cross_venue_signal(
                    ticker=ticker, slug=slug,
                    kalshi_implied=kalshi_yes_mid,
                    polymarket_implied=poly_yes,
                    deviation=deviation, side=side,
                    expected_size_usdc=size, action_taken=action,
                )
                summary[f"{slug}/{action}"] += 1
    return dict(summary)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between scans")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(cfg)
    mapping = PolymarketKalshiMapping(MAPPING_PATH)

    log.info(f"Starting xvenue_polymarket scanner — interval={args.interval}s")
    while True:
        t0 = time.time()
        try:
            summary = await scan_once(client, db, cfg, mapping)
            elapsed = time.time() - t0
            log.info(f"scan complete in {elapsed:.1f}s: {summary}")
        except Exception as e:
            log.exception(f"scan error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(5.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 12.2: Smoke test --once**

```
python scripts/research/xvenue_signal_scanner.py --once
```
Expected: 5-60 sec runtime; logs scan summary. May report `no_quote` for most slugs if Task 11 cron hasn't run yet — acceptable for smoke.

- [ ] **Step 12.3: Commit**

```bash
git add scripts/research/xvenue_signal_scanner.py
git commit -m "$(cat <<'EOF'
feat(xvenue): Phase 0 prevalence scanner (no-fire signal logger)

Mirrors xvenue_polymarket signal logic but writes to cross_venue_signals
without firing. action_taken='would_fire' (not 'fired') makes the read-only
nature explicit when querying later. 1-week run feeds Phase 0 gate:
≥3 signals/day, EV ≥+$10/week, mapping accuracy ≥90%.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: main.py registration

**Files:**
- Modify: `main.py`

- [ ] **Step 13.1: Pre-edit smoke check**

```
python -c "import main; print('OK')"
```
Expected: `OK`.

- [ ] **Step 13.2: Add import + instance**

In `main.py`, add to strategy imports (near other xvenue/macro/crypto imports added earlier):
```python
from strategies.xvenue_polymarket import XVenuePolymarketStrategy
from core.polymarket_mapping import PolymarketKalshiMapping
```

In `main.py` `__init__`, after the existing `self.crypto_fair_value = ...` instance creation (added by B2), append:
```python
        # Cross-Venue Polymarket Trader (B1) — paper-only at launch.
        # Reads gamma-api via 1-min cron. Spec:
        # docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
        self.xvenue_mapping = PolymarketKalshiMapping(
            "data/polymarket_kalshi_mapping.json"
        )
        self.xvenue_polymarket = XVenuePolymarketStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            mapping=self.xvenue_mapping,
        )
```

In `_strategy_loop` strategies list, append after the last ladder/macro/crypto tuple:
```python
            # Cross-Venue Polymarket Trader (B1) — paper-only. Requires
            # scripts/polymarket_quote_cron.py running separately.
            ("xvenue_polymarket", self.xvenue_polymarket),
```

- [ ] **Step 13.3: Smoke check after wiring**

```
python -c "import main; print('OK')"
```
Expected: `OK`.

- [ ] **Step 13.4: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): register xvenue_polymarket sub-strategy (paper-only)

B1 cross-venue trader wired into _strategy_loop. Runs in main bot's 60s
cadence (no separate fast-scan task). No entry in LIVE_STRATEGIES env →
paper-only at launch. Requires scripts/polymarket_quote_cron.py running
separately to populate polymarket_quotes table; without it, strategy
silently skips every market with 'no quote' signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

After completing all 13 tasks, perform the following checks:

**1. Spec coverage:**
- [ ] core/polymarket_scraper.py: gamma-api REST (Task 3) ✓
- [ ] core/polymarket_mapping.py: JSON loader + hot-reload (Task 4) ✓
- [ ] core/database.py: 2 tables (polymarket_quotes, cross_venue_signals) + 4 helpers (Tasks 1, 2) ✓
- [ ] core/order_manager.py: dedup gate prefix → xvenue_* (Task 6) ✓
- [ ] core/config.py: 8 XVENUE_* knobs (Task 5) ✓
- [ ] data/polymarket_kalshi_mapping.json: 20-entry seed (Task 7) ✓
- [ ] strategies/xvenue_polymarket.py: universe + edge + 9-gate orchestration (Tasks 8-10) ✓
- [ ] scripts/polymarket_quote_cron.py (Task 11) ✓
- [ ] scripts/research/xvenue_signal_scanner.py (Task 12) ✓
- [ ] main.py registration (Task 13) ✓
- [ ] Spec mentions Telegram alerts + dashboard panels — NOT covered. Defer to operational follow-up (A1/C1/B2 same pattern).
- [ ] Spec mentions promotion_eval entry — NOT covered. Defer to Phase 1→2 transition (1-line addition).

**2. Placeholder scan:** No "TBD" / "implement later" — every step has actual test + impl code.

**3. Type consistency:**
- `PolymarketScraper.fetch_single(slug) -> dict | None` — consistent across Tasks 3 and 11 ✓
- `PolymarketScraper.fetch_batch(slugs) -> dict[slug, dict]` — consistent across Tasks 3 and 11 ✓
- `PolymarketKalshiMapping.entries_for_slug(slug) -> list[dict]` — consistent across Tasks 4, 10, 12 ✓
- `PolymarketKalshiMapping.all_slugs() -> list[str]` — consistent across Tasks 4, 11, 12 ✓
- `Database.record_polymarket_quote(slug, yes_price, no_price, volume_24h)` — consistent across Tasks 1, 11 ✓
- `Database.fetch_polymarket_quote(slug)` — consistent across Tasks 1, 10 ✓
- `Database.record_cross_venue_signal(...)` keyword args match across Tasks 2 and 12 ✓
- `XVenuePolymarketStrategy.__init__(config, order_manager, market_scanner, kalshi_client, *, mapping)` — `mapping` keyword-only across Tasks 8, 9, 10, 13 ✓
- `STRATEGY_NAME = "xvenue_polymarket"` matches dedup-gate prefix `xvenue_*` (Task 6) ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-xvenue-polymarket-trader-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
