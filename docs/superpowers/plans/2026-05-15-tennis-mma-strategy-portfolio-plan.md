# Tennis/MMA Strategy Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 5 new tennis/MMA Kalshi trading strategies (A1/A2/A3/B1/C) per the [2026-05-15 design spec](../specs/2026-05-15-tennis-mma-strategy-portfolio-design.md), plus the Phase 0 replay infrastructure that validates them before paper-twin enablement.

**Architecture:** All 5 strategies extend `SportsTraderStrategy` (sport-trader base in [strategies/sports_trader.py](../../../strategies/sports_trader.py)). Subclasses override `OBSERVATION_ONLY_SPORTS`, `REGION_OVERRIDE`, price/depth/MTS gates, and `evaluate()`. The base class handles scan loop, odds fetching, Pinnacle exposure (via `self._last_pinnacle_yes_prob` / `self._last_pinnacle_age_s`), depth checks, and OrderManager dispatch. Phase 0 replay simulates each strategy against `odds_observations` history before any live capital.

**Tech Stack:** Python 3.11+, pytest, SQLite, existing `sports_trader.py` base class, `core.config.Config` env-var pattern, `core.order_manager.OrderManager` strategy registry. No new third-party deps.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `strategies/sports_tennis_pinnacle_arb_v1.py` | A1 strategy class | Create |
| `strategies/sports_atp_gs_bo5_chalk_v1.py` | A2 strategy class | Create |
| `strategies/sports_tennis_bid_imbalance_v1.py` | A3 strategy class | Create |
| `strategies/sports_ufc_chalk_fade_v1.py` | B1 strategy class | Create |
| `strategies/sports_tennis_surface_skew_v1.py` | C strategy class | Create |
| `tests/test_sports_tennis_pinnacle_arb_v1.py` | A1 unit tests | Create |
| `tests/test_sports_atp_gs_bo5_chalk_v1.py` | A2 unit tests | Create |
| `tests/test_sports_tennis_bid_imbalance_v1.py` | A3 unit tests | Create |
| `tests/test_sports_ufc_chalk_fade_v1.py` | B1 unit tests | Create |
| `tests/test_sports_tennis_surface_skew_v1.py` | C unit tests | Create |
| `scripts/tennis_mma_strategy_replay.py` | Phase 0 replay: read `odds_observations`, simulate fires, output WR/ROI/CI | Create |
| `tests/test_tennis_mma_strategy_replay.py` | Replay correctness tests | Create |
| `core/order_manager.py` | Add 5 names to `PAPER_TWIN_STRATEGIES`; add 5 to `LIMIT_AGGRESSION_CENTS_BY_STRATEGY` defensively | Modify |
| `core/config.py` | Add A1 sizing env vars (`TENNIS_PINNACLE_*`) — others use class constants | Modify |
| `main.py` | Import + instantiate 5 strategies; add to strategy loop list | Modify |
| `scripts/strategy_replay.py` | Register 5 new entries in `STRATEGIES` dict (optional — only needed if promotion_eval should replay them; reuse existing `sim_pnl`/`sim_cost`) | Modify |

Each strategy file is self-contained (~120-180 LOC). Test files mirror the strategy file 1:1.

---

## Task Sequence Overview

**Phase A (foundations):** Tasks 1-2 — replay script skeleton & test fixtures.

**Phase B (strategy implementation):** Tasks 3-7 — one task per strategy, TDD.

**Phase C (wiring):** Tasks 8-10 — order_manager, main.py, config.

**Phase D (Phase 0 replay execution):** Tasks 11-13 — run replays, gate paper-twin enablement on results.

**Phase E (deploy):** Task 14 — EC2 deploy + smoke.

**Phase F (memory):** Task 15 — save memory notes.

---

## Task 1: Replay script skeleton (`tennis_mma_strategy_replay.py`)

**Files:**
- Create: `scripts/tennis_mma_strategy_replay.py`
- Create: `tests/test_tennis_mma_strategy_replay.py`

**What it does:** Reads `odds_observations` rows for ATP/WTA/UFC within a date window, joins with settled ticker outcomes, and exposes a `simulate_strategy(name, rows, settled)` function that returns fire-by-fire P&L. Each strategy gets its own simulator that mirrors its gate logic exactly (per [feedback_observer_replay_before_gate_changes](../../memory/feedback_observer_replay_before_gate_changes.md)).

- [ ] **Step 1.1: Write failing test for `load_observations`**

```python
# tests/test_tennis_mma_strategy_replay.py
import sqlite3
import tempfile
from pathlib import Path

from scripts.tennis_mma_strategy_replay import load_observations


def _make_db(rows):
    """Helper: create an in-memory sqlite DB with odds_observations schema + given rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT, kalshi_ticker TEXT, series TEXT, sport TEXT,
            phase TEXT, minutes_to_start REAL,
            home_team TEXT, away_team TEXT, yes_sub_title TEXT,
            is_yes_home INTEGER,
            kalshi_yes_ask REAL, kalshi_no_ask REAL,
            us_yes_prob REAL, eu_yes_prob REAL, pinnacle_yes_prob REAL,
            pinnacle_age_s INTEGER, is_playoff INTEGER, is_yes_draw INTEGER,
            n_books_eu INTEGER, n_books_us INTEGER, has_pinnacle INTEGER,
            kalshi_yes_bid REAL, kalshi_no_bid REAL,
            kalshi_yes_bid_size REAL, kalshi_yes_ask_size REAL,
            kalshi_no_bid_size REAL, kalshi_no_ask_size REAL,
            kalshi_volume_24h REAL, kalshi_open_interest REAL,
            tournament_slug TEXT, starter_home TEXT, starter_away TEXT
        )
    """)
    for r in rows:
        cols = ",".join(r.keys())
        placeholders = ",".join(["?"] * len(r))
        conn.execute(f"INSERT INTO odds_observations ({cols}) VALUES ({placeholders})",
                     tuple(r.values()))
    conn.commit()
    return conn


def test_load_observations_filters_sport_and_window():
    conn = _make_db([
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-1",
         "series": "KXATPMATCH", "sport": "ATP", "phase": "pre",
         "minutes_to_start": 120.0, "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.52, "pinnacle_yes_prob": 0.60, "pinnacle_age_s": 100,
         "has_pinnacle": 1, "n_books_eu": 20, "is_yes_home": 1,
         "tournament_slug": "tennis_atp_italian_open"},
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXNBAGAME-X",
         "series": "KXNBAGAME", "sport": "NBA", "phase": "pre",
         "minutes_to_start": 60.0, "kalshi_yes_ask": 0.50, "kalshi_no_ask": 0.50,
         "eu_yes_prob": 0.50, "pinnacle_yes_prob": 0.50, "pinnacle_age_s": 100,
         "has_pinnacle": 1, "n_books_eu": 15, "is_yes_home": 1,
         "tournament_slug": None},
    ])
    rows = load_observations(conn, sports=("ATP",), start="2026-05-11", end="2026-05-15")
    assert len(rows) == 1
    assert rows[0]["sport"] == "ATP"
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py::test_load_observations_filters_sport_and_window -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.tennis_mma_strategy_replay'`.

- [ ] **Step 1.3: Create script with `load_observations`**

```python
# scripts/tennis_mma_strategy_replay.py
"""Phase 0 replay for tennis/MMA strategy portfolio (A1/A2/A3/B1/C).

Reads `odds_observations` rows, simulates each strategy's gate logic
exactly as the live class does, and outputs per-strategy fire-by-fire
P&L with WR / ROI / Wilson CI. Run before paper-twin enablement to
guard against selection-bias traps (see
memory/bid_imbalance_v2_2026-05-15.md).

Usage:
    venv/bin/python scripts/tennis_mma_strategy_replay.py \\
        --db data/bot.db --start 2026-05-11 --end 2026-05-15 \\
        --strategy tennis_pinnacle_arb_v1
"""
from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Add repo root to path for direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tennis_mma_strategy_replay")

KALSHI_FEE = 0.07  # matches core.config default


def load_observations(
    conn: sqlite3.Connection, *, sports: tuple[str, ...],
    start: str, end: str,
) -> list[dict]:
    """Return odds_observations rows for given sports within [start, end)."""
    placeholders = ",".join(["?"] * len(sports))
    sql = f"""
        SELECT * FROM odds_observations
        WHERE sport IN ({placeholders})
          AND timestamp >= ?
          AND timestamp < ?
        ORDER BY timestamp ASC
    """
    cur = conn.cursor()
    cur.execute(sql, (*sports, start, end))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 1.4: Run test to verify it passes**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py::test_load_observations_filters_sport_and_window -v
```
Expected: PASS.

- [ ] **Step 1.5: Commit**

```bash
git add scripts/tennis_mma_strategy_replay.py tests/test_tennis_mma_strategy_replay.py
git commit -m "feat(replay): tennis/MMA Phase 0 replay skeleton with load_observations"
```

---

## Task 2: Replay — settled outcome resolver + Wilson CI util

**Files:**
- Modify: `scripts/tennis_mma_strategy_replay.py`
- Modify: `tests/test_tennis_mma_strategy_replay.py`

**What it does:** Adds (a) `resolve_settled_outcomes(conn, tickers)` that joins observations to settled values (uses `kalshi_settlements` table — see `core/database.py` for schema), and (b) `wilson_ci(wins, n, z=1.96)` returning (lower, upper) bounds for win rate confidence.

- [ ] **Step 2.1: Write failing test for `wilson_ci`**

Append to `tests/test_tennis_mma_strategy_replay.py`:

```python
from scripts.tennis_mma_strategy_replay import wilson_ci


def test_wilson_ci_known_values():
    # Known: 5/10 wins, z=1.96 → CI ~[0.237, 0.763]
    lo, hi = wilson_ci(5, 10)
    assert abs(lo - 0.237) < 0.01
    assert abs(hi - 0.763) < 0.01


def test_wilson_ci_zero_n():
    lo, hi = wilson_ci(0, 0)
    assert lo == 0.0 and hi == 0.0
```

- [ ] **Step 2.2: Run test, see fail (ImportError)**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py::test_wilson_ci_known_values -v
```
Expected: FAIL with `ImportError: cannot import name 'wilson_ci'`.

- [ ] **Step 2.3: Implement `wilson_ci` and `resolve_settled_outcomes`**

Append to `scripts/tennis_mma_strategy_replay.py`:

```python
def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1.0 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def resolve_settled_outcomes(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, float]:
    """Return {ticker: settle_value in [0,1]} for tickers in kalshi_settlements.

    Kalshi binary markets settle to 1.0 (YES side wins) or 0.0 (NO side wins).
    Unknown / not-yet-settled tickers are omitted from the result.
    """
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    sql = f"""
        SELECT ticker, settle_value FROM kalshi_settlements
        WHERE ticker IN ({placeholders})
    """
    cur = conn.cursor()
    cur.execute(sql, tickers)
    return {row[0]: float(row[1]) for row in cur.fetchall()}
```

- [ ] **Step 2.4: Add settled-outcomes test**

```python
def test_resolve_settled_outcomes_returns_dict():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE kalshi_settlements (
            ticker TEXT PRIMARY KEY, settle_value REAL
        )
    """)
    conn.execute("INSERT INTO kalshi_settlements VALUES ('KXATPMATCH-1', 1.0)")
    conn.execute("INSERT INTO kalshi_settlements VALUES ('KXATPMATCH-2', 0.0)")
    conn.commit()
    out = resolve_settled_outcomes(conn, ["KXATPMATCH-1", "KXATPMATCH-2", "KXATPMATCH-3"])
    assert out == {"KXATPMATCH-1": 1.0, "KXATPMATCH-2": 0.0}
```

Add `from scripts.tennis_mma_strategy_replay import resolve_settled_outcomes` to imports.

- [ ] **Step 2.5: Run all tests, verify pass**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py -v
```
Expected: 3 passed.

- [ ] **Step 2.6: Commit**

```bash
git add scripts/tennis_mma_strategy_replay.py tests/test_tennis_mma_strategy_replay.py
git commit -m "feat(replay): add wilson_ci + resolve_settled_outcomes helpers"
```

**Note on settlement table name:** if `kalshi_settlements` is not the actual table, check via `sqlite3 data/bot.db ".tables"`. The likely candidates are `trades.settled_value` or `kalshi_settlements`. Adjust the SQL accordingly before moving on — failing test will surface the issue.

---

## Task 3: Strategy A1 — `sports_tennis_pinnacle_arb_v1.py`

**Files:**
- Create: `strategies/sports_tennis_pinnacle_arb_v1.py`
- Create: `tests/test_sports_tennis_pinnacle_arb_v1.py`

**Class signature:** `SportsTennisPinnacleArbV1Strategy(SportsTraderStrategy)`.

**Gates beyond base class:**
1. Sport must be ATP or WTA (`OBSERVATION_ONLY_SPORTS` override).
2. `pinnacle_yes_prob` available AND `pinnacle_age_s < 600`.
3. `n_books_eu` (passed via fair_prob context; see below) implicitly OK because base only calls evaluate for matched markets — but enforce `|eu_yes_prob - pinnacle_yes_prob| <= 0.03` via class attribute `MAX_EU_PINNACLE_DRIFT`.
4. `|kalshi_yes_ask - pinnacle_yes_prob| >= 0.05`.
5. Direction: BUY whichever side Pinnacle's prob exceeds Kalshi's ask.

**Sizing:** `_get_trade_size` returns `5 + round(edge*100)*1.0` capped at $30.

- [ ] **Step 3.1: Write failing unit tests**

```python
# tests/test_sports_tennis_pinnacle_arb_v1.py
from unittest.mock import MagicMock

import pytest

from strategies.sports_tennis_pinnacle_arb_v1 import SportsTennisPinnacleArbV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisPinnacleArbV1Strategy.__new__(SportsTennisPinnacleArbV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._client = MagicMock()
    s._last_pinnacle_yes_prob = None
    s._last_pinnacle_age_s = None
    s._last_eu_yes_prob = None  # set by our override (see Step 3.3)
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_pinnacle_above_kalshi_ask():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.65
    s._last_pinnacle_age_s = 100
    s._last_eu_yes_prob = 0.63
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.63,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.55
    assert 0.09 <= sig["edge"] <= 0.11  # pinnacle - kalshi_ask = 0.10


def test_fires_buy_no_when_pinnacle_below_kalshi_ask():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.35  # implies no_prob 0.65
    s._last_pinnacle_age_s = 100
    s._last_eu_yes_prob = 0.37
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.37,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "no"


def test_skips_when_gap_below_5pp():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.58
    s._last_pinnacle_age_s = 100
    s._last_eu_yes_prob = 0.57
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.57,
                     ticker="KXATPMATCH-X")
    assert sig is None  # 0.58 - 0.55 = 0.03 < 0.05


def test_skips_when_pinnacle_missing():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = None
    s._last_pinnacle_age_s = None
    s._last_eu_yes_prob = 0.50
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.50,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_stale():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.70
    s._last_pinnacle_age_s = 1000  # > 600s threshold
    s._last_eu_yes_prob = 0.68
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.68,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_eu_pinnacle_drift_too_large():
    """Pinnacle 0.70 vs eu 0.50 = 20pp drift → Pinnacle likely wrong; skip."""
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.70
    s._last_pinnacle_age_s = 100
    s._last_eu_yes_prob = 0.50  # 20pp from Pinnacle > 3pp threshold
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.50,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_size_scales_with_edge():
    s = _make_strategy()
    # edge=0.10 → 5 + 10 = $15
    assert s._size_for_edge(0.10) == 15.0
    # edge=0.30 → 5 + 30 = $35 → cap to $30
    assert s._size_for_edge(0.30) == 30.0
    # edge=0.05 (minimum gate) → 5 + 5 = $10
    assert s._size_for_edge(0.05) == 10.0
```

- [ ] **Step 3.2: Run tests, see they fail (ImportError)**

```bash
venv/bin/pytest tests/test_sports_tennis_pinnacle_arb_v1.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3.3: Implement the strategy**

```python
# strategies/sports_tennis_pinnacle_arb_v1.py
"""Tennis Pinnacle-Kalshi spread arb (A1).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Pinnacle is the sharpest tennis line. Kalshi tennis tickers are illiquid
in the 1-3 day pre-match window, allowing retail drift away from Pinnacle.
We arb the gap when |Kalshi_yes_ask - Pinnacle_yes_prob| >= 0.05 AND eu
consensus confirms Pinnacle is sane (within 3pp).

Paper-twin only at launch — no LIVE_STRATEGIES env entry.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_pinnacle_arb_v1")


class SportsTennisPinnacleArbV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_pinnacle_arb_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_pinnacle_arb_v1"
    BADGE_LABEL = "A1"
    BADGE_COLOR = "#60a5fa"
    REGION_OVERRIDE = "eu"

    # ATP + WTA only — strip them from observation-only set so evaluate() runs.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0  # 3 days
    MIN_DEPTH_USDC = 50.0
    MIN_ENTRY_PRICE = 0.10
    MAX_ENTRY_PRICE = 0.90

    # Strategy-specific thresholds
    MIN_KALSHI_PINN_GAP = 0.05
    MAX_EU_PINNACLE_DRIFT = 0.03
    MAX_PINNACLE_AGE_S = 600

    def _size_for_edge(self, edge: float) -> float:
        """Edge-scaled sizing: $5 base + $1 per pp of edge, cap $30."""
        size = 5.0 + round(edge * 100) * 1.0
        return min(size, 30.0)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        # Gate: Pinnacle present + fresh
        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None

        # Gate: eu consensus confirms Pinnacle is sane
        eu = getattr(self, "_last_eu_yes_prob", None)
        if eu is None:
            eu = fair_prob  # fair_prob IS the eu consensus per sports_trader.py
        if abs(eu - pin) > self.MAX_EU_PINNACLE_DRIFT:
            return None

        # Gate: gap large enough on either side
        gap_yes = pin - kalshi_yes   # positive → Pinnacle says BUY YES
        gap_no = (1.0 - pin) - kalshi_no  # positive → Pinnacle says BUY NO

        if gap_yes >= self.MIN_KALSHI_PINN_GAP and gap_yes >= gap_no:
            return {
                "side": "yes",
                "price": kalshi_yes,
                "edge": gap_yes,
                "strategy": self.STRATEGY_NAME,
                "ticker": ticker,
            }
        if gap_no >= self.MIN_KALSHI_PINN_GAP:
            return {
                "side": "no",
                "price": kalshi_no,
                "edge": gap_no,
                "strategy": self.STRATEGY_NAME,
                "ticker": ticker,
            }
        return None

    def _get_trade_size(self, signal: dict) -> float:
        return self._size_for_edge(signal.get("edge", 0.0))
```

- [ ] **Step 3.4: Run tests, verify pass**

```bash
venv/bin/pytest tests/test_sports_tennis_pinnacle_arb_v1.py -v
```
Expected: 7 passed.

- [ ] **Step 3.5: Commit**

```bash
git add strategies/sports_tennis_pinnacle_arb_v1.py tests/test_sports_tennis_pinnacle_arb_v1.py
git commit -m "feat(strategies): A1 tennis_pinnacle_arb_v1 — Kalshi-Pinnacle spread arb"
```

**Note on `_last_eu_yes_prob`:** The base class exposes `_last_pinnacle_yes_prob` but not eu separately — `fair_prob` is already the eu consensus. Our tests pass `_last_eu_yes_prob` via attribute injection for clarity, but `evaluate()` falls back to `fair_prob` if the attribute is unset (which is the production path). This is intentional and documented in the code.

---

## Task 4: Strategy A2 — `sports_atp_gs_bo5_chalk_v1.py`

**Files:**
- Create: `strategies/sports_atp_gs_bo5_chalk_v1.py`
- Create: `tests/test_sports_atp_gs_bo5_chalk_v1.py`

**Class:** `SportsAtpGsBo5ChalkV1Strategy(SportsTraderStrategy)`.

**Gates beyond base class:**
1. Sport: ATP only.
2. `TENNIS_SLUG_META[tournament_slug]["format"] == "BO5"` AND `tier == "GS"`.
3. `parse_tennis_round(yes_sub_title)` ∈ {"R128", "R64", "R32"}.
4. Price band 0.70-0.85 on YES side only.
5. `pinnacle_yes_prob >= 0.72`.

**Implementation note:** The strategy needs `tournament_slug` and `yes_sub_title`. Base class doesn't pass these to `evaluate()`. We attach them via `self._last_tournament_slug` / `self._last_yes_sub_title` — but `sports_trader.py` doesn't set these today. Two options:

- **Option A (chosen):** Read `yes_sub_title` from the current market in scan loop and stash on `self`. Requires a base-class patch.
- **Option B:** Look up the slug/title from the active markets cache inside `evaluate()`.

For minimal change, use Option B — call `self._scanner.get_market_by_ticker(ticker)` inside `evaluate()`. Cache lookup is cheap and existing strategies (e.g. `sports_soccer_underdog_v1` uses `self._client.get_order_book_depth`) follow this pattern.

- [ ] **Step 4.1: Write failing unit tests**

```python
# tests/test_sports_atp_gs_bo5_chalk_v1.py
from unittest.mock import MagicMock

from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy


def _make_strategy(slug="tennis_atp_french_open", title="Will Sinner win the Sinner vs Nadal: Round Of 64 match?"):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsAtpGsBo5ChalkV1Strategy.__new__(SportsAtpGsBo5ChalkV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.78
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    s._last_tournament_slug = slug
    s._last_yes_sub_title = title
    return s


def test_fires_on_french_open_r64_chalk():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_skips_us_open_qf_round():
    # QF not in {R128, R64, R32}
    s = _make_strategy(title="Will Alcaraz win the Alcaraz vs Djokovic: Quarterfinal match?")
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_bo3_atp_1000():
    # Italian Open is BO3, not BO5 → skip even at R64.
    s = _make_strategy(slug="tennis_atp_italian_open",
                       title="Will X win the X vs Y: Round Of 64 match?")
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_below_price_band():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.65, kalshi_no=0.35, fair_prob=0.66,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_above_price_band():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.90, kalshi_no=0.10, fair_prob=0.90,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.68  # below 0.72 floor
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.68,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_slug_missing():
    s = _make_strategy(slug=None)
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None
```

- [ ] **Step 4.2: Run, fail (ImportError)**

```bash
venv/bin/pytest tests/test_sports_atp_gs_bo5_chalk_v1.py -v
```
Expected: FAIL.

- [ ] **Step 4.3: Implement**

```python
# strategies/sports_atp_gs_bo5_chalk_v1.py
"""ATP Grand Slam BO5 chalk-hold (A2).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

BO5 reduces variance for the favorite. Top ATP seeds at -300 win
~85-88% in BO5 vs ~78% in BO3-equivalent lines. Kalshi inherits BO3
mental model — we BUY YES on favorites in early rounds where the seed
advantage is largest.

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import (
    SportsTraderStrategy, TENNIS_SLUG_META, parse_tennis_round,
)

logger = logging.getLogger("sports_atp_gs_bo5_chalk_v1")


class SportsAtpGsBo5ChalkV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_atp_gs_bo5_chalk_v1_tickers.json"
    STRATEGY_NAME = "sports_atp_gs_bo5_chalk_v1"
    BADGE_LABEL = "A2"
    BADGE_COLOR = "#a78bfa"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP"}
    )

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0
    MIN_DEPTH_USDC = 30.0
    MIN_ENTRY_PRICE = 0.70
    MAX_ENTRY_PRICE = 0.85

    ELIGIBLE_ROUNDS = frozenset({"R128", "R64", "R32"})
    MIN_PINNACLE_CONFIRM = 0.72

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        slug = getattr(self, "_last_tournament_slug", None)
        if slug is None:
            return None
        meta = TENNIS_SLUG_META.get(slug)
        if meta is None or meta.get("format") != "BO5" or meta.get("tier") != "GS":
            return None

        title = getattr(self, "_last_yes_sub_title", None)
        round_code = parse_tennis_round(title or "")
        if round_code not in self.ELIGIBLE_ROUNDS:
            return None

        if not (self.MIN_ENTRY_PRICE <= kalshi_yes <= self.MAX_ENTRY_PRICE):
            return None

        pin = self._last_pinnacle_yes_prob
        if pin is None or pin < self.MIN_PINNACLE_CONFIRM:
            return None

        return {
            "side": "yes",
            "price": kalshi_yes,
            "edge": pin - kalshi_yes,
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(6.0, self._order_manager.bankroll * 0.02)
```

- [ ] **Step 4.4: Verify pass**

```bash
venv/bin/pytest tests/test_sports_atp_gs_bo5_chalk_v1.py -v
```
Expected: 7 passed.

- [ ] **Step 4.5: Commit**

```bash
git add strategies/sports_atp_gs_bo5_chalk_v1.py tests/test_sports_atp_gs_bo5_chalk_v1.py
git commit -m "feat(strategies): A2 atp_gs_bo5_chalk_v1 — GS BO5 early-round chalk hold"
```

**Note on base-class wiring for `_last_tournament_slug` / `_last_yes_sub_title`:** Task 8 (main.py wiring) includes a base-class patch in `sports_trader.py` to stash these attributes during scan. The evaluate() guards against `None` so tests pass even before the wiring is in place — but the strategy won't fire in production until Task 8 ships.

---

## Task 5: Strategy A3 — `sports_tennis_bid_imbalance_v1.py`

**Files:**
- Create: `strategies/sports_tennis_bid_imbalance_v1.py`
- Create: `tests/test_sports_tennis_bid_imbalance_v1.py`

**Class:** `SportsTennisBidImbalanceV1Strategy(SportsTraderStrategy)`.

**Gates:**
1. Sport: ATP or WTA.
2. Window: mts ∈ [60, 1440] (1-24h, intraday only).
3. Compute `imbalance = yes_bid_size / (yes_bid_size + no_bid_size)` from Kalshi book; skip if both sizes zero.
4. BUY YES if `imbalance > 0.65` AND `yes_ask ∈ [0.50, 0.85]` AND Pinnacle agrees (`pinnacle_yes_prob > yes_ask`).
5. BUY NO if `imbalance < 0.35` AND `yes_ask ∈ [0.15, 0.50]` AND Pinnacle agrees (`pinnacle_yes_prob < yes_ask`).

**Bid sizes:** Fetched via `self._client.get_orderbook_top_of_book(ticker)` returning `(yes_bid_size, no_bid_size, yes_bid, no_bid)`. If that helper doesn't exist, use `self._client.get_order_book_depth(ticker)` (already used by `sports_soccer_underdog_v1`) and adapt — the helper returns depth-USDC, but we need raw size. Check existing client API in `core/kalshi_client.py` before implementing.

- [ ] **Step 5.1: Inspect existing client API**

```bash
grep -n "def get_order_book\|def get_orderbook" core/kalshi_client.py | head
```

If `get_order_book_depth(ticker)` returns USDC-denominated depth (`price × size`), we need raw size. Either:
- Add a new `get_top_of_book_sizes(ticker)` returning `(yb_size, ya_size, nb_size, na_size)`. OR
- Compute `bid_size = bid_depth / bid_price` inside `evaluate()` — same information.

Choose the second (no client API change). Document the derivation.

- [ ] **Step 5.2: Write failing unit tests**

```python
# tests/test_sports_tennis_bid_imbalance_v1.py
from unittest.mock import MagicMock

from strategies.sports_tennis_bid_imbalance_v1 import SportsTennisBidImbalanceV1Strategy


def _make_strategy(yes_bid_size=100.0, no_bid_size=50.0, mts=300.0):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisBidImbalanceV1Strategy.__new__(SportsTennisBidImbalanceV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._client = MagicMock()
    # Mock orderbook: returns (yes_bid_size, yes_ask_size, no_bid_size, no_ask_size)
    s._client.get_top_of_book_sizes = MagicMock(
        return_value=(yes_bid_size, 100.0, no_bid_size, 100.0)
    )
    s._last_pinnacle_yes_prob = 0.70
    s._last_pinnacle_age_s = 100
    s._last_mts = mts
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_imbalance_above_65pct_and_yes_band():
    # yes_bid_size=200, no_bid_size=50 → imbalance 0.80
    s = _make_strategy(yes_bid_size=200.0, no_bid_size=50.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_fires_buy_no_when_imbalance_below_35pct_and_no_band():
    # yes_bid_size=20, no_bid_size=200 → imbalance 0.09
    s = _make_strategy(yes_bid_size=20.0, no_bid_size=200.0)
    s._last_pinnacle_yes_prob = 0.25  # Pinnacle agrees NO side
    sig = s.evaluate(kalshi_yes=0.30, kalshi_no=0.70, fair_prob=0.25,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "no"


def test_skips_when_imbalance_neutral():
    s = _make_strategy(yes_bid_size=100.0, no_bid_size=100.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.55,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_outside_intraday_window():
    s = _make_strategy(mts=2000.0)  # > 1440
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees_with_imbalance_direction():
    # Imbalance says BUY YES but Pinnacle says NO side (0.40 < 0.60 ask)
    s = _make_strategy(yes_bid_size=200.0, no_bid_size=50.0)
    s._last_pinnacle_yes_prob = 0.40
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.40,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_both_bid_sizes_zero():
    s = _make_strategy(yes_bid_size=0.0, no_bid_size=0.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is None
```

- [ ] **Step 5.3: Run, fail**

```bash
venv/bin/pytest tests/test_sports_tennis_bid_imbalance_v1.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 5.4: Implement**

```python
# strategies/sports_tennis_bid_imbalance_v1.py
"""Tennis bid-imbalance signal (A3).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Top-of-book bid-size skew as a settlement-direction predictor. Validated
on crypto observers (Phase 1 bid_imbalance_v1, pre-revision 18/26=69%
WR). Tennis adds 21+ book eu consensus + Pinnacle confirm.

Mandatory Phase 0 gate: replay must show top-decile WR ≥ baseline + 6pp
on full universe before paper-twin enable. Guards against
bid_imbalance_v2 selection-bias trap (memory/bid_imbalance_v2_2026-05-15).

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_bid_imbalance_v1")


class SportsTennisBidImbalanceV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_bid_imbalance_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_bid_imbalance_v1"
    BADGE_LABEL = "A3"
    BADGE_COLOR = "#34d399"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 1440.0  # intraday only
    MIN_MTS_AT_ENTRY = 60.0
    MIN_DEPTH_USDC = 20.0

    IMBALANCE_YES_THRESHOLD = 0.65
    IMBALANCE_NO_THRESHOLD = 0.35
    YES_PRICE_BAND = (0.50, 0.85)
    NO_PRICE_BAND = (0.15, 0.50)

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        # Window guard (intraday only). Base class handles MAX_MTS via class
        # attribute; we also need MIN_MTS for this strategy.
        mts = getattr(self, "_last_mts", None)
        if mts is None or mts < self.MIN_MTS_AT_ENTRY:
            return None

        pin = self._last_pinnacle_yes_prob
        if pin is None or self._last_pinnacle_age_s is None:
            return None

        try:
            yb, _ya, nb, _na = self._client.get_top_of_book_sizes(ticker)
        except (AttributeError, Exception) as e:
            logger.debug(f"[{self.STRATEGY_NAME}] {ticker}: orderbook fetch failed: {e}")
            return None
        if (yb + nb) <= 0:
            return None
        imbalance = yb / (yb + nb)

        # BUY YES branch
        if imbalance > self.IMBALANCE_YES_THRESHOLD:
            lo, hi = self.YES_PRICE_BAND
            if lo <= kalshi_yes <= hi and pin > kalshi_yes:
                return {
                    "side": "yes",
                    "price": kalshi_yes,
                    "edge": imbalance,
                    "strategy": self.STRATEGY_NAME,
                    "ticker": ticker,
                }

        # BUY NO branch
        if imbalance < self.IMBALANCE_NO_THRESHOLD:
            lo, hi = self.NO_PRICE_BAND
            if lo <= kalshi_yes <= hi and pin < kalshi_yes:
                return {
                    "side": "no",
                    "price": kalshi_no,
                    "edge": 1.0 - imbalance,
                    "strategy": self.STRATEGY_NAME,
                    "ticker": ticker,
                }

        return None

    def _get_trade_size(self, signal: dict) -> float:
        return min(3.0, self._order_manager.bankroll * 0.02)
```

- [ ] **Step 5.5: Verify pass**

```bash
venv/bin/pytest tests/test_sports_tennis_bid_imbalance_v1.py -v
```
Expected: 6 passed.

- [ ] **Step 5.6: Commit**

```bash
git add strategies/sports_tennis_bid_imbalance_v1.py tests/test_sports_tennis_bid_imbalance_v1.py
git commit -m "feat(strategies): A3 tennis_bid_imbalance_v1 — book-skew predictor"
```

**Note on `get_top_of_book_sizes`:** Task 8 (wiring) adds this method to `core/kalshi_client.py` if it doesn't exist yet (current code has `get_order_book_depth` returning USDC). The strategy `evaluate()` catches `AttributeError` so tests pass even before the client method exists.

**Note on `_last_mts`:** Same pattern as `_last_tournament_slug` — Task 8 stashes `minutes_to_start` from the matched game in the base class scan loop.

---

## Task 6: Strategy B1 — `sports_ufc_chalk_fade_v1.py`

**Files:**
- Create: `strategies/sports_ufc_chalk_fade_v1.py`
- Create: `tests/test_sports_ufc_chalk_fade_v1.py`

**Class:** `SportsUfcChalkFadeV1Strategy(SportsTraderStrategy)`.

**Gates:**
1. Sport: UFC only.
2. `kalshi_yes_ask >= 0.78` (heavy chalk).
3. `pinnacle_yes_prob >= 0.78` (sharp confirms chalk).
4. `pinnacle_age_s < 600`.
5. Direction: BUY NO (fade).

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_sports_ufc_chalk_fade_v1.py
from unittest.mock import MagicMock

from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsUfcChalkFadeV1Strategy.__new__(SportsUfcChalkFadeV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.82
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    return s


def test_fires_buy_no_when_both_kalshi_and_pinnacle_chalk():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.20


def test_skips_below_chalk_threshold():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.75,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.70  # Pinnacle says not chalk
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.70,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_stale():
    s = _make_strategy()
    s._last_pinnacle_age_s = 1000  # > 600s
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_missing():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = None
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.50,
                     ticker="KXUFCFIGHT-X")
    assert sig is None
```

- [ ] **Step 6.2: Run, fail**

```bash
venv/bin/pytest tests/test_sports_ufc_chalk_fade_v1.py -v
```
Expected: FAIL.

- [ ] **Step 6.3: Implement**

```python
# strategies/sports_ufc_chalk_fade_v1.py
"""UFC heavy-chalk fade (B1).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

MMA single-shot variance — heavy chalk (>= 0.78 implied) historically
underperforms by 3-5pp due to KO/submission risk. We BUY NO when both
Kalshi and Pinnacle price the favorite >= 0.78.

Paper-twin only at launch. Promotion gate relaxed (n>=15) per slow
card cadence (~4-8 trades/month).
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_ufc_chalk_fade_v1")


class SportsUfcChalkFadeV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_ufc_chalk_fade_v1_tickers.json"
    STRATEGY_NAME = "sports_ufc_chalk_fade_v1"
    BADGE_LABEL = "B1"
    BADGE_COLOR = "#f87171"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"UFC"}
    )

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0
    MIN_DEPTH_USDC = 20.0  # UFC books thinner
    MIN_ENTRY_PRICE = 0.78  # heavy chalk only

    MIN_PINNACLE_CONFIRM = 0.78
    MAX_PINNACLE_AGE_S = 600

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        if kalshi_yes < self.MIN_ENTRY_PRICE:
            return None
        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None
        if pin < self.MIN_PINNACLE_CONFIRM:
            return None
        return {
            "side": "no",
            "price": kalshi_no,
            "edge": kalshi_yes - (1.0 - 0.78),  # symbolic, used for logging only
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(2.0, self._order_manager.bankroll * 0.02)
```

- [ ] **Step 6.4: Verify pass**

```bash
venv/bin/pytest tests/test_sports_ufc_chalk_fade_v1.py -v
```
Expected: 5 passed.

- [ ] **Step 6.5: Commit**

```bash
git add strategies/sports_ufc_chalk_fade_v1.py tests/test_sports_ufc_chalk_fade_v1.py
git commit -m "feat(strategies): B1 ufc_chalk_fade_v1 — heavy UFC chalk fade"
```

---

## Task 7: Strategy C — `sports_tennis_surface_skew_v1.py`

**Files:**
- Create: `strategies/sports_tennis_surface_skew_v1.py`
- Create: `tests/test_sports_tennis_surface_skew_v1.py`

**Class:** `SportsTennisSurfaceSkewV1Strategy(SportsTraderStrategy)`.

**Gates:**
1. Sport: ATP or WTA.
2. `TENNIS_SLUG_META[tournament_slug]["surface"] in {"clay", "grass"}`.
3. Compute `surface_skew = pinnacle_yes_prob - fair_prob` (fair_prob = eu consensus).
4. `|surface_skew| >= 0.04`.
5. Price band 0.20-0.80 on Pinnacle's preferred side.

- [ ] **Step 7.1: Write failing tests**

```python
# tests/test_sports_tennis_surface_skew_v1.py
from unittest.mock import MagicMock

from strategies.sports_tennis_surface_skew_v1 import SportsTennisSurfaceSkewV1Strategy


def _make_strategy(surface="clay", slug="tennis_atp_french_open"):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisSurfaceSkewV1Strategy.__new__(SportsTennisSurfaceSkewV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.65  # Pinnacle higher than eu
    s._last_pinnacle_age_s = 100
    s._last_tournament_slug = slug
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_pinnacle_higher_on_clay():
    s = _make_strategy()
    # Pinnacle 0.65, eu (fair_prob) 0.58 → skew +0.07 → BUY YES
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_fires_buy_no_when_pinnacle_lower_on_grass():
    s = _make_strategy(slug="tennis_atp_wimbledon")
    s._last_pinnacle_yes_prob = 0.40
    # Pinnacle 0.40, eu 0.50 → skew -0.10 → BUY NO
    sig = s.evaluate(kalshi_yes=0.50, kalshi_no=0.50, fair_prob=0.50,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "no"


def test_skips_hard_court():
    s = _make_strategy(slug="tennis_atp_aus_open_singles")  # hard
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_skew_below_4pp():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.60  # eu 0.58 → skew +0.02
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_outside_price_band():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.90
    sig = s.evaluate(kalshi_yes=0.85, kalshi_no=0.15, fair_prob=0.85,
                     ticker="KXATPMATCH-X")
    assert sig is None  # 0.85 > 0.80 upper band


def test_skips_unknown_slug():
    s = _make_strategy(slug="tennis_random_unknown_event")
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None
```

- [ ] **Step 7.2: Run, fail**

```bash
venv/bin/pytest tests/test_sports_tennis_surface_skew_v1.py -v
```
Expected: FAIL.

- [ ] **Step 7.3: Implement**

```python
# strategies/sports_tennis_surface_skew_v1.py
"""Tennis surface-skew Pinnacle-vs-eu (C).

Spec: docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md

Pinnacle prices surface specialization (clay/grass) more aggressively
than other books. When Pinnacle diverges from eu consensus on a clay
or grass match, that gap is Pinnacle's surface-aware lean. We follow
it before Kalshi adjusts.

Distinct from A1 (which uses Kalshi-Pinnacle gap). Hard-court excluded
as surface-neutral.

Paper-twin only at launch.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy, TENNIS_SLUG_META

logger = logging.getLogger("sports_tennis_surface_skew_v1")


class SportsTennisSurfaceSkewV1Strategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_tennis_surface_skew_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_surface_skew_v1"
    BADGE_LABEL = "C"
    BADGE_COLOR = "#fbbf24"
    REGION_OVERRIDE = "eu"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )

    PRE_GAME_ONLY = True
    MAX_MTS_AT_ENTRY = 4320.0
    MIN_DEPTH_USDC = 30.0
    MIN_ENTRY_PRICE = 0.20
    MAX_ENTRY_PRICE = 0.80

    ELIGIBLE_SURFACES = frozenset({"clay", "grass"})
    MIN_SURFACE_SKEW = 0.04
    MAX_PINNACLE_AGE_S = 600

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        slug = getattr(self, "_last_tournament_slug", None)
        if slug is None:
            return None
        meta = TENNIS_SLUG_META.get(slug)
        if meta is None or meta.get("surface") not in self.ELIGIBLE_SURFACES:
            return None

        pin = self._last_pinnacle_yes_prob
        age = self._last_pinnacle_age_s
        if pin is None or age is None or age > self.MAX_PINNACLE_AGE_S:
            return None

        skew = pin - fair_prob  # Pinnacle vs eu consensus
        if abs(skew) < self.MIN_SURFACE_SKEW:
            return None

        if skew > 0:
            side, price = "yes", kalshi_yes
        else:
            side, price = "no", kalshi_no

        if not (self.MIN_ENTRY_PRICE <= price <= self.MAX_ENTRY_PRICE):
            return None

        return {
            "side": side,
            "price": price,
            "edge": abs(skew),
            "strategy": self.STRATEGY_NAME,
            "ticker": ticker,
        }

    def _get_trade_size(self, signal: dict) -> float:
        return min(4.0, self._order_manager.bankroll * 0.02)
```

- [ ] **Step 7.4: Verify pass**

```bash
venv/bin/pytest tests/test_sports_tennis_surface_skew_v1.py -v
```
Expected: 6 passed.

- [ ] **Step 7.5: Commit**

```bash
git add strategies/sports_tennis_surface_skew_v1.py tests/test_sports_tennis_surface_skew_v1.py
git commit -m "feat(strategies): C tennis_surface_skew_v1 — Pinnacle-eu surface gap"
```

---

## Task 8: Base-class wiring for `_last_tournament_slug`, `_last_yes_sub_title`, `_last_mts` + `get_top_of_book_sizes`

**Files:**
- Modify: `strategies/sports_trader.py` (scan loop)
- Modify: `core/kalshi_client.py` (add helper if missing)
- Create: `tests/test_sports_trader_attr_wiring.py`

**What it does:** A2 / A3 / C reach into base-class attributes that don't exist today. This task adds them. Tests verify the scan loop populates them on each evaluate() call.

- [ ] **Step 8.1: Inspect current `scan_and_execute` for the right insertion point**

```bash
sed -n '1440,1470p' strategies/sports_trader.py
```

The insertion point is just before `signal = self.evaluate(...)` at line 1462. Pinnacle attributes are already set above; we add slug/title/mts in the same block.

- [ ] **Step 8.2: Write failing test for attribute stashing**

```python
# tests/test_sports_trader_attr_wiring.py
"""Verify scan_and_execute stashes per-market context for subclasses."""
from unittest.mock import MagicMock

import pytest

from strategies.sports_trader import SportsTraderStrategy


class _CaptureStrategy(SportsTraderStrategy):
    """Test double — captures attribute snapshot at evaluate() time."""
    STRATEGY_NAME = "capture_test"
    OBSERVATION_ONLY_SPORTS = frozenset()

    def __init__(self):
        # Bypass parent __init__ requirements
        self._captured = {}
        self._traded_tickers = {}

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, **kwargs):
        self._captured = {
            "slug": getattr(self, "_last_tournament_slug", "<unset>"),
            "title": getattr(self, "_last_yes_sub_title", "<unset>"),
            "mts": getattr(self, "_last_mts", "<unset>"),
        }
        return None


def test_attributes_stashed_per_market():
    """The base scan loop should populate _last_tournament_slug, _last_yes_sub_title,
    _last_mts from the matched market/game before calling evaluate()."""
    # This test asserts the contract; full integration tested via paper-twin
    # observation post-deploy. Here we directly invoke the stash helper.
    s = _CaptureStrategy()
    market = {
        "ticker": "KXATPMATCH-X",
        "tournament_slug": "tennis_atp_french_open",
        "yes_sub_title": "Will X win the X vs Y: Round Of 64 match?",
    }
    game = {"commence_time": "2026-06-01T12:00:00+00:00"}
    # Helper sets the attributes from the current market+game pair.
    s._stash_market_context(market, game)
    assert s._last_tournament_slug == "tennis_atp_french_open"
    assert "Round Of 64" in s._last_yes_sub_title
    assert s._last_mts is not None  # actual value depends on now() vs commence_time
```

- [ ] **Step 8.3: Run, see fail (`_stash_market_context` missing)**

```bash
venv/bin/pytest tests/test_sports_trader_attr_wiring.py -v
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 8.4: Add `_stash_market_context` to `SportsTraderStrategy`**

Find an empty line just before the `def evaluate` definition in `sports_trader.py` (around line 1155-1160) and insert:

```python
    def _stash_market_context(self, market: dict, game: Optional[dict]) -> None:
        """Stash per-market context attributes used by subclasses' evaluate().

        Called by scan_and_execute() immediately before evaluate(). Subclasses
        read these as self._last_tournament_slug / _last_yes_sub_title / _last_mts.
        """
        self._last_tournament_slug = market.get("tournament_slug")
        self._last_yes_sub_title = market.get("yes_sub_title")
        if game is not None:
            self._last_mts = _commence_time_to_minutes(game.get("commence_time"))
        else:
            self._last_mts = None
```

Then in `scan_and_execute`, just before line 1462 (`signal = self.evaluate(...)`), add:

```python
                self._stash_market_context(market, matched_game)
```

- [ ] **Step 8.5: Verify test passes**

```bash
venv/bin/pytest tests/test_sports_trader_attr_wiring.py -v
```
Expected: PASS.

- [ ] **Step 8.6: Add `get_top_of_book_sizes` to KalshiClient**

```bash
grep -n "def get_order_book_depth\|def get_orderbook" core/kalshi_client.py
```

If `get_top_of_book_sizes` does not exist, append to `core/kalshi_client.py`:

```python
    def get_top_of_book_sizes(self, ticker: str) -> tuple[float, float, float, float]:
        """Return (yes_bid_size, yes_ask_size, no_bid_size, no_ask_size).

        Sizes are raw contract counts (NOT USDC-denominated, unlike
        get_order_book_depth). Used by bid-imbalance strategies that
        need symmetric YES/NO bid size comparison.

        Returns (0, 0, 0, 0) on any fetch error.
        """
        try:
            ob = self.get_orderbook(ticker)
            if not ob:
                return (0.0, 0.0, 0.0, 0.0)
            yb = float(ob.get("yes", {}).get("bid_size", 0) or 0)
            ya = float(ob.get("yes", {}).get("ask_size", 0) or 0)
            nb = float(ob.get("no", {}).get("bid_size", 0) or 0)
            na = float(ob.get("no", {}).get("ask_size", 0) or 0)
            return (yb, ya, nb, na)
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)
```

Adjust the `ob.get(...)` extraction to match the actual Kalshi orderbook payload shape (check existing `get_order_book_depth` for the exact path).

- [ ] **Step 8.7: Run the full A2/A3/C test suites — they should still pass**

```bash
venv/bin/pytest tests/test_sports_atp_gs_bo5_chalk_v1.py tests/test_sports_tennis_bid_imbalance_v1.py tests/test_sports_tennis_surface_skew_v1.py -v
```
Expected: 19 passed (7+6+6).

- [ ] **Step 8.8: Commit**

```bash
git add strategies/sports_trader.py core/kalshi_client.py tests/test_sports_trader_attr_wiring.py
git commit -m "feat(sports_trader): stash market context + top-of-book sizes helper"
```

---

## Task 9: Register strategies in `order_manager.py`

**Files:**
- Modify: `core/order_manager.py`

**What it does:** Adds the 5 new strategy names to `PAPER_TWIN_STRATEGIES` (paper twin recording) and `LIMIT_AGGRESSION_CENTS_BY_STRATEGY` (defensive 25¢ aggression, matching `sports_soccer_underdog_v1`/`v36`/`v37` pattern for thin-book / new launches).

- [ ] **Step 9.1: Find the existing entries**

```bash
grep -n "sports_underdog_v37" core/order_manager.py
```

- [ ] **Step 9.2: Append to `PAPER_TWIN_STRATEGIES` (around line 124)**

Edit `core/order_manager.py`, find the `PAPER_TWIN_STRATEGIES = { ... "sports_underdog_v37", ... }` block, append:

```python
    "sports_tennis_pinnacle_arb_v1",  # 2026-05-15 A1 paper-twin launch
    "sports_atp_gs_bo5_chalk_v1",     # 2026-05-15 A2 paper-twin launch
    "sports_tennis_bid_imbalance_v1", # 2026-05-15 A3 paper-twin launch
    "sports_ufc_chalk_fade_v1",       # 2026-05-15 B1 paper-twin launch
    "sports_tennis_surface_skew_v1",  # 2026-05-15 C paper-twin launch
```

- [ ] **Step 9.3: Append to `LIMIT_AGGRESSION_CENTS_BY_STRATEGY` (around line 85)**

In the same file, find the existing `LIMIT_AGGRESSION_CENTS_BY_STRATEGY = { ... }` and append:

```python
    "sports_tennis_pinnacle_arb_v1": 25,    # thin-book tennis defensive
    "sports_atp_gs_bo5_chalk_v1": 25,
    "sports_tennis_bid_imbalance_v1": 25,
    "sports_ufc_chalk_fade_v1": 25,         # UFC slow-book defensive
    "sports_tennis_surface_skew_v1": 25,
```

- [ ] **Step 9.4: Run existing order_manager tests to verify no regression**

```bash
venv/bin/pytest tests/test_cross_strategy_exposure.py tests/test_dedup_rollback.py -v
```
Expected: all pre-existing tests still pass.

- [ ] **Step 9.5: Commit**

```bash
git add core/order_manager.py
git commit -m "feat(order_manager): register 5 tennis/MMA strategies for paper twin"
```

---

## Task 10: Wire strategies into `main.py`

**Files:**
- Modify: `main.py`

**What it does:** Import the 5 classes, instantiate in `__init__`, add to `_strategy_loop` scan list.

- [ ] **Step 10.1: Add imports near the top of main.py**

Find the existing block of strategy imports (around `from strategies.sports_soccer_underdog_v1 import ...`), add:

```python
from strategies.sports_tennis_pinnacle_arb_v1 import SportsTennisPinnacleArbV1Strategy
from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy
from strategies.sports_tennis_bid_imbalance_v1 import SportsTennisBidImbalanceV1Strategy
from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy
from strategies.sports_tennis_surface_skew_v1 import SportsTennisSurfaceSkewV1Strategy
```

- [ ] **Step 10.2: Instantiate in `__init__` (around line 186)**

Find where existing sport strategies are instantiated and add after `self.sports_soccer_underdog_v1`:

```python
        self.sports_tennis_pinnacle_arb_v1 = SportsTennisPinnacleArbV1Strategy(
            self.config, self.order_manager, self.market_scanner, self.kalshi_client
        )
        self.sports_atp_gs_bo5_chalk_v1 = SportsAtpGsBo5ChalkV1Strategy(
            self.config, self.order_manager, self.market_scanner, self.kalshi_client
        )
        self.sports_tennis_bid_imbalance_v1 = SportsTennisBidImbalanceV1Strategy(
            self.config, self.order_manager, self.market_scanner, self.kalshi_client
        )
        self.sports_ufc_chalk_fade_v1 = SportsUfcChalkFadeV1Strategy(
            self.config, self.order_manager, self.market_scanner, self.kalshi_client
        )
        self.sports_tennis_surface_skew_v1 = SportsTennisSurfaceSkewV1Strategy(
            self.config, self.order_manager, self.market_scanner, self.kalshi_client
        )
```

**Verify constructor signature:** Run

```bash
grep -n "def __init__" strategies/sports_underdog_v31.py strategies/sports_soccer_underdog_v1.py strategies/sports_trader.py | head
```

and confirm the call matches the actual `SportsTraderStrategy.__init__` arity. If it differs, copy the pattern from `self.sports_soccer_underdog_v1 = ...` verbatim.

- [ ] **Step 10.3: Add to scan list in `_strategy_loop` (around line 445)**

Find the `strategies = [...]` tuple list and add after `("sports_soccer_draw_v1", self.sports_soccer_draw_v1),`:

```python
            # 2026-05-15 tennis/MMA portfolio — paper-twin only at launch.
            ("sports_tennis_pinnacle_arb_v1", self.sports_tennis_pinnacle_arb_v1),
            ("sports_atp_gs_bo5_chalk_v1",    self.sports_atp_gs_bo5_chalk_v1),
            ("sports_tennis_bid_imbalance_v1", self.sports_tennis_bid_imbalance_v1),
            ("sports_ufc_chalk_fade_v1",      self.sports_ufc_chalk_fade_v1),
            ("sports_tennis_surface_skew_v1", self.sports_tennis_surface_skew_v1),
```

- [ ] **Step 10.4: Import-time smoke check**

```bash
venv/bin/python -c "from main import Bot; print('OK')"
```
Expected: `OK` (no ImportError, no syntax error).

- [ ] **Step 10.5: Run the full pytest suite**

```bash
venv/bin/pytest tests/ -x -q
```
Expected: All tests pass. If a pre-existing test fails, investigate before proceeding — likely the base-class patch broke an assumption.

- [ ] **Step 10.6: Commit**

```bash
git add main.py
git commit -m "feat(main): wire 5 tennis/MMA strategies into bot loop"
```

---

## Task 11: Phase 0 replay — implement simulators for A1, B1, C

**Files:**
- Modify: `scripts/tennis_mma_strategy_replay.py`
- Modify: `tests/test_tennis_mma_strategy_replay.py`

**What it does:** Adds `simulate_tennis_pinnacle_arb_v1`, `simulate_ufc_chalk_fade_v1`, `simulate_tennis_surface_skew_v1` functions that replicate each strategy's `evaluate()` gates against `odds_observations` rows. Output: list of `{"ticker", "side", "price", "edge", "settle"}` dicts.

These three are the strategies that benefit most from selection-bias replay. A2 and A3 get lighter validation later (Task 12).

- [ ] **Step 11.1: Test for `simulate_tennis_pinnacle_arb_v1`**

Append to `tests/test_tennis_mma_strategy_replay.py`:

```python
from scripts.tennis_mma_strategy_replay import simulate_tennis_pinnacle_arb_v1


def test_simulate_tennis_pinnacle_arb_fires_on_gap():
    rows = [
        # Should fire — 0.10 gap, fresh pin, eu within 3pp of pin
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-1",
         "sport": "ATP", "minutes_to_start": 120.0,
         "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.63, "pinnacle_yes_prob": 0.65,
         "pinnacle_age_s": 100, "has_pinnacle": 1, "n_books_eu": 20},
        # Should NOT fire — gap only 2pp
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-2",
         "sport": "ATP", "minutes_to_start": 120.0,
         "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.56, "pinnacle_yes_prob": 0.57,
         "pinnacle_age_s": 100, "has_pinnacle": 1, "n_books_eu": 20},
    ]
    settled = {"KXATPMATCH-1": 1.0, "KXATPMATCH-2": 0.0}
    fires = simulate_tennis_pinnacle_arb_v1(rows, settled)
    assert len(fires) == 1
    assert fires[0]["ticker"] == "KXATPMATCH-1"
    assert fires[0]["side"] == "yes"
    assert fires[0]["settle"] == 1.0
```

- [ ] **Step 11.2: Run, fail**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py::test_simulate_tennis_pinnacle_arb_fires_on_gap -v
```
Expected: ImportError.

- [ ] **Step 11.3: Implement the three simulators**

Append to `scripts/tennis_mma_strategy_replay.py`:

```python
def simulate_tennis_pinnacle_arb_v1(
    rows: list[dict], settled: dict[str, float]
) -> list[dict]:
    """A1 replay: |Kalshi-Pinnacle| >= 5pp AND eu drift <= 3pp."""
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 4320:
            continue
        pin = r["pinnacle_yes_prob"]
        eu = r["eu_yes_prob"]
        if abs(eu - pin) > 0.03:
            continue
        ya = r["kalshi_yes_ask"]
        na = r["kalshi_no_ask"]
        gap_yes = pin - ya
        gap_no = (1.0 - pin) - na
        if gap_yes >= 0.05 and gap_yes >= gap_no:
            side, price, edge = "yes", ya, gap_yes
        elif gap_no >= 0.05:
            side, price, edge = "no", na, gap_no
        else:
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "tennis_pinnacle_arb_v1", "ticker": ticker, "side": side,
            "price": price, "edge": edge, "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def simulate_ufc_chalk_fade_v1(
    rows: list[dict], settled: dict[str, float]
) -> list[dict]:
    """B1 replay: kalshi_yes_ask >= 0.78 AND pinnacle_yes_prob >= 0.78."""
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") != "UFC":
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 4320:
            continue
        ya = r["kalshi_yes_ask"]
        pin = r["pinnacle_yes_prob"]
        if ya < 0.78 or pin < 0.78:
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "ufc_chalk_fade_v1", "ticker": ticker, "side": "no",
            "price": r["kalshi_no_ask"], "edge": (ya + pin) / 2 - 0.78,
            "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def simulate_tennis_surface_skew_v1(
    rows: list[dict], settled: dict[str, float], slug_meta: dict,
) -> list[dict]:
    """C replay: |Pinnacle - eu| >= 0.04 on clay or grass surface."""
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        slug = r.get("tournament_slug")
        meta = slug_meta.get(slug)
        if meta is None or meta.get("surface") not in {"clay", "grass"}:
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        pin = r["pinnacle_yes_prob"]
        eu = r["eu_yes_prob"]
        skew = pin - eu
        if abs(skew) < 0.04:
            continue
        if skew > 0:
            side, price = "yes", r["kalshi_yes_ask"]
        else:
            side, price = "no", r["kalshi_no_ask"]
        if not (0.20 <= price <= 0.80):
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "tennis_surface_skew_v1", "ticker": ticker, "side": side,
            "price": price, "edge": abs(skew), "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires
```

- [ ] **Step 11.4: Verify test passes**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py -v
```
Expected: 4 passed.

- [ ] **Step 11.5: Add CLI entry point**

Append to `scripts/tennis_mma_strategy_replay.py`:

```python
def _compute_summary(fires: list[dict], strategy: str) -> dict:
    if not fires:
        return {"strategy": strategy, "n": 0, "wr": 0.0, "roi": 0.0,
                "wilson_lo": 0.0, "wilson_hi": 0.0}
    wins = 0
    pnl = 0.0
    cost_total = 0.0
    for f in fires:
        price = f["price"]
        settle = f["settle"]
        side = f["side"]
        # YES side wins if settle==1; NO side wins if settle==0.
        is_win = (side == "yes" and settle == 1.0) or (side == "no" and settle == 0.0)
        if is_win:
            wins += 1
            pnl += (1.0 - price) - KALSHI_FEE * price
        else:
            pnl += -price - KALSHI_FEE * price
        cost_total += price
    n = len(fires)
    wr = wins / n
    roi = pnl / cost_total if cost_total > 0 else 0.0
    lo, hi = wilson_ci(wins, n)
    return {
        "strategy": strategy, "n": n, "wr": wr, "roi": roi,
        "wilson_lo": lo, "wilson_hi": hi,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--strategy",
                        choices=["tennis_pinnacle_arb_v1", "ufc_chalk_fade_v1",
                                 "tennis_surface_skew_v1", "all"],
                        default="all")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    from strategies.sports_trader import TENNIS_SLUG_META

    if args.strategy in ("tennis_pinnacle_arb_v1", "all"):
        rows = load_observations(conn, sports=("ATP", "WTA"), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_tennis_pinnacle_arb_v1(rows, settled)
        print(_compute_summary(fires, "tennis_pinnacle_arb_v1"))

    if args.strategy in ("ufc_chalk_fade_v1", "all"):
        rows = load_observations(conn, sports=("UFC",), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_ufc_chalk_fade_v1(rows, settled)
        print(_compute_summary(fires, "ufc_chalk_fade_v1"))

    if args.strategy in ("tennis_surface_skew_v1", "all"):
        rows = load_observations(conn, sports=("ATP", "WTA"), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_tennis_surface_skew_v1(rows, settled, TENNIS_SLUG_META)
        print(_compute_summary(fires, "tennis_surface_skew_v1"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 11.6: Commit**

```bash
git add scripts/tennis_mma_strategy_replay.py tests/test_tennis_mma_strategy_replay.py
git commit -m "feat(replay): A1/B1/C simulators + CLI"
```

---

## Task 12: A3 replay — bid-imbalance with selection-bias guard

**Files:**
- Modify: `scripts/tennis_mma_strategy_replay.py`
- Modify: `tests/test_tennis_mma_strategy_replay.py`

**What it does:** Adds `simulate_tennis_bid_imbalance_v1` plus a **decile-bucketed baseline comparison**. Per spec: top imbalance decile WR must beat baseline (price-band-matched WR without imbalance filter) by ≥6pp.

- [ ] **Step 12.1: Test the bias-guard function**

Append to test file:

```python
from scripts.tennis_mma_strategy_replay import (
    simulate_tennis_bid_imbalance_v1,
    compute_imbalance_decile_baseline,
)


def test_imbalance_decile_baseline_returns_per_band_wr():
    """For each price band, return baseline WR with NO imbalance filter."""
    rows = [
        # YES band 0.50-0.85: 3 wins / 5 settled
        {"timestamp": "t", "kalshi_ticker": f"X{i}", "sport": "ATP",
         "minutes_to_start": 300.0, "kalshi_yes_ask": 0.60, "kalshi_no_ask": 0.40,
         "kalshi_yes_bid_size": 50.0, "kalshi_no_bid_size": 50.0,
         "pinnacle_yes_prob": 0.60, "pinnacle_age_s": 100, "has_pinnacle": 1,
         "eu_yes_prob": 0.60}
        for i in range(5)
    ]
    settled = {f"X{i}": (1.0 if i < 3 else 0.0) for i in range(5)}
    baselines = compute_imbalance_decile_baseline(rows, settled)
    assert "yes_band" in baselines
    assert abs(baselines["yes_band"] - 0.60) < 0.001  # 3/5 = 0.60
```

- [ ] **Step 12.2: Run, fail**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py::test_imbalance_decile_baseline_returns_per_band_wr -v
```
Expected: ImportError.

- [ ] **Step 12.3: Implement**

Append to `scripts/tennis_mma_strategy_replay.py`:

```python
def simulate_tennis_bid_imbalance_v1(
    rows: list[dict], settled: dict[str, float]
) -> list[dict]:
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 1440:
            continue
        yb = r.get("kalshi_yes_bid_size") or 0
        nb = r.get("kalshi_no_bid_size") or 0
        if (yb + nb) <= 0:
            continue
        imbalance = yb / (yb + nb)
        ya = r["kalshi_yes_ask"]
        pin = r["pinnacle_yes_prob"]
        if imbalance > 0.65 and 0.50 <= ya <= 0.85 and pin > ya:
            side, price = "yes", ya
        elif imbalance < 0.35 and 0.15 <= ya <= 0.50 and pin < ya:
            side, price = "no", r["kalshi_no_ask"]
        else:
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "tennis_bid_imbalance_v1", "ticker": ticker, "side": side,
            "price": price, "edge": imbalance, "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def compute_imbalance_decile_baseline(
    rows: list[dict], settled: dict[str, float]
) -> dict[str, float]:
    """Baseline WR per A3 price band, WITHOUT imbalance filter.

    A3's promotion gate requires top-decile WR ≥ baseline + 6pp.
    """
    yes_wins, yes_n = 0, 0
    no_wins, no_n = 0, 0
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 1440:
            continue
        if ticker not in settled:
            continue
        ya = r["kalshi_yes_ask"]
        s = settled[ticker]
        if 0.50 <= ya <= 0.85:
            yes_n += 1
            if s == 1.0:
                yes_wins += 1
            seen.add(ticker)
        elif 0.15 <= ya <= 0.50:
            no_n += 1
            if s == 0.0:
                no_wins += 1
            seen.add(ticker)
    return {
        "yes_band": yes_wins / yes_n if yes_n > 0 else 0.0,
        "no_band": no_wins / no_n if no_n > 0 else 0.0,
        "yes_n": yes_n,
        "no_n": no_n,
    }
```

- [ ] **Step 12.4: Verify pass**

```bash
venv/bin/pytest tests/test_tennis_mma_strategy_replay.py -v
```
Expected: 5 passed.

- [ ] **Step 12.5: Extend CLI to include A3**

In `main()`, add the `"tennis_bid_imbalance_v1"` choice and branch:

```python
    if args.strategy in ("tennis_bid_imbalance_v1", "all"):
        rows = load_observations(conn, sports=("ATP", "WTA"), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        baseline = compute_imbalance_decile_baseline(rows, settled)
        fires = simulate_tennis_bid_imbalance_v1(rows, settled)
        summary = _compute_summary(fires, "tennis_bid_imbalance_v1")
        summary["baseline_yes_wr"] = baseline["yes_band"]
        summary["baseline_no_wr"] = baseline["no_band"]
        print(summary)
```

- [ ] **Step 12.6: Commit**

```bash
git add scripts/tennis_mma_strategy_replay.py tests/test_tennis_mma_strategy_replay.py
git commit -m "feat(replay): A3 bid_imbalance with baseline-decile selection-bias guard"
```

---

## Task 13: Execute Phase 0 replay against live observer data

**Files:** None modified — execution only.

**What it does:** Runs the replay against the 5/11-5/15 observer window on prod DB (SSH to EC2). Captures output as the gate for paper-twin enablement. If A3's top-decile WR < baseline + 6pp, A3 is held back from paper-twin launch (Task 14).

- [ ] **Step 13.1: SSH to EC2 and run all three replays**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && venv/bin/python scripts/tennis_mma_strategy_replay.py \
     --db data/bot.db --start 2026-05-11 --end 2026-05-16 --strategy all"
```

Capture stdout. Expected: 4 summary dicts (A1, A3, B1, C).

- [ ] **Step 13.2: Decide per-strategy enablement based on output**

Apply these enablement rules:

| Strategy | Enable paper-twin if |
|---|---|
| A1 tennis_pinnacle_arb_v1 | n ≥ 10 AND wilson_lo ≥ -10% |
| A2 atp_gs_bo5_chalk_v1 | Enabled — French Open not yet started; no replay possible. Paper-only by definition. |
| A3 tennis_bid_imbalance_v1 | **Top-decile WR ≥ baseline + 6pp** AND n ≥ 10. Else hold. |
| B1 ufc_chalk_fade_v1 | n ≥ 3 AND wilson_lo ≥ -25% (sample slow; tolerate wide CI) |
| C tennis_surface_skew_v1 | n ≥ 10 AND wilson_lo ≥ -10% |

If any strategy fails its gate, comment out the corresponding `("...", self.sports_...)` line in `main.py` and re-commit before deploy.

- [ ] **Step 13.3: Document the replay output as a memory note**

Save the output to a new memory file (use the auto-memory `Write` pattern). Skipped here in the plan; specified in Task 15.

- [ ] **Step 13.4: No commit needed unless main.py was edited**

If a strategy was held back, commit the main.py change:

```bash
git add main.py
git commit -m "ops(tennis-mma): hold <strategy> from paper-twin per Phase 0 replay"
```

---

## Task 14: Deploy to EC2 + smoke test

**Files:** None modified — deploy only.

- [ ] **Step 14.1: Push to master**

```bash
git push origin master
```

- [ ] **Step 14.2: Pull and restart on EC2**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot && sudo systemctl is-active kalshi-bot"
```
Expected: prints `active`.

- [ ] **Step 14.3: Tail logs for 60 seconds, verify 5 strategies appear**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '1 minute ago' | grep -E 'tennis_pinnacle_arb|atp_gs_bo5|tennis_bid_imbalance|ufc_chalk_fade|tennis_surface_skew' | head -20"
```
Expected: at least one log line from each enabled strategy (most likely "ATP/WTA/UFC market scanned, no signal" or similar — the point is they're alive).

- [ ] **Step 14.4: Check dashboard for new rows**

```bash
curl -s http://18.117.216.224:8080/api/strategies | python3 -m json.tool | grep -E 'tennis|ufc|atp'
```
Expected: 5 entries (or however many enabled).

---

## Task 15: Memory updates

**Files:**
- Create: `~/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/tennis_mma_5strat_design_2026-05-15.md`
- Modify: `~/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/MEMORY.md`
- Modify: `~/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/tennis_mma_observer_2026-05-11.md`

**What it does:** Documents the launch state so future Claude sessions can pick up context.

- [ ] **Step 15.1: Create the new memory file**

Write a memory file with:

```markdown
---
name: tennis-mma-5strat-design-2026-05-15
description: 5 net-new tennis/MMA strategies (A1/A2/A3/B1/C) — paper-twin launched 2026-05-15 per design spec
metadata:
  type: project
---

Launched 2026-05-15 (commit <sha>). Paper-twin only.

**Strategies:**
- A1 sports_tennis_pinnacle_arb_v1 — Kalshi-Pinnacle spread ≥5pp arb (ATP+WTA, edge-scaled $5-30)
- A2 sports_atp_gs_bo5_chalk_v1 — ATP GS BO5 0.70-0.85 chalk hold (calendar-dependent)
- A3 sports_tennis_bid_imbalance_v1 — top-of-book skew (paper held if Phase 0 baseline+6pp fails)
- B1 sports_ufc_chalk_fade_v1 — UFC ≥0.78 chalk fade BUY NO (slow sample, ~4-8/mo)
- C sports_tennis_surface_skew_v1 — Pinnacle-eu gap on clay/grass

**Phase 0 replay results (5/11-5/15 window):** <fill in from Task 13.1 output>

**Promotion gates:**
- A1/A2/C: n≥25, ROI≥+3%, drop-5≥0
- A3: n≥30, ROI≥+5%, Wilson lower>0%
- B1: n≥15, ROI≥+3%, Wilson lower≥-3%

**Spec:** [docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md](../../Desktop/LFG/kalshi-arb/docs/superpowers/specs/2026-05-15-tennis-mma-strategy-portfolio-design.md)
**Plan:** [docs/superpowers/plans/2026-05-15-tennis-mma-strategy-portfolio-plan.md](../../Desktop/LFG/kalshi-arb/docs/superpowers/plans/2026-05-15-tennis-mma-strategy-portfolio-plan.md)

**First eval:** 2026-06-01 (T+17). T+14 (5/25) original observer promotion_eval runs in parallel for the 4-archetype baseline.

**Related:** [[tennis-mma-observer-2026-05-11]] · [[bid_imbalance_v2_2026-05-15]] · [[crypto_observer_phase1_promotion_2026-05-14]]
```

- [ ] **Step 15.2: Add MEMORY.md index entry**

Append one line to `MEMORY.md`:

```
- [tennis/MMA 5-strat design 2026-05-15](tennis_mma_5strat_design_2026-05-15.md) — A1/A2/A3/B1/C paper-launched; first eval 6/1
```

- [ ] **Step 15.3: Update `tennis_mma_observer_2026-05-11.md`**

Add a one-line note in the existing memory's footer:

```
Strategy portfolio designed + paper-launched 5/15 — see [[tennis-mma-5strat-design-2026-05-15]]
```

- [ ] **Step 15.4: No git commit — memory files are outside the repo.**

---

## Self-Review (post-write)

(Verified inline; no engineer action required.)

1. **Spec coverage:**
   - A1/A2/A3/B1/C all have their own implementation task (3, 4, 5, 6, 7).
   - Common scaffolding (OBSERVATION_ONLY_SPORTS override, base-class wiring, dedup) covered by Task 8.
   - Phase 0 replay covered by Tasks 11, 12.
   - Risk register items mapped: same-ticker multi-strategy (no dedup added, per user), A2 calendar (paper-only by definition, Task 13.2 rule), B1 slow sample (relaxed gate documented in Task 15.1), A3 bias guard (Task 12.3), NY resident (Kalshi-only confirmed at spec level), in-play exclusion (`MAX_MTS_AT_ENTRY` + `PRE_GAME_ONLY` set per strategy).
   - Rollout plan (5/15 spec → 5/17 paper → 6/1 live) matches Task 13 → 14 → 15 sequence (replay → deploy → memory).

2. **Placeholder scan:** No "TBD/TODO/implement later" text in tasks. Each step has actual code or exact commands.

3. **Type consistency:**
   - `STRATEGY_NAME` strings match across .py / order_manager.py / main.py / memory.
   - `evaluate()` signature matches base class: `(kalshi_yes, kalshi_no, fair_prob, line_movement, home_adj, ticker)`.
   - `_size_for_edge` (A1 only) returns float; other strategies use `_get_trade_size`.
   - Replay function names match between script and tests.

4. **Gaps surfaced:**
   - `kalshi_settlements` table name is a guess — Task 2.6 explicitly flags this for verification.
   - `get_top_of_book_sizes` payload-shape detail in Task 8.6 — engineer instructed to match existing client method.

No issues that block execution. The plan is ready to hand to subagents or inline executor.

---

## Total Task Count

15 tasks. ~70 steps. Estimated wall time: 4-6 hours including replay execution + deploy verification.
