# Macro Event Trader (C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single sub-strategy `macro_event` that fires directional trades on KXCPI + KXFEDDECISION when Kalshi implied prob deviates from external consensus (CME FedWatch + Cleveland Fed Inflation Nowcast) by ≥ MIN_DEVIATION (default 0.10 = fee 7c + 3c cushion), gated by a 9-stage risk pipeline including cross-strategy exposure cap with the existing ladder_fed (A1).

**Architecture:** Stateless `core/macro_consensus.py` exposes 2 scrapers (FedWatch JSON, Cleveland Fed HTML) + 1 normal-distribution-to-bucket helper, with persistence via a new `macro_consensus` SQLite table. `strategies/macro_event.py` is a single class that consumes the cached consensus, applies edge math + 9 risk gates, and fires edge-scaled single trades. A separate `scripts/macro_consensus_cron.py` process refreshes consensus every 4h. A no-fire research scanner mirrors the strategy's signal logic into `macro_event_signals` for Phase 0 prevalence analysis.

**Tech Stack:** Python 3 + httpx (already installed) + math.erf for normal CDF (no scipy) + regex for HTML parsing (no bs4) + aiosqlite + pytest. Codebase patterns: `strategies/ladder_fed.py` (single-strategy class shape, just merged in PR #1), `strategies/sports_underdog_v32.py` (edge-scaled sizing), `core/order_manager.py:438+` (dedup gate prefix).

**Spec:** `docs/superpowers/specs/2026-05-09-macro-event-trader-design.md` (commit effa729).

---

## File Structure

**Create:**
- `core/macro_consensus.py` — scrapers + normal-CDF helper + thin DB cache wrappers (~250 LOC)
- `strategies/macro_event.py` — MacroEventStrategy class (~200 LOC)
- `scripts/macro_consensus_cron.py` — 4h refresh cron entrypoint (~80 LOC)
- `scripts/research/macro_event_signal_scanner.py` — Phase 0 dry-run logger (~120 LOC)
- `tests/test_macro_consensus.py` — scraper + bucket helper unit tests (~200 LOC)
- `tests/test_macro_event_strategy.py` — strategy unit + integration tests (~250 LOC)
- `tests/test_macro_event_dedup.py` — V1 dedup pattern test for macro_event (~80 LOC)
- `tests/test_cross_strategy_exposure.py` — exposure helper tests (~80 LOC)

**Modify:**
- `core/database.py` — add 2 new tables (`macro_consensus`, `macro_event_signals`) + 4 helpers
- `core/order_manager.py` — extend dedup gate prefix from `(sports_*, ladder_*)` to also accept `macro_*` (5 sites + comment cleanup), add `_total_open_exposure(market_id)` helper
- `core/config.py` — add 8 new MACRO_* env knobs
- `main.py` — import + instantiate + register `macro_event` in `_strategy_loop` (paper-only)

---

## Task 1: macro_consensus DB schema + record/fetch helpers

**Files:**
- Modify: `core/database.py`
- Create: `tests/test_macro_consensus_db.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_macro_consensus_db.py
import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_and_fetch_macro_consensus():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_consensus(
            event_key="FOMC-26JUN", bucket="C25",
            prob=0.65, source="fedwatch",
        )
        row = await db.fetch_macro_consensus("FOMC-26JUN", "C25")
        assert row is not None
        assert row["prob"] == 0.65
        assert row["source"] == "fedwatch"
        assert row["fetched_at"]


@pytest.mark.asyncio
async def test_record_macro_consensus_upserts():
    """Re-recording (event_key, bucket, source) updates prob in place."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_consensus("FOMC-26JUN", "C25", 0.65, "fedwatch")
        await db.record_macro_consensus("FOMC-26JUN", "C25", 0.70, "fedwatch")
        row = await db.fetch_macro_consensus("FOMC-26JUN", "C25")
        assert row["prob"] == 0.70
```

- [ ] **Step 1.2: Run test to verify it fails**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
pytest tests/test_macro_consensus_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_macro_consensus'`.

- [ ] **Step 1.3: Add CREATE TABLE + helpers**

In `core/database.py`, inside `Database.initialize()` after the `ladder_violations` block (added by A1), append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_consensus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    prob REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(event_key, bucket, source)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mc_event_ts "
                "ON macro_consensus(event_key, fetched_at)"
            )
```

Add two methods to the `Database` class (place near `record_ladder_violation`):
```python
    async def record_macro_consensus(
        self, event_key: str, bucket: str, prob: float, source: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO macro_consensus "
                "(event_key, bucket, prob, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(event_key, bucket, source) DO UPDATE SET "
                "prob = excluded.prob, fetched_at = excluded.fetched_at",
                (event_key, bucket, prob, source, ts),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_macro_consensus(
        self, event_key: str, bucket: str, source: str | None = None,
    ) -> dict | None:
        """Returns the most recent consensus row for (event_key, bucket).
        If source is given, restrict to that source; else return any."""
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            if source:
                cur = await conn.execute(
                    "SELECT * FROM macro_consensus "
                    "WHERE event_key = ? AND bucket = ? AND source = ? "
                    "ORDER BY fetched_at DESC LIMIT 1",
                    (event_key, bucket, source),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM macro_consensus "
                    "WHERE event_key = ? AND bucket = ? "
                    "ORDER BY fetched_at DESC LIMIT 1",
                    (event_key, bucket),
                )
            row = await cur.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 1.4: Run test to verify it passes**

```
pytest tests/test_macro_consensus_db.py -v
```
Expected: 2 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add core/database.py tests/test_macro_consensus_db.py
git commit -m "$(cat <<'EOF'
feat(db): add macro_consensus table + upsert/fetch helpers

Persistence layer for C1 macro event trader — caches CME FedWatch +
Cleveland Fed Nowcast consensus per (event_key, bucket, source). UNIQUE
constraint + ON CONFLICT upsert pattern for idempotent cron refresh.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: bucket_prob_from_normal helper (math.erf based)

**Files:**
- Create: `core/macro_consensus.py`
- Create: `tests/test_macro_consensus.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_macro_consensus.py
import math
import pytest

from core.macro_consensus import bucket_prob_from_normal, normal_cdf


def test_normal_cdf_zero_mean_unit_variance():
    """N(0, 1) CDF: P(Z <= 0) = 0.5, P(Z <= 1.96) ≈ 0.975"""
    assert abs(normal_cdf(0.0, mu=0.0, sigma=1.0) - 0.5) < 1e-6
    assert abs(normal_cdf(1.96, mu=0.0, sigma=1.0) - 0.9750) < 1e-3
    assert abs(normal_cdf(-1.96, mu=0.0, sigma=1.0) - 0.0250) < 1e-3


def test_bucket_prob_centered_bucket_around_mean():
    """N(0.3, 0.1): P(0.2 <= X <= 0.4) ≈ 0.6827 (1-sigma window)"""
    p = bucket_prob_from_normal(low=0.2, high=0.4, mu=0.3, sigma=0.1)
    assert abs(p - 0.6827) < 1e-3


def test_bucket_prob_far_tail():
    """N(0.3, 0.1): P(0.6 <= X <= 0.7) ≈ 0.001 (3+ sigma)"""
    p = bucket_prob_from_normal(low=0.6, high=0.7, mu=0.3, sigma=0.1)
    assert p < 0.01


def test_bucket_prob_open_lower_bound():
    """low=None → P(X <= high), no lower clamp"""
    p = bucket_prob_from_normal(low=None, high=0.3, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_open_upper_bound():
    """high=None → P(X >= low), no upper clamp"""
    p = bucket_prob_from_normal(low=0.3, high=None, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_zero_sigma_returns_indicator():
    """Degenerate sigma=0 case: deterministic point mass at mu"""
    # mu inside bucket → 1.0
    assert bucket_prob_from_normal(0.2, 0.4, 0.3, 0.0) == 1.0
    # mu outside bucket → 0.0
    assert bucket_prob_from_normal(0.5, 0.6, 0.3, 0.0) == 0.0
```

- [ ] **Step 2.2: Run test to verify it fails**

```
pytest tests/test_macro_consensus.py -v
```
Expected: FAIL with `ImportError: cannot import name 'bucket_prob_from_normal' from 'core.macro_consensus'`.

- [ ] **Step 2.3: Write minimal implementation**

```python
# core/macro_consensus.py
"""Macro event consensus — scrapers + bucket prob helpers + DB cache shims.

Stateless functions. Persistence is delegated to core/database.py.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import math
from typing import Optional


def normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Cumulative distribution function for N(mu, sigma)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bucket_prob_from_normal(
    low: Optional[float],
    high: Optional[float],
    mu: float,
    sigma: float,
) -> float:
    """Probability that X ~ N(mu, sigma) falls in the half-open interval [low, high].

    `low=None` treated as -infinity (P(X <= high)).
    `high=None` treated as +infinity (P(X >= low)).
    `sigma=0` is the degenerate case: returns 1.0 if mu is inside the
    interval, 0.0 otherwise.
    """
    if sigma <= 0:
        in_lower = (low is None) or (mu >= low)
        in_upper = (high is None) or (mu <= high)
        return 1.0 if (in_lower and in_upper) else 0.0
    p_high = 1.0 if high is None else normal_cdf(high, mu, sigma)
    p_low = 0.0 if low is None else normal_cdf(low, mu, sigma)
    return max(0.0, p_high - p_low)
```

- [ ] **Step 2.4: Run test to verify it passes**

```
pytest tests/test_macro_consensus.py -v
```
Expected: 6 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add core/macro_consensus.py tests/test_macro_consensus.py
git commit -m "$(cat <<'EOF'
feat(macro): add normal_cdf + bucket_prob_from_normal (stdlib math.erf)

Pure helpers for converting Cleveland Fed Nowcast (point estimate + std)
into per-bucket probability for KXCPI bucketed markets. No scipy
dependency — uses math.erf available since Python 3.2. Handles open
intervals (low/high = None) and degenerate sigma=0 case.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: FedWatchScraper

**Files:**
- Modify: `core/macro_consensus.py`
- Modify: `tests/test_macro_consensus.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_macro_consensus.py`:
```python
from unittest.mock import patch, MagicMock
from core.macro_consensus import FedWatchScraper


def test_fedwatch_scraper_parses_meeting_probabilities():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "meetings": [
            {"date": "2026-06-17", "probabilities": {
                "C50": 0.05, "C25": 0.65, "H0": 0.28, "H25": 0.02,
            }},
        ]
    }
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        out = scraper.fetch_meeting("2026-06-17")
        assert out == {"C50": 0.05, "C25": 0.65, "H0": 0.28, "H25": 0.02}


def test_fedwatch_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


def test_fedwatch_scraper_returns_none_on_malformed_json():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.side_effect = ValueError("malformed")
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


def test_fedwatch_scraper_returns_none_on_missing_date():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"meetings": []}
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None
```

- [ ] **Step 3.2: Run test to verify it fails**

```
pytest tests/test_macro_consensus.py::test_fedwatch_scraper_parses_meeting_probabilities -v
```
Expected: FAIL with `ImportError: cannot import name 'FedWatchScraper'`.

- [ ] **Step 3.3: Append implementation**

Append to `core/macro_consensus.py`:
```python
import logging
import httpx

logger = logging.getLogger("macro_consensus")

FEDWATCH_URL_TEMPLATE = (
    "https://www.cmegroup.com/CmeWS/mvc/tools/quoteboard/iframe/"
    "cmeFedWatchTool/probabilities/{date}"
)


class FedWatchScraper:
    """Fetches CME FedWatch implied probabilities per FOMC meeting outcome.

    The endpoint is unofficial. If CME changes the schema, this returns None
    and the strategy falls back to stale cached data (with staleness alert).
    """

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_meeting(self, meeting_date: str) -> Optional[dict]:
        """Fetch implied prob per outcome bucket for one FOMC meeting.

        meeting_date: ISO date string, e.g. '2026-06-17'.
        Returns: {bucket: prob} dict (e.g. {'C25': 0.65, 'H0': 0.30}) or None on error.
        """
        url = FEDWATCH_URL_TEMPLATE.format(date=meeting_date)
        try:
            response = httpx.get(url, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"FedWatch fetch failed for {meeting_date}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"FedWatch HTTP {response.status_code} for {meeting_date}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"FedWatch JSON parse failed for {meeting_date}: {e}")
            return None
        meetings = data.get("meetings") or []
        for m in meetings:
            if m.get("date") == meeting_date:
                probs = m.get("probabilities") or {}
                if probs:
                    return dict(probs)
        return None
```

- [ ] **Step 3.4: Run test to verify it passes**

```
pytest tests/test_macro_consensus.py -v
```
Expected: 10 PASS (6 from Task 2 + 4 new).

- [ ] **Step 3.5: Commit**

```bash
git add core/macro_consensus.py tests/test_macro_consensus.py
git commit -m "$(cat <<'EOF'
feat(macro): add FedWatchScraper (CME unofficial JSON endpoint)

Returns per-outcome implied prob dict for a given FOMC meeting date.
Returns None on any failure (HTTP error, JSON malformed, missing date) —
caller (cron) handles fallback to stale cached data with alerts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: CPINowcastScraper (Cleveland Fed)

**Files:**
- Modify: `core/macro_consensus.py`
- Modify: `tests/test_macro_consensus.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_macro_consensus.py`:
```python
from core.macro_consensus import CPINowcastScraper

# Realistic snippet of Cleveland Fed Nowcast HTML — point + std embedded
# in a tabular layout. Adjust regex if real page layout differs.
SAMPLE_NOWCAST_HTML = """
<html><body>
<div class="nowcast-table">
  <table>
    <tr><th>Indicator</th><th>Forecast (%)</th><th>Std Error</th></tr>
    <tr><td>CPI MoM</td><td>0.31</td><td>0.10</td></tr>
    <tr><td>Core CPI MoM</td><td>0.27</td><td>0.09</td></tr>
  </table>
</div>
</body></html>
"""


def test_cpi_nowcast_scraper_extracts_point_and_std():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = SAMPLE_NOWCAST_HTML
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = CPINowcastScraper()
        out = scraper.fetch()
        assert out is not None
        assert abs(out["cpi_mom_point"] - 0.31) < 1e-6
        assert abs(out["cpi_mom_std"] - 0.10) < 1e-6


def test_cpi_nowcast_scraper_returns_none_when_pattern_missing():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = "<html><body>maintenance</body></html>"
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = CPINowcastScraper()
        assert scraper.fetch() is None


def test_cpi_nowcast_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 503
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        assert CPINowcastScraper().fetch() is None
```

- [ ] **Step 4.2: Run test to verify it fails**

```
pytest tests/test_macro_consensus.py::test_cpi_nowcast_scraper_extracts_point_and_std -v
```
Expected: FAIL with `ImportError: cannot import name 'CPINowcastScraper'`.

- [ ] **Step 4.3: Append implementation**

Append to `core/macro_consensus.py`:
```python
import re

CPI_NOWCAST_URL = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"

# Match a row containing "CPI MoM" then two decimal numbers (point, std).
# Adjust this if Cleveland Fed page layout changes.
CPI_MOM_ROW_RE = re.compile(
    r"CPI\s*MoM.*?>\s*(-?\d+(?:\.\d+)?)\s*</td>\s*<td[^>]*>\s*(-?\d+(?:\.\d+)?)\s*</td>",
    re.IGNORECASE | re.DOTALL,
)


class CPINowcastScraper:
    """Fetches Cleveland Fed Inflation Nowcast point + std for next CPI release."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch(self) -> Optional[dict]:
        """Returns {'cpi_mom_point': float, 'cpi_mom_std': float} or None."""
        try:
            response = httpx.get(CPI_NOWCAST_URL, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"CPI Nowcast fetch failed: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"CPI Nowcast HTTP {response.status_code}")
            return None
        match = CPI_MOM_ROW_RE.search(response.text)
        if not match:
            logger.warning("CPI Nowcast: CPI MoM row not found in HTML")
            return None
        try:
            point = float(match.group(1))
            std = float(match.group(2))
        except ValueError as e:
            logger.warning(f"CPI Nowcast: failed to parse point/std: {e}")
            return None
        return {"cpi_mom_point": point, "cpi_mom_std": std}
```

- [ ] **Step 4.4: Run test to verify it passes**

```
pytest tests/test_macro_consensus.py -v
```
Expected: 13 PASS (10 + 3 new).

- [ ] **Step 4.5: Commit**

```bash
git add core/macro_consensus.py tests/test_macro_consensus.py
git commit -m "$(cat <<'EOF'
feat(macro): add CPINowcastScraper (Cleveland Fed HTML scrape)

Extracts CPI MoM point estimate + standard error from the Cleveland Fed
Inflation Nowcasting page. Regex-based (no bs4 dep). Returns None on any
parse failure — production logic uses stale cached data with alerts.

Note: regex pattern targets "CPI MoM" row in tabular layout. If the page
HTML layout changes materially, the regex needs an update — flagged as a
maintenance hotspot in the spec's open questions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: MACRO_* config knobs

**Files:**
- Modify: `core/config.py`
- Create: `tests/test_macro_config.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_macro_config.py
import os
from unittest.mock import patch

from core.config import Config


MACRO_KEYS = (
    "MACRO_EVENT_MIN_DEVIATION",
    "MACRO_EVENT_MIN_DEPTH_USDC",
    "MACRO_EVENT_BASE_SIZE_USDC",
    "MACRO_EVENT_MAX_POSITION_PER_TICKER",
    "MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE",
    "MACRO_EVENT_WINDOW_MAX_HOURS",
    "MACRO_EVENT_WINDOW_MIN_MINUTES",
    "MACRO_CONSENSUS_STALE_HOURS",
)


def test_macro_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in MACRO_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.MACRO_EVENT_MIN_DEVIATION == 0.10
        assert cfg.MACRO_EVENT_MIN_DEPTH_USDC == 20.0
        assert cfg.MACRO_EVENT_BASE_SIZE_USDC == 20.0
        assert cfg.MACRO_EVENT_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.MACRO_EVENT_WINDOW_MAX_HOURS == 336.0  # 14 days
        assert cfg.MACRO_EVENT_WINDOW_MIN_MINUTES == 30.0
        assert cfg.MACRO_CONSENSUS_STALE_HOURS == 24.0


def test_macro_config_env_override():
    with patch.dict(os.environ, {
        "MACRO_EVENT_MIN_DEVIATION": "0.15",
        "MACRO_EVENT_BASE_SIZE_USDC": "25",
    }):
        cfg = Config()
        assert cfg.MACRO_EVENT_MIN_DEVIATION == 0.15
        assert cfg.MACRO_EVENT_BASE_SIZE_USDC == 25.0
```

- [ ] **Step 5.2: Run test to verify it fails**

```
pytest tests/test_macro_config.py -v
```
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'MACRO_EVENT_MIN_DEVIATION'`.

- [ ] **Step 5.3: Add config attributes**

Append to `Config.__init__` body in `core/config.py` (after the LADDER_* block from A1):
```python
        # Macro Event Trader (C1) — single sub-strategy macro_event.
        # Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
        self.MACRO_EVENT_MIN_DEVIATION = float(os.getenv("MACRO_EVENT_MIN_DEVIATION", "0.10"))
        self.MACRO_EVENT_MIN_DEPTH_USDC = float(os.getenv("MACRO_EVENT_MIN_DEPTH_USDC", "20"))
        self.MACRO_EVENT_BASE_SIZE_USDC = float(os.getenv("MACRO_EVENT_BASE_SIZE_USDC", "20"))
        self.MACRO_EVENT_MAX_POSITION_PER_TICKER = float(os.getenv("MACRO_EVENT_MAX_POSITION_PER_TICKER", "30"))
        self.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.MACRO_EVENT_WINDOW_MAX_HOURS = float(os.getenv("MACRO_EVENT_WINDOW_MAX_HOURS", "336"))
        self.MACRO_EVENT_WINDOW_MIN_MINUTES = float(os.getenv("MACRO_EVENT_WINDOW_MIN_MINUTES", "30"))
        self.MACRO_CONSENSUS_STALE_HOURS = float(os.getenv("MACRO_CONSENSUS_STALE_HOURS", "24"))
```

- [ ] **Step 5.4: Run test to verify it passes**

```
pytest tests/test_macro_config.py -v
```
Expected: 2 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add core/config.py tests/test_macro_config.py
git commit -m "$(cat <<'EOF'
feat(config): add MACRO_* env knobs (8 thresholds)

Per spec: MIN_DEVIATION=0.10 (fee 0.07 + 3c cushion), depth $20,
base size $20, per-ticker cap $30, cross-strategy cap $80,
window 336h (14d) — 30min, consensus stale 24h.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Extend has_existing_trade dedup gate to macro_*

**Files:**
- Modify: `core/order_manager.py` (5 sites + comment block)
- Create: `tests/test_macro_event_dedup.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_macro_event_dedup.py
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_macro_event_blocked_when_paper_filled_exists():
    """macro_event re-entry on existing paper_filled trade must be skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="macro_event",
            market_id="KXCPI-26JUN-T0.3",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_filled",
        )

        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0

        result = await om._paper_market_order(
            ticker="KXCPI-26JUN-T0.3", side="yes",
            size_usdc=20.0, price=0.30,
            strategy="macro_event", market_id="KXCPI-26JUN-T0.3",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
```

- [ ] **Step 6.2: Run test to verify it fails**

```
pytest tests/test_macro_event_dedup.py -v
```
Expected: FAIL — current gate is `(sports_* or ladder_*)`, macro_* skips dedup → second fill proceeds.

- [ ] **Step 6.3: Extend dedup gate at 5 sites in `core/order_manager.py`**

Find the 5 sites:
```bash
grep -c 'startswith("sports_") or strategy.startswith("ladder_")' core/order_manager.py
```
Expected: 5.

For each of the 5 sites, change:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```
to:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
        strategy.startswith("macro_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```

After all 5 sites updated, verify:
```bash
grep -c 'strategy.startswith("macro_")' core/order_manager.py
```
Expected: 5.

Also update the canonical comment block at `_paper_market_order` (currently mentions sports_* + ladder_*) to add macro_*:

```python
        # DB-side dedup applies to:
        #   sports_* — single game has one outcome, re-entry is always a bug.
        #   ladder_* — single Kalshi event ladder leg has one outcome; V1
        #              shutdown_fade over-fired 13× on KXNUMSHUTDOWNS by
        #              skipping this guard. See known_bugs.md.
        #   macro_* — single (FOMC bucket | CPI bucket) outcome — same
        #              one-shot invariant. C1 macro_event sub-strategy.
        # Other strategies (mentions, market_maker, spread_scanner)
        # legitimately re-enter the same ticker.
```

- [ ] **Step 6.4: Run test to verify it passes**

```
pytest tests/test_macro_event_dedup.py -v
pytest tests/test_ladder_dedup_guard.py -v  # ensure no regression
```
Expected: PASS for both.

- [ ] **Step 6.5: Commit**

```bash
git add core/order_manager.py tests/test_macro_event_dedup.py
git commit -m "$(cat <<'EOF'
fix(order_manager): extend has_existing_trade dedup gate to macro_* prefix

C1 macro_event sub-strategy follows the same one-shot-per-(strategy,
market_id) invariant as sports_* and ladder_*. Each (FOMC bucket | CPI
bucket) market has one outcome. Extends the gate at 5 sites + updates
the canonical policy comment.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Cross-strategy exposure helper

**Files:**
- Modify: `core/database.py`
- Create: `tests/test_cross_strategy_exposure.py`

- [ ] **Step 7.1: Write the failing test**

```python
# tests/test_cross_strategy_exposure.py
import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_total_open_exposure_sums_across_strategies():
    """Multiple strategies on same ticker → exposure sums."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUN-C25",
            direction="yes", price=0.30, size=50.0, fee=0.07,
            status="paper_filled",
        )
        await db.record_trade(
            strategy="macro_event", market_id="KXFEDDECISION-26JUN-C25",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_filled",
        )
        # Different ticker — should NOT contribute
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUN-H0",
            direction="yes", price=0.40, size=30.0, fee=0.07,
            status="paper_filled",
        )

        total = await db.total_open_exposure("KXFEDDECISION-26JUN-C25")
        assert total == 70.0


@pytest.mark.asyncio
async def test_total_open_exposure_excludes_settled_trades():
    """Closed trades (status=settled, paper_settled) must not contribute."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="macro_event", market_id="KXCPI-26MAY-T0.3",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_settled",
        )
        await db.record_trade(
            strategy="macro_event", market_id="KXCPI-26MAY-T0.3",
            direction="yes", price=0.30, size=15.0, fee=0.07,
            status="paper_filled",
        )
        total = await db.total_open_exposure("KXCPI-26MAY-T0.3")
        assert total == 15.0


@pytest.mark.asyncio
async def test_total_open_exposure_returns_zero_when_no_trades():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        total = await db.total_open_exposure("KXCPI-26MAY-T0.3")
        assert total == 0.0
```

- [ ] **Step 7.2: Run test to verify it fails**

```
pytest tests/test_cross_strategy_exposure.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'total_open_exposure'`.

- [ ] **Step 7.3: Add helper method**

Add to the `Database` class in `core/database.py` (place near `has_existing_trade`):
```python
    async def total_open_exposure(self, market_id: str) -> float:
        """Sum of `size` across all strategies for open trades on this ticker.

        Open = status in ('paper_filled', 'paper_open', 'filled', 'open').
        Excludes settled / closed / failed trades.
        """
        OPEN_STATUSES = ("paper_filled", "paper_open", "filled", "open")
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                f"SELECT COALESCE(SUM(size), 0.0) FROM trades "
                f"WHERE market_id = ? AND status IN ({','.join('?' * len(OPEN_STATUSES))})",
                (market_id, *OPEN_STATUSES),
            )
            row = await cur.fetchone()
            return float(row[0]) if row else 0.0
```

- [ ] **Step 7.4: Run test to verify it passes**

```
pytest tests/test_cross_strategy_exposure.py -v
```
Expected: 3 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add core/database.py tests/test_cross_strategy_exposure.py
git commit -m "$(cat <<'EOF'
feat(db): add total_open_exposure(market_id) helper

Sums open trade sizes across all strategies for a given market. C1
macro_event uses this to enforce MAX_TICKER_TOTAL_EXPOSURE cap, ensuring
combined ladder_fed + macro_event positions on the same KXFEDDECISION
ticker stay below the safety threshold.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: macro_event_signals DB schema + helper

**Files:**
- Modify: `core/database.py`
- Modify: `tests/test_macro_consensus_db.py`

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_macro_consensus_db.py`:
```python
@pytest.mark.asyncio
async def test_record_macro_event_signal_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_event_signal(
            ticker="KXCPI-26JUN-T0.3", event_key="CPI-26JUN", bucket="T0.3",
            kalshi_implied=0.25, consensus=0.40, deviation=0.15,
            side="yes", expected_size_usdc=30.0, action_taken="fired",
        )
        rows = await db.fetch_recent_macro_event_signals(limit=5)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "KXCPI-26JUN-T0.3"
        assert rows[0]["deviation"] == 0.15
        assert rows[0]["action_taken"] == "fired"


@pytest.mark.asyncio
async def test_record_macro_event_signal_allows_skipped_with_null_side():
    """Skipped signals (e.g., skip_ev) record action_taken without side."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_event_signal(
            ticker="KXCPI-26JUN-T0.3", event_key="CPI-26JUN", bucket="T0.3",
            kalshi_implied=0.40, consensus=0.42, deviation=0.02,
            side=None, expected_size_usdc=None, action_taken="skip_ev",
        )
        rows = await db.fetch_recent_macro_event_signals(limit=5)
        assert rows[0]["action_taken"] == "skip_ev"
        assert rows[0]["side"] is None
```

- [ ] **Step 8.2: Run test to verify it fails**

```
pytest tests/test_macro_consensus_db.py -v
```
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_macro_event_signal'`.

- [ ] **Step 8.3: Add CREATE TABLE + helpers**

In `core/database.py` `initialize()`, after the `macro_consensus` block from Task 1, append:
```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_event_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    consensus REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mes_ticker_ts "
                "ON macro_event_signals(ticker, ts)"
            )
```

Add two methods to the `Database` class:
```python
    async def record_macro_event_signal(
        self, *, ticker: str, event_key: str, bucket: str,
        kalshi_implied: float, consensus: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO macro_event_signals "
                "(ts, ticker, event_key, bucket, kalshi_implied, consensus, "
                " deviation, side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, event_key, bucket, kalshi_implied, consensus,
                 deviation, side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_macro_event_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM macro_event_signals ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
```

- [ ] **Step 8.4: Run test to verify it passes**

```
pytest tests/test_macro_consensus_db.py -v
```
Expected: 4 PASS (2 from Task 1 + 2 new).

- [ ] **Step 8.5: Commit**

```bash
git add core/database.py tests/test_macro_consensus_db.py
git commit -m "$(cat <<'EOF'
feat(db): add macro_event_signals table + record/fetch helpers

Logging table for both Phase 0 dry-run scanner and live strategy. Records
every signal with action_taken (fired | skip_ev | skip_window | skip_dedup
| skip_depth | skip_exposure) for offline analysis.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: MacroEventStrategy — universe + window + ticker parsing

**Files:**
- Create: `strategies/macro_event.py`
- Create: `tests/test_macro_event_strategy.py`

- [ ] **Step 9.1: Write the failing test**

```python
# tests/test_macro_event_strategy.py
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from strategies.macro_event import MacroEventStrategy


def _cfg():
    return MagicMock(
        MACRO_EVENT_MIN_DEVIATION=0.10,
        MACRO_EVENT_MIN_DEPTH_USDC=20.0,
        MACRO_EVENT_BASE_SIZE_USDC=20.0,
        MACRO_EVENT_MAX_POSITION_PER_TICKER=30.0,
        MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        MACRO_EVENT_WINDOW_MAX_HOURS=336.0,
        MACRO_EVENT_WINDOW_MIN_MINUTES=30.0,
        MACRO_CONSENSUS_STALE_HOURS=24.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_universe_filter_includes_only_kxcpi_and_kxfeddecision():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXCPI-26JUN-T0.3"}) is True
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is True
    assert s._is_target_market({"ticker": "KXFED-26JUN-T4.25"}) is False
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False


def test_extract_event_key_for_kxfeddecision():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key({"ticker": "KXFEDDECISION-26JUN-C25"}) == "FOMC-26JUN"


def test_extract_event_key_for_kxcpi():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key({"ticker": "KXCPI-26JUN-T0.3"}) == "CPI-26JUN"


def test_extract_bucket_returns_last_segment():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_bucket({"ticker": "KXFEDDECISION-26JUN-C25"}) == "C25"
    assert s._extract_bucket({"ticker": "KXCPI-26JUN-T0.3"}) == "T0.3"


def test_window_check_inside():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(hours=24)).isoformat(),
    }
    assert s._inside_window(market, now=now) is True


def test_window_check_too_close():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(minutes=10)).isoformat(),  # < 30 min
    }
    assert s._inside_window(market, now=now) is False


def test_window_check_too_far():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(days=20)).isoformat(),  # > 14 days
    }
    assert s._inside_window(market, now=now) is False
```

- [ ] **Step 9.2: Run test to verify it fails**

```
pytest tests/test_macro_event_strategy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.macro_event'`.

- [ ] **Step 9.3: Write minimal implementation**

```python
# strategies/macro_event.py
"""Macro Event Trader (C1) — pre-event consensus deviation single-fire trader.

Targets KXCPI + KXFEDDECISION buckets. Compares Kalshi implied prob to
external consensus (CME FedWatch + Cleveland Fed Nowcast) cached via
core/macro_consensus.py and the cron in scripts/macro_consensus_cron.py.
Fires single edge-scaled directional trade when |deviation| ≥ MIN_DEVIATION
and EV after fee is positive, gated by 9 risk checks including
cross-strategy exposure cap with ladder_fed.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("macro_event")

TARGET_SERIES = frozenset({"KXCPI", "KXFEDDECISION"})

# event_key prefix maps from the Kalshi series. Used by
# core/macro_consensus.py.fetch_macro_consensus(event_key, bucket).
SERIES_TO_EVENT_PREFIX = {
    "KXCPI": "CPI",
    "KXFEDDECISION": "FOMC",
}


class MacroEventStrategy:
    STRATEGY_NAME = "macro_event"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        """KXFEDDECISION-26JUN-C25 → 'FOMC-26JUN'.
        KXCPI-26JUN-T0.3 → 'CPI-26JUN'."""
        parts = m.get("ticker", "").split("-")
        if len(parts) < 3:
            return ""
        series = parts[0]
        date_part = parts[1]
        prefix = SERIES_TO_EVENT_PREFIX.get(series)
        if not prefix:
            return ""
        return f"{prefix}-{date_part}"

    @staticmethod
    def _extract_bucket(m: dict) -> str:
        return m.get("ticker", "").rsplit("-", 1)[-1]

    def _inside_window(self, m: dict, now: Optional[datetime] = None) -> bool:
        """True if MIN_MINUTES <= time_to_close <= MAX_HOURS."""
        if now is None:
            now = datetime.now(timezone.utc)
        close_iso = m.get("close_time", "")
        if not close_iso:
            return False
        try:
            close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except Exception:
            return False
        delta = close_dt - now
        delta_min = delta.total_seconds() / 60.0
        delta_hr = delta.total_seconds() / 3600.0
        cfg = self.config
        return (delta_min >= cfg.MACRO_EVENT_WINDOW_MIN_MINUTES
                and delta_hr <= cfg.MACRO_EVENT_WINDOW_MAX_HOURS)

    async def scan_and_execute(self):
        """Stub — fully wired in Task 11."""
        return
```

- [ ] **Step 9.4: Run test to verify it passes**

```
pytest tests/test_macro_event_strategy.py -v
```
Expected: 7 PASS.

- [ ] **Step 9.5: Commit**

```bash
git add strategies/macro_event.py tests/test_macro_event_strategy.py
git commit -m "$(cat <<'EOF'
feat(macro): add MacroEventStrategy shell — universe + window + parsing

Class skeleton with universe filter (KXCPI + KXFEDDECISION only), event_key
extraction (FOMC-/CPI- prefix mapping), bucket extraction, and pre-event
window check (30min ≤ time-to-close ≤ 14d). scan_and_execute is a stub —
fully wired in Task 11.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: MacroEventStrategy — edge math + sizing

**Files:**
- Modify: `strategies/macro_event.py`
- Modify: `tests/test_macro_event_strategy.py`

- [ ] **Step 10.1: Append failing tests**

Append to `tests/test_macro_event_strategy.py`:
```python
def test_compute_edge_buy_yes_when_consensus_above_implied():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # Kalshi implied 0.25 (yes_ask 0.27, yes_bid 0.23 → mid 0.25)
    # Consensus 0.40
    edge = s._compute_edge(
        kalshi_yes_ask=0.27, kalshi_yes_bid=0.23,
        kalshi_no_ask=0.77, kalshi_no_bid=0.73,
        consensus=0.40,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.15) < 1e-6  # 0.40 - 0.25
    assert edge["entry_price"] == 0.27  # yes_ask


def test_compute_edge_buy_no_when_consensus_below_implied():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # Kalshi implied 0.55 (yes_ask 0.57, yes_bid 0.53 → mid 0.55)
    # Consensus 0.30 → buy NO
    edge = s._compute_edge(
        kalshi_yes_ask=0.57, kalshi_yes_bid=0.53,
        kalshi_no_ask=0.47, kalshi_no_bid=0.43,
        consensus=0.30,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert abs(edge["deviation"] + 0.25) < 1e-6  # negative deviation
    assert edge["entry_price"] == 0.47  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.27, kalshi_yes_bid=0.23,
        kalshi_no_ask=0.77, kalshi_no_bid=0.73,
        consensus=0.30,  # mid 0.25, dev 0.05 < 0.10
    )
    assert edge is None


def test_compute_edge_skips_when_ev_negative():
    """Even above MIN_DEVIATION, skip when entry + fee > consensus."""
    cfg = _cfg()
    cfg.MACRO_EVENT_MIN_DEVIATION = 0.05  # lower threshold so dev passes
    s = MacroEventStrategy(cfg, MagicMock(), MagicMock(), MagicMock())
    # Mid 0.20, ask 0.25, consensus 0.27 → dev 0.07 ≥ 0.05.
    # But EV = consensus - entry - fee = 0.27 - 0.25 - 0.07 = -0.05 → skip.
    edge = s._compute_edge(
        kalshi_yes_ask=0.25, kalshi_yes_bid=0.15,
        kalshi_no_ask=0.85, kalshi_no_bid=0.75,
        consensus=0.27,
    )
    assert edge is None


def test_size_for_edge_scales_with_deviation():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # base = 20, clamp(|0.10|/0.10, 0.5, 3.0) = 1.0 → size 20
    assert s._size_for_edge(deviation=0.10) == 20.0
    # base × clamp(|0.30|/0.10, 0.5, 3.0) = 3.0 → 60, capped at 30 (per ticker)
    assert s._size_for_edge(deviation=0.30) == 30.0
    # base × clamp(|0.05|/0.10, 0.5, 3.0) = 0.5 → 10
    assert s._size_for_edge(deviation=0.05) == 10.0
```

- [ ] **Step 10.2: Run tests to verify they fail**

```
pytest tests/test_macro_event_strategy.py -v
```
Expected: 5 NEW fail with `AttributeError: '_compute_edge'` / `_size_for_edge`.

- [ ] **Step 10.3: Append edge + sizing methods**

In `strategies/macro_event.py`, add these methods to `MacroEventStrategy` (above `scan_and_execute`):

```python
    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        consensus: float,
    ) -> Optional[dict]:
        """Returns trade signal dict or None.

        Signal dict: {'side': 'yes'|'no', 'deviation': float (signed),
                      'entry_price': float, 'ev_per_contract': float}
        Caller still needs to apply window + dedup + depth + exposure gates.
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = consensus - kalshi_yes_mid  # positive ⇒ Kalshi underprices YES
        if abs(deviation) < cfg.MACRO_EVENT_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            # EV = consensus·(1 - entry - fee) + (1-consensus)·(-entry - fee)
            #    = consensus - entry - fee
            ev = consensus - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            # NO wins prob = 1 - consensus
            ev = (1.0 - consensus) - entry_price - fee
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
        base = cfg.MACRO_EVENT_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.MACRO_EVENT_MIN_DEVIATION))
        return min(base * scale, cfg.MACRO_EVENT_MAX_POSITION_PER_TICKER)
```

- [ ] **Step 10.4: Run tests to verify they pass**

```
pytest tests/test_macro_event_strategy.py -v
```
Expected: 12 PASS (7 from Task 9 + 5 new).

- [ ] **Step 10.5: Commit**

```bash
git add strategies/macro_event.py tests/test_macro_event_strategy.py
git commit -m "$(cat <<'EOF'
feat(macro): add edge math + edge-scaled sizing to MacroEventStrategy

_compute_edge: returns side + deviation + entry_price + ev_per_contract,
or None if |deviation| < MIN_DEVIATION or EV ≤ 0 (post-fee).
_size_for_edge: base × clamp(|dev|/MIN_DEVIATION, 0.5, 3.0), capped at
MAX_POSITION_PER_TICKER.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: MacroEventStrategy — fire orchestration with all 9 risk gates

**Files:**
- Modify: `strategies/macro_event.py`
- Modify: `tests/test_macro_event_strategy.py`

- [ ] **Step 11.1: Append failing test**

Append to `tests/test_macro_event_strategy.py`:
```python
@pytest.mark.asyncio
async def test_scan_and_execute_fires_yes_when_all_gates_pass():
    """End-to-end happy path: window OK, consensus fresh, dev > min, EV > 0,
    depth OK, no existing trade, exposure within cap → fire BUY YES."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    # OrderManager owns the DB in production; the strategy reaches into it
    # here for test isolation, mirroring how ladder_fed exposes _db indirectly
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))  # yes_ask, no_ask
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,  # cents
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["ticker"] == "KXCPI-26JUN-T0.3"
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "macro_event"
    # base 20 × clamp(0.15/0.10, 0.5, 3.0) = 30, capped at 30
    assert call_kwargs["size_usdc"] == 30.0


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_consensus_stale():
    cfg = _cfg()
    stale = datetime.now(timezone.utc) - timedelta(hours=48)  # > 24
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40, "fetched_at": stale.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_exposure_cap_exceeded():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    # 60 already open from another strategy + 30 size → 90 > 80 cap
    db.total_open_exposure = AsyncMock(return_value=60.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_depth_insufficient():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(5.0, 5.0))  # < 20

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()
```

Add `from unittest.mock import AsyncMock` near the top of the test file imports if not already present.

- [ ] **Step 11.2: Run tests to verify they fail**

```
pytest tests/test_macro_event_strategy.py -v
```
Expected: 4 NEW tests FAIL (scan_and_execute is currently a stub).

- [ ] **Step 11.3: Implement scan_and_execute orchestration**

Replace the stub `scan_and_execute` body in `strategies/macro_event.py` with:

```python
    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_all_markets()
                   if self._is_target_market(m)]
        if not markets:
            return
        cfg = self.config
        now = datetime.now(timezone.utc)
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db attribute — skipping scan")
            return
        for m in markets:
            await self._handle_market(m, db=db, now=now)

    async def _handle_market(self, market: dict, *, db, now: datetime):
        cfg = self.config
        ticker = market.get("ticker", "")

        # Gate 1: window
        if not self._inside_window(market, now=now):
            return

        # Fetch latest book
        yes_price, no_price = self._client.get_market_prices(ticker)
        if yes_price is None or no_price is None:
            return
        yes_depth, no_depth = self._client.get_order_book_depth(ticker)
        yes_depth = yes_depth or 0.0
        no_depth = no_depth or 0.0

        # Bid/ask: KalshiClient returns mid-ish; spec section 3.7 records the
        # bid-ask collapse limitation. Use yes_price as both ask AND mid for v0.
        yes_ask = yes_bid = yes_price
        no_ask = no_bid = no_price

        # Resolve event_key + bucket + consensus
        event_key = self._extract_event_key(market)
        bucket = self._extract_bucket(market)
        if not event_key or not bucket:
            return
        consensus_row = await db.fetch_macro_consensus(event_key, bucket)
        if not consensus_row:
            return
        consensus = consensus_row["prob"]

        # Gate 2: consensus staleness
        try:
            fetched_at = datetime.fromisoformat(
                consensus_row["fetched_at"].replace("Z", "+00:00")
            )
        except Exception:
            return
        age_hours = (now - fetched_at).total_seconds() / 3600.0
        if age_hours > cfg.MACRO_CONSENSUS_STALE_HOURS:
            logger.warning(f"[MACRO] consensus stale for {event_key}/{bucket} "
                           f"({age_hours:.1f}h)")
            return

        # Gates 3 + 4: deviation + EV (handled by _compute_edge)
        edge = self._compute_edge(
            kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
            kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
            consensus=consensus,
        )
        if not edge:
            return

        # Gate 5: depth (check the side we're entering)
        depth_for_side = yes_depth if edge["side"] == "yes" else no_depth
        if depth_for_side < cfg.MACRO_EVENT_MIN_DEPTH_USDC:
            return

        # Gate 6: per-strategy dedup is enforced by OrderManager's
        # has_existing_trade guard — extended to macro_* in Task 6.

        # Gate 7: cross-strategy exposure cap
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE:
            logger.info(f"[MACRO] exposure cap on {ticker}: "
                        f"existing={existing} + size={size} > "
                        f"{cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE}")
            return

        # Fire
        logger.info(f"[MACRO] FIRE {ticker} {edge['side']} size=${size:.2f} "
                    f"dev={edge['deviation']:+.4f} ev={edge['ev_per_contract']:+.4f}")
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
```

- [ ] **Step 11.4: Run tests to verify they pass**

```
pytest tests/test_macro_event_strategy.py tests/test_macro_event_dedup.py -v
```
Expected: 16 PASS (12 from Tasks 9-10 + 4 new + 1 dedup integration).

- [ ] **Step 11.5: Commit**

```bash
git add strategies/macro_event.py tests/test_macro_event_strategy.py
git commit -m "$(cat <<'EOF'
feat(macro): wire MacroEventStrategy.scan_and_execute with 9 risk gates

Order: window → consensus staleness → deviation → EV → depth → DB dedup
(via OrderManager) → cross-strategy exposure → fire. Edge-scaled size,
direction from deviation sign. Bid-ask collapse note: v0 uses yes_price
as both ask + mid (recorded in spec section 3.7 deferral).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: macro_consensus cron script

**Files:**
- Create: `scripts/macro_consensus_cron.py`

- [ ] **Step 12.1: Write the script**

```python
# scripts/macro_consensus_cron.py
"""Macro consensus refresh cron — runs every 4 hours.

Fetches CME FedWatch + Cleveland Fed Nowcast and upserts rows into
macro_consensus DB. Designed to be run as a separate process (e.g.
launchd/cron entry on the EC2 host) so scrape failures do NOT impact
the main bot loop.

Usage:
  python scripts/macro_consensus_cron.py --once
  python scripts/macro_consensus_cron.py        # loop every 4h
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import Config
from core.database import Database
from core.macro_consensus import (
    FedWatchScraper, CPINowcastScraper, bucket_prob_from_normal,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("macro_cron")

# Known FOMC meeting dates — keep manually maintained; alternative is a
# Kalshi /series API parse but the schedule is stable enough that a
# constant list is the simplest correct option for v0.
FOMC_DATES = [
    "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
    "2026-12-16", "2027-01-27", "2027-03-17", "2027-05-05",
]

# CPI bucket boundaries — KXCPI bucket suffix format is `T<value>`
# meaning "CPI MoM <= value%". Each bucket is a 0.1% wide interval
# centered on the suffix value: e.g., T0.3 covers (0.25, 0.35].
# v0 assumes 0.1% bucket width; verify against actual Kalshi spec
# (open question 1 in design doc).
CPI_BUCKET_VALUES = [-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
CPI_BUCKET_WIDTH = 0.1

# Upcoming CPI release dates — same logic as FOMC_DATES.
CPI_DATES = [
    "2026-06-10", "2026-07-15", "2026-08-12", "2026-09-11",
    "2026-10-15", "2026-11-13", "2026-12-10", "2027-01-14",
]


def event_key_fomc(meeting_date: str) -> str:
    """2026-06-17 → 'FOMC-26JUN' (matches strategy._extract_event_key)."""
    dt = datetime.fromisoformat(meeting_date)
    return f"FOMC-{dt.strftime('%y%b').upper()}"


def event_key_cpi(release_date: str) -> str:
    dt = datetime.fromisoformat(release_date)
    return f"CPI-{dt.strftime('%y%b').upper()}"


async def refresh_fomc(db: Database, scraper: FedWatchScraper) -> int:
    """Returns # of (event, bucket) rows updated."""
    updated = 0
    for date in FOMC_DATES:
        probs = scraper.fetch_meeting(date)
        if not probs:
            log.warning(f"FedWatch fetch returned no data for {date}")
            continue
        ek = event_key_fomc(date)
        for bucket, prob in probs.items():
            await db.record_macro_consensus(ek, bucket, float(prob), "fedwatch")
            updated += 1
    return updated


async def refresh_cpi(db: Database, scraper: CPINowcastScraper) -> int:
    """Pull point + std once, then fan out to per-bucket prob via normal CDF."""
    nowcast = scraper.fetch()
    if not nowcast:
        log.warning("CPI Nowcast scrape returned no data")
        return 0
    point = nowcast["cpi_mom_point"]
    std = nowcast["cpi_mom_std"]
    updated = 0
    for date in CPI_DATES:
        ek = event_key_cpi(date)
        for value in CPI_BUCKET_VALUES:
            low = value - CPI_BUCKET_WIDTH / 2.0
            high = value + CPI_BUCKET_WIDTH / 2.0
            prob = bucket_prob_from_normal(low, high, point, std)
            bucket = f"T{value}" if value < 0 else f"T{value}"  # uniform format
            await db.record_macro_consensus(ek, bucket, prob, "clevelandfed")
            updated += 1
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    fed_scraper = FedWatchScraper()
    cpi_scraper = CPINowcastScraper()

    interval = 4 * 60 * 60  # 4 hours

    while True:
        t0 = time.time()
        try:
            n_fomc = await refresh_fomc(db, fed_scraper)
            n_cpi = await refresh_cpi(db, cpi_scraper)
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — "
                     f"fomc={n_fomc} cpi={n_cpi}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 12.2: Smoke test --once**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python scripts/macro_consensus_cron.py --once
```
Expected: 2-30s runtime; logs `refresh complete ... fomc=N cpi=M`. N may be 0 if FedWatch endpoint structure has changed (not blocking — strategy will use stale cache and Telegram-alert).

Verify DB rows landed:
```
sqlite3 data/bot.db "SELECT source, COUNT(*) FROM macro_consensus GROUP BY source;"
```

- [ ] **Step 12.3: Commit**

```bash
git add scripts/macro_consensus_cron.py
git commit -m "$(cat <<'EOF'
feat(macro): add 4h consensus refresh cron (FedWatch + Cleveland Fed)

Standalone async script — fetches FOMC outcome probs and CPI nowcast,
fans CPI nowcast (point + std) into per-bucket prob via normal CDF, and
upserts into macro_consensus DB. Designed to run as separate process
(launchd/cron) so scrape failures don't impact main bot loop. v0 uses
hardcoded FOMC + CPI date lists; auto-discovery via Kalshi /series is
a follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: research/macro_event_signal_scanner.py — Phase 0 dry-run

**Files:**
- Create: `scripts/research/macro_event_signal_scanner.py`

- [ ] **Step 13.1: Write the script**

```python
# scripts/research/macro_event_signal_scanner.py
"""Phase 0 prevalence scanner for C1 macro_event.

Mirrors strategies/macro_event.py's signal logic but does NOT fire any
trade. Logs every (signal, action_taken) tuple to macro_event_signals
for offline analysis. Run for ≥2 weeks before paper deployment to
validate fire rate + EV distribution.

Usage:
  python scripts/research/macro_event_signal_scanner.py --once
  python scripts/research/macro_event_signal_scanner.py --interval 3600
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
from strategies.macro_event import MacroEventStrategy, TARGET_SERIES

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("macro_scanner")


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
    strategy = MacroEventStrategy(cfg, None, None, client)
    now = datetime.now(timezone.utc)
    for series in TARGET_SERIES:
        markets = fetch_open_markets(client, series)
        for m in markets:
            ticker = m.get("ticker", "")
            event_key = strategy._extract_event_key(m)
            bucket = strategy._extract_bucket(m)
            if not event_key or not bucket:
                continue

            # Window check
            if not strategy._inside_window(m, now=now):
                summary[f"{series}/skip_window"] += 1
                continue

            # Pull live book (Phase 0 — minor API cost is OK at 1h cadence)
            yes_price, no_price = client.get_market_prices(ticker)
            if yes_price is None or no_price is None:
                continue

            consensus_row = await db.fetch_macro_consensus(event_key, bucket)
            if not consensus_row:
                summary[f"{series}/no_consensus"] += 1
                continue
            consensus = consensus_row["prob"]

            # Staleness
            try:
                fetched_at = datetime.fromisoformat(
                    consensus_row["fetched_at"].replace("Z", "+00:00")
                )
                age_h = (now - fetched_at).total_seconds() / 3600.0
            except Exception:
                continue
            if age_h > cfg.MACRO_CONSENSUS_STALE_HOURS:
                summary[f"{series}/stale_consensus"] += 1
                continue

            edge = strategy._compute_edge(
                kalshi_yes_ask=yes_price, kalshi_yes_bid=yes_price,
                kalshi_no_ask=no_price, kalshi_no_bid=no_price,
                consensus=consensus,
            )
            kalshi_implied = (yes_price + yes_price) / 2.0  # mid for logging
            deviation = consensus - kalshi_implied

            if not edge:
                # Either dev too small or EV negative — log as skip_ev
                action = "skip_ev"
                side = None
                size = None
            else:
                action = "fired"
                side = edge["side"]
                size = strategy._size_for_edge(edge["deviation"])

            await db.record_macro_event_signal(
                ticker=ticker, event_key=event_key, bucket=bucket,
                kalshi_implied=kalshi_implied, consensus=consensus,
                deviation=deviation, side=side,
                expected_size_usdc=size, action_taken=action,
            )
            summary[f"{series}/{action}"] += 1
    return dict(summary)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=3600,
                        help="Seconds between scans")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(cfg)

    log.info(f"Starting macro_event signal scanner — interval={args.interval}s")
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
        await asyncio.sleep(max(60.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 13.2: Smoke test --once**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python scripts/research/macro_event_signal_scanner.py --once
```
Expected: completes within ~1-2 min; logs scan summary. May report `no_consensus` for most markets if Task 12 cron hasn't populated the cache yet — that's fine, a real Phase 0 run requires the cron in place first.

- [ ] **Step 13.3: Commit**

```bash
git add scripts/research/macro_event_signal_scanner.py
git commit -m "$(cat <<'EOF'
feat(macro): Phase 0 prevalence scanner (no-fire signal logger)

Mirrors macro_event signal logic but writes to macro_event_signals
without firing any trade. 1h cadence (lower than ladder scanner — events
are infrequent). 2-week run feeds Phase 0 gate (≥2 signals/week, EV
≥+$5/week, scrape uptime ≥99%).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: main.py registration

**Files:**
- Modify: `main.py`

- [ ] **Step 14.1: Pre-edit smoke check**

```
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && source venv/bin/activate
python -c "import main; print('OK')"
```
Expected: prints `OK`.

- [ ] **Step 14.2: Add import + instance**

In `main.py`, add to the strategy import block (near the existing `from strategies.ladder_*` lines added by A1):
```python
from strategies.macro_event import MacroEventStrategy
```

In `main.py`, after the existing `self.ladder_crypto = LadderCryptoStrategy(...)` instance creation block, append:
```python
        # Macro Event Trader (C1) — paper-only at launch.
        # Requires scripts/macro_consensus_cron.py to populate consensus
        # cache. See spec:
        # docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
        self.macro_event = MacroEventStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
```

In the `_strategy_loop` strategies list, append after the ladder_* tuples:
```python
            # Macro Event Trader (C1) — paper-only. Requires consensus cron.
            ("macro_event", self.macro_event),
```

- [ ] **Step 14.3: Smoke check after wiring**

```
python -c "import main; print('OK')"
```
Expected: `OK`.

- [ ] **Step 14.4: Add KXCPI + KXFEDDECISION to STRATEGY_SERIES if missing**

KXFEDDECISION was already added by A1 (commit 3c14ac9). Verify KXCPI is also there:
```
grep -c '"KXCPI"' core/market_scanner.py
```
Expected: 1 (already present per the existing strategy series list).

If 0, add `"KXCPI"` to the list in `core/market_scanner.py:STRATEGY_SERIES`.

- [ ] **Step 14.5: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): register macro_event sub-strategy (paper-only)

C1 Macro Event Trader wired into _strategy_loop. No entry in
LIVE_STRATEGIES env → paper-only at launch per paper-first rule.
Requires scripts/macro_consensus_cron.py to be running separately to
populate the consensus cache; without it, macro_event will skip every
market with a 'no consensus' signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

After completing all 14 tasks, perform the following checks:

**1. Spec coverage:**
- [ ] core/macro_consensus.py: scrapers + bucket helper (Tasks 2-4) ✓
- [ ] core/database.py: 2 tables + 5 helpers (Tasks 1, 7, 8) ✓
- [ ] core/order_manager.py: dedup gate prefix + cross-strategy exposure (Tasks 6, 7) ✓
- [ ] core/config.py: 8 knobs (Task 5) ✓
- [ ] strategies/macro_event.py: universe + window + edge + sizing + 9-gate orchestration (Tasks 9-11) ✓
- [ ] scripts/macro_consensus_cron.py (Task 12) ✓
- [ ] scripts/research/macro_event_signal_scanner.py (Task 13) ✓
- [ ] main.py registration (Task 14) ✓
- [ ] Spec mentions Telegram alerts + dashboard panels — NOT covered. Defer to operational follow-up after Phase 0 confirms viability.
- [ ] Spec mentions promotion_eval entry — NOT covered. Defer to Phase 1→2 transition (1-line addition).
- [ ] Spec mentions Phase 1 paper deployment + Phase 2 promotion criteria — operational, out of scope.

**2. Placeholder scan:** No "TBD" / "implement later" — every task has actual test + impl code.

**3. Type consistency:**
- `core.macro_consensus.bucket_prob_from_normal(low, high, mu, sigma)` signature consistent across Tasks 2 and 12 ✓
- `Database.record_macro_consensus(event_key, bucket, prob, source)` signature consistent across Tasks 1 and 12 ✓
- `Database.fetch_macro_consensus(event_key, bucket, source=None)` consistent across Tasks 1, 11, 13 ✓
- `Database.total_open_exposure(market_id)` consistent across Tasks 7 and 11 ✓
- `MacroEventStrategy._compute_edge` returns dict with keys `side`, `deviation`, `entry_price`, `ev_per_contract` consistent across Tasks 10 and 11 ✓
- `MacroEventStrategy._size_for_edge(deviation)` consistent across Tasks 10 and 11 ✓
- `STRATEGY_NAME = "macro_event"` constant matches everywhere ✓
- Sub-strategy ctor signature `(config, order_manager, market_scanner, kalshi_client)` matches existing pattern ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-macro-event-trader-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
