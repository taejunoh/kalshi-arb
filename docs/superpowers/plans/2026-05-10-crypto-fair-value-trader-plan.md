# Crypto Fair-Value Trader (B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single sub-strategy `crypto_fair_value` that fires directional trades on KXBTC/KXETH/KXXRP/KXBTCD/KXETHD when Kalshi implied prob deviates from a lognormal fair-value model (using Binance WS spot via existing PriceFeed + Deribit IV for BTC/ETH or PriceFeed realized vol for XRP) by ≥ MIN_DEVIATION (default 0.10 = fee 7c + 3c cushion). Event-driven 1-sec fast-scan task with 9-stage risk pipeline including 60-sec recently-fired guard and cross-strategy exposure cap.

**Architecture:** Stateless `core/fair_value_math.py` (lognormal CDF using existing `core/macro_consensus.py:normal_cdf`) + stateless `core/deribit_iv.py` (scrapes Deribit options chain) + stateful `core/kalshi_orderbook_cache.py` (5-sec TTL in-memory cache). `strategies/crypto_fair_value.py` is a single class running its own asyncio task (separate from `_strategy_loop` because of 1-sec cadence). 4-hour cron refreshes Deribit IV; Phase 0 dry-run scanner mirrors signal logic without firing.

**Tech Stack:** Python 3 + httpx + math.erf (no scipy) + websockets (already in PriceFeed) + aiosqlite + pytest. Codebase patterns: `strategies/macro_event.py` (just merged in PR #2 — single-class shape, 9-gate orchestration), `strategies/ladder_crypto.py` (KXBTC ATM filter, just patched for T-format), `core/price_feed.py` (Binance WS BTC/ETH/SOL — extend with XRP).

**Spec:** `docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md` (commit d5429ed).

---

## File Structure

**Create:**
- `core/fair_value_math.py` — `lognormal_cdf_above(spot, vol, strike, tau_years)` (~50 LOC)
- `core/deribit_iv.py` — `DeribitIVScraper` + `extract_atm_iv` (~150 LOC)
- `core/kalshi_orderbook_cache.py` — `OrderbookCache` with TTL + refresh loop (~120 LOC)
- `strategies/crypto_fair_value.py` — `CryptoFairValueStrategy` (~250 LOC)
- `scripts/deribit_iv_cron.py` — 4h refresh entrypoint (~80 LOC)
- `scripts/research/crypto_fair_value_signal_scanner.py` — Phase 0 dry-run (~150 LOC)
- `tests/test_fair_value_math.py` — golden lognormal values (~80 LOC)
- `tests/test_deribit_iv.py` — mocked HTTP scraper (~150 LOC)
- `tests/test_orderbook_cache.py` — TTL behavior (~100 LOC)
- `tests/test_crypto_iv_db.py` — DB round-trip (~70 LOC)
- `tests/test_crypto_fair_value_strategy.py` — strategy unit + integration (~250 LOC)
- `tests/test_crypto_fair_value_dedup.py` — dedup gate prefix integration (~80 LOC)

**Modify:**
- `core/database.py` — 2 new tables (`crypto_iv`, `crypto_fair_value_signals`) + 4 helpers
- `core/order_manager.py` — extend dedup gate prefix to also accept `crypto_*` (5 sites + canonical comment)
- `core/config.py` — 8 new `CRYPTO_FAIR_*` env knobs + add `XRP` to `BINANCE_WS_URLS`
- `main.py` — import + instantiate `CryptoFairValueStrategy` + add its task(s) to `_bot_tasks`

---

## Task 1: fair_value_math.py — lognormal_cdf_above

**Files:**
- Create: `core/fair_value_math.py`
- Create: `tests/test_fair_value_math.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_fair_value_math.py
import math
import pytest

from core.fair_value_math import lognormal_cdf_above


def test_atm_zero_vol_zero_tau_returns_neutral():
    """Degenerate (sigma=0, tau=0): S=K → 0.5 (boundary). S>K → 1, S<K → 0."""
    assert lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.0, tau_years=0.0) == 0.5
    assert lognormal_cdf_above(spot=110.0, strike=100.0, vol=0.0, tau_years=0.0) == 1.0
    assert lognormal_cdf_above(spot=90.0, strike=100.0, vol=0.0, tau_years=0.0) == 0.0


def test_atm_3month_20pct_vol():
    """S=100 K=100 sigma=0.20 tau=0.25: standard textbook ≈ 0.4602.

    fair = Phi((ln(1) - 0.5*0.04*0.25) / (0.2*0.5))
         = Phi((-0.005) / 0.1) = Phi(-0.05) ≈ 0.4801
    """
    p = lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert abs(p - 0.4801) < 0.01


def test_itm_call_deeper_than_atm():
    """S > K → fair > 0.5 (under r=0 model)."""
    p = lognormal_cdf_above(spot=110.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert p > 0.5


def test_otm_call_below_atm():
    """S < K → fair < 0.5."""
    p = lognormal_cdf_above(spot=90.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert p < 0.5


def test_negative_tau_returns_none():
    """Past expiry → undefined. Returns None per contract."""
    assert lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.20, tau_years=-0.01) is None


def test_zero_or_negative_strike_returns_none():
    """Strike must be positive."""
    assert lognormal_cdf_above(spot=100.0, strike=0.0, vol=0.20, tau_years=0.25) is None
    assert lognormal_cdf_above(spot=100.0, strike=-50.0, vol=0.20, tau_years=0.25) is None
```

- [ ] **Step 1.2: Run test to verify it fails**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
pytest tests/test_fair_value_math.py -v
```
Expected: FAIL with `ImportError: cannot import name 'lognormal_cdf_above' from 'core.fair_value_math'`.

- [ ] **Step 1.3: Write minimal implementation**

```python
# core/fair_value_math.py
"""Lognormal fair-value math for binary "asset_T >= K" markets.

Reuses core/macro_consensus.py:normal_cdf (no scipy dependency).

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import math
from typing import Optional

from core.macro_consensus import normal_cdf


def lognormal_cdf_above(
    *, spot: float, strike: float, vol: float, tau_years: float,
) -> Optional[float]:
    """P(S_T >= K) under lognormal with drift r=0 (short-horizon crypto).

    fair = Phi((ln(S/K) - 0.5*sigma^2*tau) / (sigma * sqrt(tau)))

    Edge cases:
    - tau_years < 0 or strike <= 0 → None (undefined input)
    - vol == 0 OR tau_years == 0 → deterministic (1.0 if S>K, 0.0 if S<K, 0.5 at boundary)
    """
    if tau_years < 0 or strike <= 0 or spot <= 0:
        return None
    if vol <= 0 or tau_years == 0:
        if spot > strike:
            return 1.0
        if spot < strike:
            return 0.0
        return 0.5
    sigma_sqrt_tau = vol * math.sqrt(tau_years)
    d = (math.log(spot / strike) - 0.5 * vol * vol * tau_years) / sigma_sqrt_tau
    return normal_cdf(d, mu=0.0, sigma=1.0)
```

- [ ] **Step 1.4: Run test to verify it passes**

```
pytest tests/test_fair_value_math.py -v
```
Expected: 6 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add core/fair_value_math.py tests/test_fair_value_math.py
git commit -m "$(cat <<'EOF'
feat(macro): add lognormal_cdf_above for binary fair-value pricing

Reuses core/macro_consensus.py:normal_cdf (no scipy dep). Drift r=0 for
short-horizon crypto markets. Returns None on invalid inputs (negative
tau, non-positive strike/spot) and handles deterministic boundary
(vol=0 or tau=0) cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: crypto_iv DB schema + helpers

**Files:**
- Modify: `core/database.py`
- Create: `tests/test_crypto_iv_db.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_crypto_iv_db.py
import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_and_fetch_nearest_crypto_iv():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_iv("BTC", "2026-05-13", 0.65)
        await db.record_crypto_iv("BTC", "2026-05-20", 0.62)
        await db.record_crypto_iv("ETH", "2026-05-13", 0.75)

        # Nearest expiry for BTC = 2026-05-13 (lexicographic order works for ISO dates)
        row = await db.fetch_nearest_crypto_iv("BTC")
        assert row["asset"] == "BTC"
        assert row["expiry_date"] == "2026-05-13"
        assert row["atm_iv"] == 0.65

        # ETH has only one expiry
        eth = await db.fetch_nearest_crypto_iv("ETH")
        assert eth["expiry_date"] == "2026-05-13"
        assert eth["atm_iv"] == 0.75


@pytest.mark.asyncio
async def test_record_crypto_iv_upserts():
    """Re-record same (asset, expiry) updates atm_iv + fetched_at in place."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_iv("BTC", "2026-05-13", 0.65)
        await db.record_crypto_iv("BTC", "2026-05-13", 0.70)
        row = await db.fetch_nearest_crypto_iv("BTC")
        assert row["atm_iv"] == 0.70


@pytest.mark.asyncio
async def test_fetch_nearest_returns_none_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        assert await db.fetch_nearest_crypto_iv("BTC") is None
```

- [ ] **Step 2.2: Run test to verify it fails**

```
pytest tests/test_crypto_iv_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_crypto_iv'`.

- [ ] **Step 2.3: Add CREATE TABLE + helpers to `core/database.py`**

In `Database.initialize()`, after the `macro_event_signals` block (added by C1), append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_iv (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset TEXT NOT NULL,
                    expiry_date TEXT NOT NULL,
                    atm_iv REAL NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(asset, expiry_date)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_civ_asset_ts "
                "ON crypto_iv(asset, fetched_at)"
            )
```

Add two methods to the `Database` class (place near `record_macro_consensus`):
```python
    async def record_crypto_iv(
        self, asset: str, expiry_date: str, atm_iv: float,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO crypto_iv (asset, expiry_date, atm_iv, fetched_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(asset, expiry_date) DO UPDATE SET "
                "atm_iv = excluded.atm_iv, fetched_at = excluded.fetched_at",
                (asset, expiry_date, atm_iv, ts),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_nearest_crypto_iv(self, asset: str) -> dict | None:
        """Returns the row with the earliest non-past expiry for `asset`.

        For v0 we just pick the lexicographically smallest expiry_date
        (ISO yyyy-mm-dd sorts correctly). Caller is responsible for
        filtering against current date if past-expiry rows exist.
        """
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM crypto_iv WHERE asset = ? "
                "ORDER BY expiry_date ASC LIMIT 1",
                (asset,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 2.4: Run test to verify it passes**

```
pytest tests/test_crypto_iv_db.py -v
```
Expected: 3 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add core/database.py tests/test_crypto_iv_db.py
git commit -m "$(cat <<'EOF'
feat(db): add crypto_iv table + record/fetch_nearest helpers

Persistence for B2 crypto fair-value trader — caches Deribit ATM IV per
(asset, expiry_date). UNIQUE constraint + ON CONFLICT upsert pattern for
idempotent 4h cron. fetch_nearest_crypto_iv returns earliest expiry
(v0 uses single nearest IV, multi-expiry interpolation deferred).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: DeribitIVScraper

**Files:**
- Create: `core/deribit_iv.py`
- Create: `tests/test_deribit_iv.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_deribit_iv.py
from unittest.mock import patch, MagicMock

import pytest

from core.deribit_iv import DeribitIVScraper, parse_instrument_name, extract_atm_iv


def test_parse_instrument_name():
    assert parse_instrument_name("BTC-13MAY26-93000-C") == ("BTC", "2026-05-13", 93000.0, "C")
    assert parse_instrument_name("ETH-20MAY26-3500-P") == ("ETH", "2026-05-20", 3500.0, "P")
    assert parse_instrument_name("BTC-INVALID") is None
    assert parse_instrument_name("") is None


def test_extract_atm_iv_picks_closest_strike():
    """ATM IV = average of (closest call mark_iv, closest put mark_iv) per expiry."""
    chain = [
        {"instrument_name": "BTC-13MAY26-92000-C", "mark_iv": 0.62},
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},  # closest call
        {"instrument_name": "BTC-13MAY26-94000-C", "mark_iv": 0.61},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.64},  # closest put
        {"instrument_name": "BTC-13MAY26-94000-P", "mark_iv": 0.66},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=92800.0)
    # closest strike to 92800 is 93000. atm_iv = (0.60 + 0.64) / 2 = 0.62
    assert "2026-05-13" in out
    assert abs(out["2026-05-13"] - 0.62) < 1e-6


def test_extract_atm_iv_handles_multiple_expiries():
    chain = [
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
        {"instrument_name": "BTC-20MAY26-93000-C", "mark_iv": 0.55},
        {"instrument_name": "BTC-20MAY26-93000-P", "mark_iv": 0.57},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert abs(out["2026-05-13"] - 0.61) < 1e-6
    assert abs(out["2026-05-20"] - 0.56) < 1e-6


def test_extract_atm_iv_skips_other_assets():
    chain = [
        {"instrument_name": "ETH-13MAY26-3500-C", "mark_iv": 0.75},
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert "2026-05-13" in out
    assert len(out) == 1


def test_extract_atm_iv_skips_expiry_with_only_one_side():
    """If an expiry has calls but no puts (or vice versa), skip it."""
    chain = [
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        # No put for this expiry
        {"instrument_name": "BTC-20MAY26-93000-C", "mark_iv": 0.55},
        {"instrument_name": "BTC-20MAY26-93000-P", "mark_iv": 0.57},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert "2026-05-13" not in out
    assert "2026-05-20" in out


def test_scraper_returns_chain_on_http_200():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "result": [
            {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
            {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
        ]
    }
    with patch("core.deribit_iv.httpx.get", return_value=fake_response):
        scraper = DeribitIVScraper()
        chain = scraper.fetch_chain("BTC")
        assert chain is not None
        assert len(chain) == 2


def test_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.deribit_iv.httpx.get", return_value=fake_response):
        scraper = DeribitIVScraper()
        assert scraper.fetch_chain("BTC") is None
```

- [ ] **Step 3.2: Run test to verify it fails**

```
pytest tests/test_deribit_iv.py -v
```
Expected: FAIL with `ImportError: cannot import name 'DeribitIVScraper'`.

- [ ] **Step 3.3: Write minimal implementation**

```python
# core/deribit_iv.py
"""Deribit options chain scraper — ATM IV per expiry for BTC + ETH.

Public REST endpoint, no auth required. v0 returns nearest-expiry single
IV; multi-expiry interpolation deferred to Phase 2.

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger("deribit_iv")

DERIBIT_URL = (
    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    "?currency={asset}&kind=option"
)

# BTC-13MAY26-93000-C
INSTRUMENT_RE = re.compile(
    r"^([A-Z]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$"
)

MONTH_TO_NUM = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def parse_instrument_name(name: str) -> Optional[tuple[str, str, float, str]]:
    """'BTC-13MAY26-93000-C' → ('BTC', '2026-05-13', 93000.0, 'C').

    Returns None on parse failure.
    """
    m = INSTRUMENT_RE.match(name or "")
    if not m:
        return None
    asset, day, month_abbr, year, strike, kind = m.groups()
    month_num = MONTH_TO_NUM.get(month_abbr)
    if not month_num:
        return None
    iso_date = f"20{year}-{month_num}-{day.zfill(2)}"
    try:
        strike_f = float(strike)
    except ValueError:
        return None
    return asset, iso_date, strike_f, kind


def extract_atm_iv(
    chain: list[dict], asset: str, current_spot: float,
) -> dict[str, float]:
    """For each expiry in `chain` (filtered to `asset`), find the ATM call+put
    pair and return atm_iv = (call.mark_iv + put.mark_iv) / 2.

    Skips any expiry that doesn't have BOTH a call and a put.
    """
    # Group by (expiry, kind) → list of (strike, mark_iv)
    groups: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for item in chain or []:
        parsed = parse_instrument_name(item.get("instrument_name", ""))
        if not parsed:
            continue
        a, expiry, strike, kind = parsed
        if a != asset:
            continue
        iv = item.get("mark_iv")
        if iv is None:
            continue
        try:
            iv_f = float(iv)
        except (TypeError, ValueError):
            continue
        groups[(expiry, kind)].append((strike, iv_f))

    # For each expiry, need both C and P; pick closest strike to spot for each
    out: dict[str, float] = {}
    expiries = {e for (e, _k) in groups.keys()}
    for expiry in expiries:
        calls = groups.get((expiry, "C"))
        puts = groups.get((expiry, "P"))
        if not calls or not puts:
            continue
        closest_call = min(calls, key=lambda t: abs(t[0] - current_spot))
        closest_put = min(puts, key=lambda t: abs(t[0] - current_spot))
        out[expiry] = (closest_call[1] + closest_put[1]) / 2.0
    return out


class DeribitIVScraper:
    """Fetches Deribit option chain for a single currency. Returns None on error."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_chain(self, asset: str) -> Optional[list[dict]]:
        url = DERIBIT_URL.format(asset=asset)
        try:
            response = httpx.get(url, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"Deribit fetch failed for {asset}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Deribit HTTP {response.status_code} for {asset}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Deribit JSON parse failed for {asset}: {e}")
            return None
        result = data.get("result")
        if not isinstance(result, list):
            return None
        return result
```

- [ ] **Step 3.4: Run test to verify it passes**

```
pytest tests/test_deribit_iv.py -v
```
Expected: 7 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/deribit_iv.py tests/test_deribit_iv.py
git commit -m "$(cat <<'EOF'
feat(crypto): add DeribitIVScraper + ATM IV extraction

Public Deribit REST endpoint (no auth). parse_instrument_name handles
the 'BTC-13MAY26-93000-C' format, extract_atm_iv pairs ATM call+put per
expiry and averages their mark_iv. v0 returns multi-expiry dict; caller
selects nearest expiry per spec section "v0 simplification".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: crypto_fair_value_signals DB schema + helpers

**Files:**
- Modify: `core/database.py`
- Modify: `tests/test_crypto_iv_db.py`

- [ ] **Step 4.1: Append failing tests**

Append to `tests/test_crypto_iv_db.py`:
```python
@pytest.mark.asyncio
async def test_record_crypto_fair_value_signal_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_fair_value_signal(
            ticker="KXBTC-26MAY1017-T89749.99", asset="BTC",
            spot=90000.0, strike=89749.99,
            vol=0.65, vol_source="deribit",
            time_to_close_secs=3600,
            fair_prob=0.55, kalshi_implied=0.40, deviation=0.15,
            side="yes", expected_size_usdc=30.0, action_taken="fired",
        )
        rows = await db.fetch_recent_crypto_fair_value_signals(limit=5)
        assert len(rows) == 1
        r = rows[0]
        assert r["asset"] == "BTC"
        assert r["vol_source"] == "deribit"
        assert r["deviation"] == 0.15
        assert r["action_taken"] == "fired"


@pytest.mark.asyncio
async def test_record_crypto_fair_value_signal_with_skip_action():
    """Skipped signals (e.g., skip_recently_fired) record action without side."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_fair_value_signal(
            ticker="KXBTC-26MAY1017-T89749.99", asset="BTC",
            spot=90000.0, strike=89749.99,
            vol=0.65, vol_source="deribit",
            time_to_close_secs=3600,
            fair_prob=0.55, kalshi_implied=0.50, deviation=0.05,
            side=None, expected_size_usdc=None, action_taken="skip_min_deviation",
        )
        rows = await db.fetch_recent_crypto_fair_value_signals(limit=5)
        assert rows[0]["action_taken"] == "skip_min_deviation"
        assert rows[0]["side"] is None
```

- [ ] **Step 4.2: Run test to verify it fails**

```
pytest tests/test_crypto_iv_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_crypto_fair_value_signal'`.

- [ ] **Step 4.3: Add CREATE TABLE + helpers to `core/database.py`**

In `Database.initialize()`, after the `crypto_iv` block from Task 2, append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_fair_value_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    spot REAL NOT NULL,
                    strike REAL NOT NULL,
                    vol REAL NOT NULL,
                    vol_source TEXT NOT NULL,
                    time_to_close_secs INTEGER NOT NULL,
                    fair_prob REAL NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cfvs_ticker_ts "
                "ON crypto_fair_value_signals(ticker, ts)"
            )
```

Add two methods to `Database`:
```python
    async def record_crypto_fair_value_signal(
        self, *, ticker: str, asset: str, spot: float, strike: float,
        vol: float, vol_source: str, time_to_close_secs: int,
        fair_prob: float, kalshi_implied: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO crypto_fair_value_signals "
                "(ts, ticker, asset, spot, strike, vol, vol_source, "
                " time_to_close_secs, fair_prob, kalshi_implied, deviation, "
                " side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, asset, spot, strike, vol, vol_source,
                 time_to_close_secs, fair_prob, kalshi_implied, deviation,
                 side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_crypto_fair_value_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM crypto_fair_value_signals "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
```

- [ ] **Step 4.4: Run test to verify it passes**

```
pytest tests/test_crypto_iv_db.py -v
```
Expected: 5 PASS (3 from Task 2 + 2 new).

- [ ] **Step 4.5: Commit**

```bash
git add core/database.py tests/test_crypto_iv_db.py
git commit -m "$(cat <<'EOF'
feat(db): add crypto_fair_value_signals table + helpers

Logging table for both Phase 0 dry-run scanner and live strategy. Records
every signal with action_taken (fired | skip_*) for offline analysis.
Indexed on (ticker, ts) for fast per-ticker queries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: CRYPTO_FAIR_* config knobs + XRP WS URL

**Files:**
- Modify: `core/config.py`
- Create: `tests/test_crypto_fair_value_config.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_crypto_fair_value_config.py
import os
from unittest.mock import patch

from core.config import Config


CRYPTO_KEYS = (
    "CRYPTO_FAIR_MIN_DEVIATION",
    "CRYPTO_FAIR_MIN_DEPTH_USDC",
    "CRYPTO_FAIR_BASE_SIZE_USDC",
    "CRYPTO_FAIR_MAX_POSITION_PER_TICKER",
    "CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE",
    "CRYPTO_FAIR_RECENTLY_FIRED_SECS",
    "CRYPTO_FAIR_ATM_WINDOW_N",
    "CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS",
)


def test_crypto_fair_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in CRYPTO_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.CRYPTO_FAIR_MIN_DEVIATION == 0.10
        assert cfg.CRYPTO_FAIR_MIN_DEPTH_USDC == 30.0
        assert cfg.CRYPTO_FAIR_BASE_SIZE_USDC == 20.0
        assert cfg.CRYPTO_FAIR_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.CRYPTO_FAIR_RECENTLY_FIRED_SECS == 60.0
        assert cfg.CRYPTO_FAIR_ATM_WINDOW_N == 5
        assert cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS == 60.0


def test_xrp_in_binance_ws_urls():
    """B2 spec adds XRP to PriceFeed subscription list."""
    cfg = Config()
    assert "XRP" in cfg.BINANCE_WS_URLS
    assert "xrpusdt" in cfg.BINANCE_WS_URLS["XRP"].lower()
```

- [ ] **Step 5.2: Run test to verify it fails**

```
pytest tests/test_crypto_fair_value_config.py -v
```
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'CRYPTO_FAIR_MIN_DEVIATION'`.

- [ ] **Step 5.3: Add config attributes + XRP to WS URLs**

Append to `Config.__init__` body in `core/config.py` (after the MACRO_* block from C1):
```python
        # Crypto Fair-Value Trader (B2) — single sub-strategy crypto_fair_value.
        # Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
        self.CRYPTO_FAIR_MIN_DEVIATION = float(os.getenv("CRYPTO_FAIR_MIN_DEVIATION", "0.10"))
        self.CRYPTO_FAIR_MIN_DEPTH_USDC = float(os.getenv("CRYPTO_FAIR_MIN_DEPTH_USDC", "30"))
        self.CRYPTO_FAIR_BASE_SIZE_USDC = float(os.getenv("CRYPTO_FAIR_BASE_SIZE_USDC", "20"))
        self.CRYPTO_FAIR_MAX_POSITION_PER_TICKER = float(os.getenv("CRYPTO_FAIR_MAX_POSITION_PER_TICKER", "30"))
        self.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.CRYPTO_FAIR_RECENTLY_FIRED_SECS = float(os.getenv("CRYPTO_FAIR_RECENTLY_FIRED_SECS", "60"))
        self.CRYPTO_FAIR_ATM_WINDOW_N = int(os.getenv("CRYPTO_FAIR_ATM_WINDOW_N", "5"))
        self.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS = float(os.getenv("CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS", "60"))
```

In `core/config.py`, locate the existing `self.BINANCE_WS_URLS = {...}` block (currently has BTC/ETH/SOL). Add `XRP` entry:
```python
        self.BINANCE_WS_URLS = {
            "BTC": f"{binance_base}/ws/btcusdt@aggTrade",
            "ETH": f"{binance_base}/ws/ethusdt@aggTrade",
            "SOL": f"{binance_base}/ws/solusdt@aggTrade",
            "XRP": f"{binance_base}/ws/xrpusdt@aggTrade",
        }
```

- [ ] **Step 5.4: Run test to verify it passes**

```
pytest tests/test_crypto_fair_value_config.py -v
```
Expected: 2 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add core/config.py tests/test_crypto_fair_value_config.py
git commit -m "$(cat <<'EOF'
feat(config): add CRYPTO_FAIR_* env knobs (8) + XRP to BINANCE_WS_URLS

Per B2 spec: MIN_DEVIATION=0.10 (fee 0.07 + 3c cushion), depth $30, base
size $20, per-ticker cap $30, cross-strategy cap $80, recently-fired 60s,
ATM ±5 strikes, min time-to-close 60s. XRP added to Binance WS URLs so
existing PriceFeed will subscribe automatically.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Extend has_existing_trade dedup gate to crypto_*

**Files:**
- Modify: `core/order_manager.py` (5 sites + comment)
- Create: `tests/test_crypto_fair_value_dedup.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_crypto_fair_value_dedup.py
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_crypto_fair_value_blocked_when_paper_filled_exists():
    """crypto_fair_value re-entry on existing paper_filled trade must be skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="crypto_fair_value",
            market_id="KXBTC-26MAY1017-T89749.99",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_filled",
        )

        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0

        result = await om._paper_market_order(
            ticker="KXBTC-26MAY1017-T89749.99", side="yes",
            size_usdc=20.0, price=0.30,
            strategy="crypto_fair_value",
            market_id="KXBTC-26MAY1017-T89749.99",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
```

- [ ] **Step 6.2: Run test to verify it fails**

```
pytest tests/test_crypto_fair_value_dedup.py -v
```
Expected: FAIL — current gate is `(sports_*, ladder_*, macro_*)`, crypto_* skips dedup.

- [ ] **Step 6.3: Extend dedup gate at 5 sites**

Find the 5 sites:
```bash
grep -c 'startswith("sports_") or strategy.startswith("ladder_") or' core/order_manager.py
```
Expected: 5.

For each of the 5 sites, change:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
        strategy.startswith("macro_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```
to:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
        strategy.startswith("macro_") or strategy.startswith("crypto_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```

Verify all 5 updated:
```
grep -c 'strategy.startswith("crypto_")' core/order_manager.py
```
Expected: 5.

Update the canonical comment at `_paper_market_order` to add `crypto_*`:
```python
        # DB-side dedup applies to:
        #   sports_* — single game has one outcome, re-entry is always a bug.
        #   ladder_* — single Kalshi event ladder leg has one outcome; V1
        #              shutdown_fade over-fired 13× on KXNUMSHUTDOWNS by
        #              skipping this guard. See known_bugs.md.
        #   macro_*  — single (FOMC bucket | CPI bucket) outcome — same
        #              one-shot invariant. C1 macro_event sub-strategy.
        #   crypto_* — single (crypto strike at close) outcome — same
        #              one-shot invariant. B2 crypto_fair_value sub-strategy.
        # Other strategies (mentions, market_maker, spread_scanner)
        # legitimately re-enter the same ticker.
```

- [ ] **Step 6.4: Run tests + regression check**

```
pytest tests/test_crypto_fair_value_dedup.py tests/test_macro_event_dedup.py tests/test_ladder_dedup_guard.py -v
```
Expected: All PASS (3 dedup tests).

- [ ] **Step 6.5: Commit**

```bash
git add core/order_manager.py tests/test_crypto_fair_value_dedup.py
git commit -m "$(cat <<'EOF'
fix(order_manager): extend has_existing_trade dedup gate to crypto_* prefix

B2 crypto_fair_value sub-strategy follows the same one-shot-per-(strategy,
market_id) invariant as sports_*, ladder_*, and macro_*. Each (asset
strike at close) market has one outcome. Extends the gate at 5 sites +
updates the canonical policy comment.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: KalshiOrderbookCache

**Files:**
- Create: `core/kalshi_orderbook_cache.py`
- Create: `tests/test_orderbook_cache.py`

- [ ] **Step 7.1: Write the failing test**

```python
# tests/test_orderbook_cache.py
import asyncio
import time
from unittest.mock import MagicMock

import pytest

from core.kalshi_orderbook_cache import OrderbookCache, OrderbookEntry


def test_get_returns_none_when_unknown_ticker():
    cache = OrderbookCache(client=MagicMock())
    assert cache.get("KXBTC-X-Y") is None


def test_get_returns_entry_when_fresh():
    cache = OrderbookCache(client=MagicMock())
    entry = OrderbookEntry(
        yes_ask=0.30, yes_bid=0.28, no_ask=0.72, no_bid=0.70,
        yes_depth=100.0, no_depth=100.0, ts=time.time(),
    )
    cache._cache["KXBTC-X-Y"] = entry
    out = cache.get("KXBTC-X-Y")
    assert out is entry


def test_get_returns_none_when_stale():
    cache = OrderbookCache(client=MagicMock(), stale_threshold_secs=10.0)
    entry = OrderbookEntry(
        yes_ask=0.30, yes_bid=0.28, no_ask=0.72, no_bid=0.70,
        yes_depth=100.0, no_depth=100.0, ts=time.time() - 30.0,  # 30s old
    )
    cache._cache["KXBTC-X-Y"] = entry
    assert cache.get("KXBTC-X-Y") is None


@pytest.mark.asyncio
async def test_refresh_loop_populates_cache():
    """One iteration of refresh_loop fetches each ticker and populates cache."""
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.30, 0.72))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    cache = OrderbookCache(client=client, refresh_interval_secs=0.05)
    tickers = ["KXBTC-A", "KXBTC-B"]
    cache.set_target_tickers(tickers)

    # Run refresh_loop briefly then cancel
    task = asyncio.create_task(cache.refresh_loop())
    await asyncio.sleep(0.1)  # let it run one iteration
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    a = cache.get("KXBTC-A")
    b = cache.get("KXBTC-B")
    assert a is not None
    assert a.yes_ask == 0.30
    assert b is not None
```

- [ ] **Step 7.2: Run test to verify it fails**

```
pytest tests/test_orderbook_cache.py -v
```
Expected: FAIL with `ImportError: cannot import name 'OrderbookCache'`.

- [ ] **Step 7.3: Write minimal implementation**

```python
# core/kalshi_orderbook_cache.py
"""In-memory Kalshi orderbook cache with TTL + async refresh loop.

Used by B2 crypto_fair_value strategy's 1-sec fast-scan to avoid
hitting Kalshi REST on every tick. Caller (strategy) calls
set_target_tickers() with the dynamic ATM ±N list; refresh_loop
polls each one every refresh_interval_secs.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("orderbook_cache")


@dataclass
class OrderbookEntry:
    yes_ask: float
    yes_bid: float
    no_ask: float
    no_bid: float
    yes_depth: float
    no_depth: float
    ts: float  # epoch seconds


class OrderbookCache:
    """Stateful: holds latest orderbook snapshot per ticker. Use get(ticker)
    to retrieve; returns None if stale (> stale_threshold_secs) or missing.
    """

    def __init__(
        self,
        client,
        refresh_interval_secs: float = 5.0,
        stale_threshold_secs: float = 10.0,
    ):
        self._client = client
        self._refresh_interval = refresh_interval_secs
        self._stale_threshold = stale_threshold_secs
        self._cache: dict[str, OrderbookEntry] = {}
        self._target_tickers: list[str] = []

    def set_target_tickers(self, tickers: list[str]) -> None:
        """Caller updates the watch list (e.g., when ATM shifts)."""
        self._target_tickers = list(tickers)

    def get(self, ticker: str) -> Optional[OrderbookEntry]:
        e = self._cache.get(ticker)
        if e is None:
            return None
        if (time.time() - e.ts) > self._stale_threshold:
            return None
        return e

    async def refresh_loop(self):
        """Background task — poll each target ticker every refresh_interval."""
        while True:
            try:
                tickers = list(self._target_tickers)
                for ticker in tickers:
                    try:
                        prices = self._client.get_market_prices(ticker)
                        if prices is None or prices[0] is None:
                            continue
                        yes_price, no_price = prices
                        depths = self._client.get_order_book_depth(ticker)
                        yes_depth, no_depth = depths if depths else (0.0, 0.0)
                        # Note: KalshiClient.get_market_prices currently returns
                        # ask side only. We collapse bid=ask for v0; spec section
                        # records this as a known limitation.
                        self._cache[ticker] = OrderbookEntry(
                            yes_ask=yes_price, yes_bid=yes_price,
                            no_ask=no_price, no_bid=no_price,
                            yes_depth=yes_depth or 0.0,
                            no_depth=no_depth or 0.0,
                            ts=time.time(),
                        )
                    except Exception as e:
                        logger.warning(f"orderbook fetch error for {ticker}: {e}")
            except Exception as e:
                logger.exception(f"refresh_loop iteration error: {e}")
            await asyncio.sleep(self._refresh_interval)
```

- [ ] **Step 7.4: Run test to verify it passes**

```
pytest tests/test_orderbook_cache.py -v
```
Expected: 4 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add core/kalshi_orderbook_cache.py tests/test_orderbook_cache.py
git commit -m "$(cat <<'EOF'
feat(crypto): add OrderbookCache with TTL + async refresh loop

In-memory cache for Kalshi orderbook snapshots, keyed by ticker. Strategy
sets target_tickers (dynamic ATM ±N list); background refresh_loop polls
each one every 5s. get(ticker) returns None if stale (> 10s) or missing.
Designed for B2's 1-sec fast-scan strategy to avoid hitting Kalshi REST
on every iteration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: CryptoFairValueStrategy — universe + ticker parsing + ATM filter

**Files:**
- Create: `strategies/crypto_fair_value.py`
- Create: `tests/test_crypto_fair_value_strategy.py`

- [ ] **Step 8.1: Write the failing test**

```python
# tests/test_crypto_fair_value_strategy.py
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest

from strategies.crypto_fair_value import CryptoFairValueStrategy


def _cfg():
    return MagicMock(
        CRYPTO_FAIR_MIN_DEVIATION=0.10,
        CRYPTO_FAIR_MIN_DEPTH_USDC=30.0,
        CRYPTO_FAIR_BASE_SIZE_USDC=20.0,
        CRYPTO_FAIR_MAX_POSITION_PER_TICKER=30.0,
        CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        CRYPTO_FAIR_RECENTLY_FIRED_SECS=60.0,
        CRYPTO_FAIR_ATM_WINDOW_N=5,
        CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS=60.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_universe_filter():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._is_target_market({"ticker": "KXBTC-26MAY1017-T89749.99"}) is True
    assert s._is_target_market({"ticker": "KXETH-26MAY1017-T3059.99"}) is True
    assert s._is_target_market({"ticker": "KXXRP-26MAY1017-T1.7799"}) is True
    assert s._is_target_market({"ticker": "KXBTCD-26MAY1017-T89749.99"}) is True
    assert s._is_target_market({"ticker": "KXETHD-26MAY1017-T3059.99"}) is True
    assert s._is_target_market({"ticker": "KXSOL-26MAY1017-T100"}) is False
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is False


def test_extract_strike():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._extract_strike({"ticker": "KXBTC-26MAY1017-T89749.99"}) == 89749.99
    assert s._extract_strike({"ticker": "KXETH-26MAY1017-T3059.99"}) == 3059.99
    assert s._extract_strike({"ticker": "KXBTC-26MAY1017-INVALID"}) is None


def test_extract_asset():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._extract_asset({"ticker": "KXBTC-26MAY1017-T89749.99"}) == "BTC"
    assert s._extract_asset({"ticker": "KXETH-26MAY1017-T3059.99"}) == "ETH"
    assert s._extract_asset({"ticker": "KXXRP-26MAY1017-T1.7799"}) == "XRP"
    assert s._extract_asset({"ticker": "KXBTCD-26MAY1017-T89749.99"}) == "BTC"  # daily
    assert s._extract_asset({"ticker": "KXETHD-26MAY1017-T3059.99"}) == "ETH"


def test_atm_window_filter():
    """Filter ATM ±N strikes based on current spot."""
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    markets = [
        {"ticker": f"KXBTC-26MAY1017-T{strike}"}
        for strike in (89000.0, 89500.0, 89750.0, 90000.0, 90250.0, 90500.0, 91000.0)
    ]
    # Spot = 90000, N=2 → strikes 89500, 89750, 90000, 90250, 90500
    filtered = s._filter_atm(markets, spot=90000.0, asset="BTC", n=2)
    keep = sorted(s._extract_strike(m) for m in filtered)
    assert keep == [89500.0, 89750.0, 90000.0, 90250.0, 90500.0]


def test_time_to_close_secs():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXBTC-26MAY1017-T89749.99",
        "close_time": (now + timedelta(seconds=3600)).isoformat(),
    }
    secs = s._time_to_close_secs(market, now=now)
    assert 3590 <= secs <= 3610  # ±10s tolerance


def test_time_to_close_returns_negative_when_past():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXBTC-X-T1",
        "close_time": (now - timedelta(seconds=3600)).isoformat(),
    }
    assert s._time_to_close_secs(market, now=now) < 0
```

- [ ] **Step 8.2: Run test to verify it fails**

```
pytest tests/test_crypto_fair_value_strategy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.crypto_fair_value'`.

- [ ] **Step 8.3: Write minimal implementation**

```python
# strategies/crypto_fair_value.py
"""Crypto Fair-Value Trader (B2) — event-driven 1-sec fast-scan trader.

Targets KXBTC + KXETH + KXXRP + KXBTCD + KXETHD (binary "asset >= K at T"
markets). Compares Kalshi implied prob to lognormal model fair prob using
external spot (PriceFeed via Binance WS) + Deribit IV (BTC/ETH) or
realized vol (XRP fallback). Fires single edge-scaled directional trade
gated by 9 risk gates including 60-sec recently-fired guard and
cross-strategy exposure cap.

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("crypto_fair_value")

# Series → asset mapping (handles regular + daily variants)
SERIES_TO_ASSET = {
    "KXBTC": "BTC", "KXBTCD": "BTC",
    "KXETH": "ETH", "KXETHD": "ETH",
    "KXXRP": "XRP",
}
TARGET_SERIES = frozenset(SERIES_TO_ASSET.keys())

# Suffix: T<float> (current Kalshi format)
SUFFIX_RE = re.compile(r"^T(\d+(?:\.\d+)?)$")


class CryptoFairValueStrategy:
    STRATEGY_NAME = "crypto_fair_value"

    def __init__(
        self, config, order_manager, market_scanner, kalshi_client,
        *, price_feed,
    ):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._price_feed = price_feed

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        series = m.get("ticker", "").split("-")[0]
        return series in TARGET_SERIES

    @staticmethod
    def _extract_strike(m: dict) -> Optional[float]:
        suffix = m.get("ticker", "").rsplit("-", 1)[-1]
        match = SUFFIX_RE.match(suffix)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _extract_asset(m: dict) -> Optional[str]:
        series = m.get("ticker", "").split("-")[0]
        return SERIES_TO_ASSET.get(series)

    @staticmethod
    def _filter_atm(markets: list[dict], *, spot: float, asset: str, n: int) -> list[dict]:
        """Keep N strikes immediately above and N immediately below spot.

        Sort all target markets by |strike - spot| and take the top 2N+1
        (one ATM + N above + N below).
        """
        # Filter to target asset only
        candidates = [
            (m, CryptoFairValueStrategy._extract_strike(m))
            for m in markets
            if CryptoFairValueStrategy._extract_asset(m) == asset
        ]
        candidates = [(m, s) for (m, s) in candidates if s is not None]
        # Sort by distance to spot, take 2n+1 closest
        candidates.sort(key=lambda t: abs(t[1] - spot))
        return [m for (m, _s) in candidates[: 2 * n + 1]]

    @staticmethod
    def _time_to_close_secs(m: dict, *, now: Optional[datetime] = None) -> float:
        if now is None:
            now = datetime.now(timezone.utc)
        close_iso = m.get("close_time", "")
        if not close_iso:
            return -1.0
        try:
            close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except Exception:
            return -1.0
        return (close_dt - now).total_seconds()
```

- [ ] **Step 8.4: Run test to verify it passes**

```
pytest tests/test_crypto_fair_value_strategy.py -v
```
Expected: 6 PASS.

- [ ] **Step 8.5: Commit**

```bash
git add strategies/crypto_fair_value.py tests/test_crypto_fair_value_strategy.py
git commit -m "$(cat <<'EOF'
feat(crypto): add CryptoFairValueStrategy shell — universe/parsing/ATM filter

Class skeleton with universe filter (5 series), strike/asset extraction
(handles T<float> current Kalshi format + daily variant suffix mapping),
ATM ±N strike filter, and time-to-close helper. Edge math + scan loop
in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: CryptoFairValueStrategy — vol resolution + edge math + sizing

**Files:**
- Modify: `strategies/crypto_fair_value.py`
- Modify: `tests/test_crypto_fair_value_strategy.py`

- [ ] **Step 9.1: Append failing tests**

Append to `tests/test_crypto_fair_value_strategy.py`:
```python
@pytest.mark.asyncio
async def test_get_vol_uses_deribit_for_btc_when_fresh():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.65,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.80)  # would be used as fallback

    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("BTC", db=db)
    assert vol == 0.65
    assert source == "deribit"


@pytest.mark.asyncio
async def test_get_vol_falls_back_to_realized_when_iv_stale():
    cfg = _cfg()
    db = MagicMock()
    stale = datetime.now(timezone.utc) - timedelta(hours=12)  # > 6h IV TTL
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.65, "fetched_at": stale.isoformat(),
    })
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.80)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("BTC", db=db)
    assert vol == 0.80
    assert source == "realized"


@pytest.mark.asyncio
async def test_get_vol_uses_realized_for_xrp():
    """XRP is not a Deribit asset, always falls back to realized."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value=None)
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.95)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("XRP", db=db)
    assert vol == 0.95
    assert source == "realized"


@pytest.mark.asyncio
async def test_get_vol_returns_none_when_both_missing():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value=None)
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=None)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    out = await s._get_vol("BTC", db=db)
    assert out is None


def test_compute_edge_buy_yes_when_fair_above_implied():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.30, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.70,
        fair_prob=0.50,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.21) < 1e-6  # 0.50 - 0.29 (mid)
    assert edge["entry_price"] == 0.30


def test_compute_edge_buy_no_when_fair_below_implied():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.70, kalshi_yes_bid=0.68,
        kalshi_no_ask=0.32, kalshi_no_bid=0.30,
        fair_prob=0.45,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert edge["deviation"] < 0
    assert edge["entry_price"] == 0.32  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.30, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.70,
        fair_prob=0.32,  # mid 0.29, dev 0.03 < 0.10
    )
    assert edge is None


def test_size_for_edge_scales_and_caps():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    # base=20, clamp(0.10/0.10, 0.5, 3.0) = 1.0 → 20
    assert s._size_for_edge(0.10) == 20.0
    # base × clamp(0.30/0.10, 0.5, 3.0) = 3.0 → 60, capped at 30
    assert s._size_for_edge(0.30) == 30.0
    # base × clamp(0.05/0.10, 0.5, 3.0) = 0.5 → 10
    assert s._size_for_edge(0.05) == 10.0
```

- [ ] **Step 9.2: Run test to verify it fails**

```
pytest tests/test_crypto_fair_value_strategy.py -v
```
Expected: 8 NEW FAIL with `AttributeError`.

- [ ] **Step 9.3: Append methods to CryptoFairValueStrategy**

In `strategies/crypto_fair_value.py`, add these methods to the class:
```python
    async def _get_vol(self, asset: str, *, db) -> Optional[tuple[float, str]]:
        """Returns (vol, source) or None.

        Source priority:
          1. Deribit ATM IV (BTC/ETH only, fresh ≤ 6h)
          2. PriceFeed.get_volatility (XRP, or Deribit stale/missing)
        """
        DERIBIT_ASSETS = ("BTC", "ETH")
        DERIBIT_TTL_HOURS = 6.0
        if asset in DERIBIT_ASSETS:
            iv_row = await db.fetch_nearest_crypto_iv(asset)
            if iv_row:
                try:
                    fetched = datetime.fromisoformat(
                        iv_row["fetched_at"].replace("Z", "+00:00")
                    )
                    age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
                    if age_h <= DERIBIT_TTL_HOURS:
                        return float(iv_row["atm_iv"]), "deribit"
                except Exception:
                    pass
        rv = self._price_feed.get_volatility(asset)
        if rv:
            return float(rv), "realized"
        return None

    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        fair_prob: float,
    ) -> Optional[dict]:
        """Returns {'side', 'deviation', 'entry_price', 'ev_per_contract'} or None.

        Mirrors C1 macro_event._compute_edge math (same fee + cushion).
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = fair_prob - kalshi_yes_mid
        if abs(deviation) < cfg.CRYPTO_FAIR_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            ev = fair_prob - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            ev = (1.0 - fair_prob) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        cfg = self.config
        base = cfg.CRYPTO_FAIR_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.CRYPTO_FAIR_MIN_DEVIATION))
        return min(base * scale, cfg.CRYPTO_FAIR_MAX_POSITION_PER_TICKER)
```

- [ ] **Step 9.4: Run test to verify it passes**

```
pytest tests/test_crypto_fair_value_strategy.py -v
```
Expected: 14 PASS (6 from Task 8 + 8 new).

- [ ] **Step 9.5: Commit**

```bash
git add strategies/crypto_fair_value.py tests/test_crypto_fair_value_strategy.py
git commit -m "$(cat <<'EOF'
feat(crypto): add vol source resolution + edge math + sizing

_get_vol: Deribit IV (BTC/ETH, fresh ≤6h) → realized vol (XRP or stale).
_compute_edge: same math as C1 macro_event (deviation > MIN, EV > 0 guards).
_size_for_edge: edge-scaled base × clamp(|dev|/MIN, 0.5, 3.0), per-ticker cap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: CryptoFairValueStrategy — fast-scan task with 9 gates

**Files:**
- Modify: `strategies/crypto_fair_value.py`
- Modify: `tests/test_crypto_fair_value_strategy.py`

- [ ] **Step 10.1: Append failing tests**

Append to `tests/test_crypto_fair_value_strategy.py`:
```python
import time
from unittest.mock import patch

from core.kalshi_orderbook_cache import OrderbookEntry


def _fresh_entry(yes_ask=0.30, no_ask=0.70, depth=100.0):
    return OrderbookEntry(
        yes_ask=yes_ask, yes_bid=yes_ask, no_ask=no_ask, no_bid=no_ask,
        yes_depth=depth, no_depth=depth, ts=time.time(),
    )


@pytest.mark.asyncio
async def test_handle_market_fires_buy_yes_when_all_gates_pass():
    """Spot 90000, strike 89500 (ITM call), 1h to close, BTC IV 0.50 →
    fair > 0.50; if Kalshi implies 0.30 → BUY YES."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db

    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70, depth=100.0))

    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)

    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "crypto_fair_value"


@pytest.mark.asyncio
async def test_handle_market_skips_when_recently_fired():
    """Same ticker fired 30 sec ago → skip (recently_fired guard)."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70))
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    s._recently_fired["KXBTC-26MAY1017-T89500"] = time.time() - 30  # 30s ago

    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_spot_stale():
    cfg = _cfg()
    pf = MagicMock()
    pf.is_stale = MagicMock(return_value=True)
    om = MagicMock()
    om.market_order = AsyncMock()
    om._db = MagicMock()
    cache = MagicMock()
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {"ticker": "KXBTC-X-T100",
              "close_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    await s._handle_market(market, db=MagicMock(), cache=cache,
                            now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_exposure_cap_exceeded():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=60.0)  # 60 + 30 > 80 cap
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70))
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_depth_insufficient():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70, depth=10.0))  # < 30
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()
```

- [ ] **Step 10.2: Run test to verify it fails**

```
pytest tests/test_crypto_fair_value_strategy.py -v
```
Expected: 5 NEW FAIL.

- [ ] **Step 10.3: Implement _handle_market with 9-gate orchestration + scan_loop**

In `strategies/crypto_fair_value.py`, add to imports at the top:
```python
import asyncio
import time
from core.fair_value_math import lognormal_cdf_above
```

In `__init__`, add a recently-fired tracker:
```python
        self._recently_fired: dict[str, float] = {}  # ticker → epoch ts
```

Add the orchestration methods to the class:
```python
    async def _handle_market(self, market: dict, *, db, cache, now: datetime):
        """9-gate pipeline for a single market — fires market_order if all gates pass."""
        cfg = self.config
        ticker = market.get("ticker", "")
        asset = self._extract_asset(market)
        if not asset:
            return

        # Gate 1: spot freshness
        if self._price_feed.is_stale(asset):
            return
        spot = self._price_feed.get_price(asset)
        if spot is None or spot <= 0:
            return

        # Gate 2: vol availability
        vol_pair = await self._get_vol(asset, db=db)
        if vol_pair is None:
            return
        vol, vol_source = vol_pair

        # Gate 3: window (close > MIN_TIME_TO_CLOSE_SECS)
        secs_to_close = self._time_to_close_secs(market, now=now)
        if secs_to_close < cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS:
            return
        tau_years = secs_to_close / (365.25 * 86400)

        # Compute fair prob
        strike = self._extract_strike(market)
        if strike is None:
            return
        fair = lognormal_cdf_above(spot=spot, strike=strike, vol=vol, tau_years=tau_years)
        if fair is None:
            return

        # Gate 4: depth (need cached orderbook)
        ob = cache.get(ticker)
        if ob is None:
            return
        # Compute edge
        edge = self._compute_edge(
            kalshi_yes_ask=ob.yes_ask, kalshi_yes_bid=ob.yes_bid,
            kalshi_no_ask=ob.no_ask, kalshi_no_bid=ob.no_bid,
            fair_prob=fair,
        )
        # Gates 5+6: deviation + EV (handled by _compute_edge)
        if edge is None:
            return
        depth_for_side = ob.yes_depth if edge["side"] == "yes" else ob.no_depth
        if depth_for_side < cfg.CRYPTO_FAIR_MIN_DEPTH_USDC:
            return

        # Gate 7: recently_fired (over-fire guard)
        last_fire = self._recently_fired.get(ticker)
        if last_fire is not None and (time.time() - last_fire) < cfg.CRYPTO_FAIR_RECENTLY_FIRED_SECS:
            return

        # Gate 8: cross-strategy exposure cap
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE:
            return

        # Gate 9: per-strategy DB dedup is enforced by OrderManager

        logger.info(
            f"[CRYPTO_FAIR] FIRE {ticker} {edge['side']} size=${size:.2f} "
            f"spot={spot:.2f} strike={strike:.2f} fair={fair:.4f} "
            f"impl={(ob.yes_ask + ob.yes_bid)/2:.4f} dev={edge['deviation']:+.4f} "
            f"vol={vol:.3f}({vol_source})"
        )
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
        self._recently_fired[ticker] = time.time()

    async def scan_loop(self, *, cache, scan_interval_secs: float = 1.0):
        """Main fast-scan loop — runs as separate asyncio task in main.py.

        cache: OrderbookCache instance (its refresh_loop should be running
        in parallel — caller's responsibility to schedule both tasks).
        """
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db — crypto_fair_value scan disabled")
            return
        while True:
            try:
                now = datetime.now(timezone.utc)
                # Universe: ATM ±N tickers per asset
                cfg = self.config
                all_markets = [
                    m for m in self._scanner.get_all_markets()
                    if self._is_target_market(m)
                ]
                # Group by asset, filter ATM ±N
                target_tickers: list[str] = []
                for asset in ("BTC", "ETH", "XRP"):
                    spot = self._price_feed.get_price(asset)
                    if spot is None:
                        continue
                    atm_markets = self._filter_atm(
                        all_markets, spot=spot, asset=asset,
                        n=cfg.CRYPTO_FAIR_ATM_WINDOW_N,
                    )
                    for m in atm_markets:
                        target_tickers.append(m.get("ticker", ""))
                        await self._handle_market(m, db=db, cache=cache, now=now)
                # Update cache target list (so refresh_loop knows what to poll)
                cache.set_target_tickers(target_tickers)
            except Exception as e:
                logger.exception(f"crypto_fair_value scan error: {e}")
            await asyncio.sleep(scan_interval_secs)
```

- [ ] **Step 10.4: Run test to verify it passes**

```
pytest tests/test_crypto_fair_value_strategy.py tests/test_crypto_fair_value_dedup.py -v
```
Expected: 19 PASS (14 from Tasks 8-9 + 5 new + 1 dedup integration).

- [ ] **Step 10.5: Commit**

```bash
git add strategies/crypto_fair_value.py tests/test_crypto_fair_value_strategy.py
git commit -m "$(cat <<'EOF'
feat(crypto): wire CryptoFairValueStrategy with 9 risk gates + scan_loop

_handle_market gate order: spot freshness → vol availability → window →
fair compute → depth → deviation+EV → recently-fired → cross-strategy
exposure → fire (DB dedup via OrderManager). scan_loop runs as separate
asyncio task at 1s cadence, dynamically updates orderbook cache target
list based on current ATM ±N strikes per asset.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: deribit_iv_cron.py

**Files:**
- Create: `scripts/deribit_iv_cron.py`

- [ ] **Step 11.1: Write the script**

```python
# scripts/deribit_iv_cron.py
"""Deribit IV refresh cron — runs every 4 hours.

Fetches BTC + ETH option chains from Deribit, extracts ATM IV per
expiry, upserts into crypto_iv DB. Designed to run as a separate
process (launchd/systemd) so scrape failures don't impact the main
bot loop.

Usage:
  python scripts/deribit_iv_cron.py --once
  python scripts/deribit_iv_cron.py        # loop every 4h
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
from core.deribit_iv import DeribitIVScraper, extract_atm_iv

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("deribit_cron")

# Spot reference for ATM strike selection. Manual fallback list — in
# production these will be overridden by PriceFeed.get_price if running
# inside the main bot, but the cron is standalone so use coarse defaults.
# Adjust periodically as crypto prices drift; the strike picker is robust
# to ±20% spot error (just picks slightly OTM/ITM).
SPOT_REFERENCE_FALLBACK = {
    "BTC": 90000.0,
    "ETH": 3000.0,
}


async def refresh_one(db: Database, scraper: DeribitIVScraper, asset: str) -> int:
    chain = scraper.fetch_chain(asset)
    if not chain:
        log.warning(f"Deribit fetch returned no chain for {asset}")
        return 0
    spot = SPOT_REFERENCE_FALLBACK.get(asset, 100.0)
    atm_per_expiry = extract_atm_iv(chain, asset=asset, current_spot=spot)
    updated = 0
    for expiry, atm_iv in atm_per_expiry.items():
        await db.record_crypto_iv(asset, expiry, atm_iv)
        updated += 1
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    scraper = DeribitIVScraper()

    interval = 4 * 60 * 60  # 4 hours

    while True:
        t0 = time.time()
        try:
            n_btc = await refresh_one(db, scraper, "BTC")
            n_eth = await refresh_one(db, scraper, "ETH")
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — btc={n_btc} eth={n_eth}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 11.2: Smoke test --once**

```
cd /Users/taejunoh/desktop/LFG/kalshi-arb && source venv/bin/activate
python scripts/deribit_iv_cron.py --once
```
Expected: 2-30s runtime, logs `refresh complete ... btc=N eth=M`. N may be 0 if Deribit blocks the dev IP (not blocking — production EC2 deployment will work).

Verify DB:
```
sqlite3 data/bot.db "SELECT asset, COUNT(*) FROM crypto_iv GROUP BY asset;"
```

- [ ] **Step 11.3: Commit**

```bash
git add scripts/deribit_iv_cron.py
git commit -m "$(cat <<'EOF'
feat(crypto): add 4h Deribit IV refresh cron

Standalone async script — fetches BTC + ETH option chains from Deribit
public REST, extracts ATM IV per expiry via core/deribit_iv.py helper,
upserts into crypto_iv table. v0 uses coarse spot reference fallback
($90k BTC, $3k ETH) for ATM strike selection — strike picker is robust
to ±20% spot error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: research/crypto_fair_value_signal_scanner.py — Phase 0 dry-run

**Files:**
- Create: `scripts/research/crypto_fair_value_signal_scanner.py`

- [ ] **Step 12.1: Write the script**

```python
# scripts/research/crypto_fair_value_signal_scanner.py
"""Phase 0 prevalence scanner for B2 crypto_fair_value.

Mirrors strategies/crypto_fair_value.py's signal logic but does NOT
fire any trade. Logs every signal evaluation to crypto_fair_value_signals
for offline analysis.

Run for ≥1 week before paper deployment to validate fire rate + EV
distribution across BTC/ETH/XRP.

NOTE: This script needs PriceFeed running. The simplest deployment is
to run it INSIDE the main bot process via main.py's _bot_tasks list,
or as a separate process that also subscribes its own PriceFeed.

For local --once testing, this script polls Coinbase REST as a quick
spot reference instead of subscribing to a full WS feed.

Usage:
  python scripts/research/crypto_fair_value_signal_scanner.py --once
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

import httpx

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.fair_value_math import lognormal_cdf_above
from strategies.crypto_fair_value import (
    CryptoFairValueStrategy, SERIES_TO_ASSET, TARGET_SERIES,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("crypto_scanner")

# Coinbase REST spot lookup for --once runs (no WS required)
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{asset}-USD/spot"


def fetch_spot_via_rest(asset: str) -> float | None:
    try:
        r = httpx.get(COINBASE_SPOT_URL.format(asset=asset), timeout=10.0)
        if r.status_code != 200:
            return None
        return float(r.json()["data"]["amount"])
    except Exception as e:
        log.warning(f"Coinbase spot fetch failed for {asset}: {e}")
        return None


def fetch_open_markets(client: KalshiClient, series: str) -> list[dict]:
    out = []
    cursor = None
    pages = 0
    while pages < 10:
        path = f"/trade-api/v2/markets?status=open&series_ticker={series}&limit=200"
        if cursor:
            path += f"&cursor={cursor}"
        d = client._get(path)
        if not d:
            break
        out.extend(d.get("markets", []))
        cursor = d.get("cursor") or None
        pages += 1
        if not cursor:
            break
    return out


async def scan_once(client: KalshiClient, db: Database, cfg: Config) -> dict:
    summary = defaultdict(int)
    # Build a no-OrderManager strategy instance just to use its parsing helpers
    strategy = CryptoFairValueStrategy(cfg, None, None, client, price_feed=None)
    now = datetime.now(timezone.utc)

    spots: dict[str, float] = {}
    for asset in ("BTC", "ETH", "XRP"):
        s = fetch_spot_via_rest(asset)
        if s is not None:
            spots[asset] = s
            log.info(f"spot {asset} = ${s:.2f}")

    for series in TARGET_SERIES:
        asset = SERIES_TO_ASSET[series]
        spot = spots.get(asset)
        if spot is None:
            continue
        markets = fetch_open_markets(client, series)
        atm_markets = strategy._filter_atm(
            markets, spot=spot, asset=asset,
            n=cfg.CRYPTO_FAIR_ATM_WINDOW_N,
        )
        for m in atm_markets:
            ticker = m.get("ticker", "")
            strike = strategy._extract_strike(m)
            if strike is None:
                continue
            secs = strategy._time_to_close_secs(m, now=now)
            if secs < cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS:
                summary[f"{series}/skip_window"] += 1
                continue

            # Vol from Deribit (BTC/ETH) or skip XRP for --once mode
            vol = None
            vol_source = None
            if asset in ("BTC", "ETH"):
                iv_row = await db.fetch_nearest_crypto_iv(asset)
                if iv_row:
                    vol = float(iv_row["atm_iv"])
                    vol_source = "deribit"
            if vol is None:
                summary[f"{series}/no_vol"] += 1
                continue

            tau_years = secs / (365.25 * 86400)
            fair = lognormal_cdf_above(spot=spot, strike=strike, vol=vol, tau_years=tau_years)
            if fair is None:
                continue

            yes_price, no_price = client.get_market_prices(ticker)
            if yes_price is None or no_price is None:
                continue
            implied = (yes_price + (1.0 - no_price)) / 2.0
            deviation = fair - implied

            if abs(deviation) < cfg.CRYPTO_FAIR_MIN_DEVIATION:
                action = "skip_min_deviation"
                side = None
                size = None
            else:
                fee = cfg.KALSHI_FEE_PER_CONTRACT
                if deviation > 0:
                    side = "yes"
                    ev = fair - yes_price - fee
                else:
                    side = "no"
                    ev = (1.0 - fair) - no_price - fee
                if ev <= 0:
                    action = "skip_ev"
                    side = None
                    size = None
                else:
                    action = "fired"
                    size = strategy._size_for_edge(deviation)

            await db.record_crypto_fair_value_signal(
                ticker=ticker, asset=asset, spot=spot, strike=strike,
                vol=vol, vol_source=vol_source or "unknown",
                time_to_close_secs=int(secs),
                fair_prob=fair, kalshi_implied=implied,
                deviation=deviation, side=side,
                expected_size_usdc=size, action_taken=action,
            )
            summary[f"{series}/{action}"] += 1
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

    log.info(f"Starting crypto_fair_value scanner — interval={args.interval}s")
    while True:
        t0 = time.time()
        try:
            summary = await scan_once(client, db, cfg)
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
python scripts/research/crypto_fair_value_signal_scanner.py --once
```
Expected: 5-30 sec runtime; logs scan summary. May report `no_vol` for most markets if Task 11 cron hasn't populated cache yet — acceptable for v0 smoke.

- [ ] **Step 12.3: Commit**

```bash
git add scripts/research/crypto_fair_value_signal_scanner.py
git commit -m "$(cat <<'EOF'
feat(crypto): Phase 0 prevalence scanner (no-fire signal logger)

Mirrors crypto_fair_value signal logic but writes to
crypto_fair_value_signals without firing. Uses Coinbase REST spot
lookup so --once runs work without WS infrastructure. 1-week run feeds
Phase 0 gate (≥5 signals/day per asset, EV ≥+$10/week).

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
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python -c "import main; print('OK')"
```
Expected: `OK`.

- [ ] **Step 13.2: Add import + instances**

In `main.py`, add to the strategy import block:
```python
from strategies.crypto_fair_value import CryptoFairValueStrategy
from core.kalshi_orderbook_cache import OrderbookCache
```

In `main.py` `__init__`, after the existing `self.macro_event = ...` instance (added by C1), append:
```python
        # Crypto Fair-Value Trader (B2) — paper-only at launch.
        # Uses existing PriceFeed (Binance WS) + Deribit IV cron.
        # Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
        self.crypto_fair_value_orderbook_cache = OrderbookCache(
            client=self.kalshi_client,
            refresh_interval_secs=5.0,
            stale_threshold_secs=10.0,
        )
        self.crypto_fair_value = CryptoFairValueStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            price_feed=self.price_feed,
        )
```

- [ ] **Step 13.3: Wire scan_loop + cache refresh_loop into _bot_tasks**

In `main.py`, in the `tasks = [...]` block (around line 272), append after the existing strategy tasks:
```python
            # B2 Crypto Fair-Value — its own asyncio task pair (1s scan +
            # 5s orderbook cache refresh). Separate from _strategy_loop
            # because of higher cadence.
            asyncio.create_task(
                self.crypto_fair_value_orderbook_cache.refresh_loop()
            ),
            asyncio.create_task(
                self.crypto_fair_value.scan_loop(
                    cache=self.crypto_fair_value_orderbook_cache,
                    scan_interval_secs=1.0,
                )
            ),
```

- [ ] **Step 13.4: Smoke check after wiring**

```
python -c "import main; print('OK')"
```
Expected: `OK`.

- [ ] **Step 13.5: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): register crypto_fair_value sub-strategy + orderbook cache (paper-only)

B2 Crypto Fair-Value Trader wired as 2 asyncio tasks (1s scan_loop +
5s orderbook cache refresh_loop), separate from _strategy_loop because
of higher cadence. No entry in LIVE_STRATEGIES env → paper-only at
launch. Reuses existing PriceFeed (Binance WS BTC/ETH/SOL — XRP added
via Task 5 config change). Requires scripts/deribit_iv_cron.py running
separately to populate IV cache for BTC/ETH (XRP falls back to realized
vol from PriceFeed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

After completing all 13 tasks, perform the following checks:

**1. Spec coverage:**
- [ ] core/fair_value_math.py: lognormal_cdf_above (Task 1) ✓
- [ ] core/deribit_iv.py: scraper + ATM extraction (Task 3) ✓
- [ ] core/kalshi_orderbook_cache.py: TTL cache + refresh loop (Task 7) ✓
- [ ] core/database.py: 2 tables + 4 helpers (Tasks 2, 4) ✓
- [ ] core/order_manager.py: dedup gate prefix (Task 6) ✓
- [ ] core/config.py: 8 knobs + XRP WS (Task 5) ✓
- [ ] strategies/crypto_fair_value.py: universe/parsing/edge math/9-gate orchestration (Tasks 8-10) ✓
- [ ] scripts/deribit_iv_cron.py (Task 11) ✓
- [ ] scripts/research/crypto_fair_value_signal_scanner.py (Task 12) ✓
- [ ] main.py registration (Task 13) ✓
- [ ] Spec mentions Telegram alerts + dashboard panels — NOT covered. Defer to operational follow-up after Phase 0 confirms viability (same pattern as A1/C1).
- [ ] Spec mentions promotion_eval entry — NOT covered. Defer to Phase 1→2 transition (1-line addition).
- [ ] A1.ladder_crypto parser fix — already committed separately (2fc4ba8) before this plan starts.

**2. Placeholder scan:** No "TBD" / "implement later" — every task has actual test + impl code.

**3. Type consistency:**
- `core.fair_value_math.lognormal_cdf_above(*, spot, strike, vol, tau_years)` keyword-only — consistent across Tasks 1, 10, 12 ✓
- `core.deribit_iv.extract_atm_iv(chain, asset, current_spot)` — consistent across Tasks 3 and 11 ✓
- `Database.record_crypto_iv(asset, expiry_date, atm_iv)` — consistent across Tasks 2 and 11 ✓
- `Database.fetch_nearest_crypto_iv(asset)` — consistent across Tasks 2, 9, 12 ✓
- `Database.total_open_exposure(market_id)` — reused from C1, consistent in Task 10 ✓
- `OrderbookCache.get(ticker)` returns `OrderbookEntry | None` — consistent across Tasks 7 and 10 ✓
- `OrderbookCache.set_target_tickers(tickers: list[str])` — consistent across Tasks 7 and 10 ✓
- `CryptoFairValueStrategy.__init__(config, order_manager, market_scanner, kalshi_client, *, price_feed)` — `price_feed` keyword-only across Tasks 8, 9, 10, 13 ✓
- `STRATEGY_NAME = "crypto_fair_value"` matches dedup-gate prefix `crypto_*` (Task 6) ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-10-crypto-fair-value-trader-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
