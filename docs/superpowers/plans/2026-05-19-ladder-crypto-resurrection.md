# Ladder Crypto Resurrection (Phase 0) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revive retired `ladder_crypto` with phantom-quote pollution fix via orderbook-walk simulation + raised edge threshold. Phase 0 = paper-only prevalence logging for 14d, then decision-tree gates Phase 1 promotion.

**Architecture:** Add stateless `orderbook_walk` utility that consumes Kalshi's existing orderbook_fp endpoint and derives ASK levels from BID-side complementarity (Kalshi only returns bid-side levels). Modify `LadderCryptoStrategy._handle_group` to walk each candidate arb's legs, compute walked-cost edge, compare against raised $0.15 threshold, and INSERT to `ladder_violations` with new walked-* columns. NO `market_order` calls (Phase 0 = pure logging). Daily Telegram cron summarizes prevalence.

**Tech Stack:** Python 3.11, aiosqlite, existing Kalshi REST client, pytest, systemd timer.

**Spec:** [docs/superpowers/specs/2026-05-19-ladder-crypto-resurrection-design.md](../specs/2026-05-19-ladder-crypto-resurrection-design.md)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `core/orderbook_walk.py` | CREATE | Stateless utility: `walk_orderbook(client, ticker, side, target_notional_usdc) → WalkResult`. Derives ASK levels from Kalshi's BID-side orderbook via 1-X complementarity. |
| `tests/test_orderbook_walk.py` | CREATE | Unit tests for walker (empty book, single-level, multi-level, API error). |
| `core/database.py` | MODIFY | Extend `ladder_violations` schema + `record_ladder_violation` to accept walked-* fields. Backward-compatible additive ALTER. |
| `tests/test_ladder_violations_db.py` | MODIFY | Add test for new walked-* columns INSERT/SELECT roundtrip. |
| `core/config.py` | MODIFY | Add 4 `LADDER_PHASE0_*` env-backed constants (lines ~82). |
| `strategies/ladder_crypto.py` | MODIFY | Replace `_handle_group` fire path with Phase 0 walked-edge logging path. Add expiry filter (current + next hourly only). |
| `tests/test_ladder_crypto.py` | CREATE | Unit tests for Phase 0 _handle_group (phantom rejected, would_fire True/False paths). |
| `scripts/ladder_crypto_phase0_summary.py` | CREATE | Daily Telegram summary: counts, edge stats, phantom rate, 14d cumulative gate. |
| `deploy/kalshi-ladder-phase0-summary.service` | CREATE | systemd oneshot unit. |
| `deploy/kalshi-ladder-phase0-summary.timer` | CREATE | Daily 09:00 EST (14:00 UTC) trigger. |

---

## Task 1: Orderbook walker utility

**Files:**
- Create: `core/orderbook_walk.py`
- Test: `tests/test_orderbook_walk.py`

Kalshi's `/markets/{ticker}/orderbook` returns BID-side levels only (`yes_dollars` = YES bids, `no_dollars` = NO bids). To walk the **ASK** side of YES, the walker must derive synthetic asks from NO bids via complementarity: `yes_ask_price = 1.0 - no_bid_price`, quantity unchanged. Symmetric for NO ask side.

- [ ] **Step 1.1: Create test file with empty-book test**

Create `tests/test_orderbook_walk.py`:

```python
"""Tests for orderbook_walk utility (Phase 0 phantom-quote defense)."""
import pytest
from unittest.mock import MagicMock

from core.orderbook_walk import walk_orderbook, WalkResult


def _mock_client(yes_levels, no_levels):
    """Mock Kalshi client returning canned orderbook_levels."""
    client = MagicMock()
    client.get_orderbook_levels.return_value = {"yes": yes_levels, "no": no_levels}
    return client


def test_walk_empty_book_returns_none():
    client = _mock_client([], [])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is None
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_orderbook_walk.py::test_walk_empty_book_returns_none -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.orderbook_walk'`

- [ ] **Step 1.3: Create `core/orderbook_walk.py` stub**

```python
"""Stateless orderbook walker — phantom-quote defense for ladder Phase 0.

Kalshi's /markets/{ticker}/orderbook endpoint returns BID-side levels only
(yes_dollars = YES bids, no_dollars = NO bids). To walk the ASK side for
buy simulation, we derive synthetic asks from the opposite-direction bids
via complementarity: yes_ask_price = 1.0 - no_bid_price, qty unchanged.
The same trick gives NO asks from YES bids.

The walker accumulates contracts level-by-level until target_notional_usdc
is reached (or book exhausted) and reports the resulting average fill
price plus the gap vs top-of-book. A wide top-vs-walked gap is the
phantom-quote signature (top level has 1-contract resting order; real
fill happens at much worse price).
"""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class WalkResult:
    avg_fill_price: float           # walked average (≥ top_ask)
    max_fillable_usdc: float         # notional actually fillable
    levels_consumed: int             # how many ladder levels were walked
    top_ask: float                   # top-of-book ask price
    top_vs_walked_gap_cents: float   # (avg_fill_price - top_ask) * 100


def walk_orderbook(
    client,
    ticker: str,
    side: Literal["yes", "no"],
    target_notional_usdc: float,
) -> Optional[WalkResult]:
    """Walk Kalshi orderbook ASKS for `ticker` `side` up to target notional.

    Returns None if the orderbook is empty on the relevant side or the
    REST call errors. Returns a WalkResult even if max_fillable_usdc <
    target_notional_usdc (caller filters).
    """
    raise NotImplementedError
```

- [ ] **Step 1.4: Run test to verify it now fails with NotImplementedError**

Run: `python -m pytest tests/test_orderbook_walk.py::test_walk_empty_book_returns_none -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 1.5: Implement empty-book case**

Replace the `raise NotImplementedError` body with:

```python
    try:
        book = client.get_orderbook_levels(ticker)
    except Exception:
        return None
    # ASK side derived from opposite-direction BID side
    bid_side_key = "no" if side == "yes" else "yes"
    bid_levels = book.get(bid_side_key, [])
    if not bid_levels:
        return None
    # Convert bid levels → synthetic ask levels via complementarity
    # bid_levels are (price, qty); ask_price = 1 - bid_price
    # Sort: highest bid first = lowest ask first (best for buyer)
    sorted_bids = sorted(bid_levels, key=lambda lv: -lv[0])
    ask_levels = [(round(1.0 - p, 4), q) for p, q in sorted_bids]
    top_ask = ask_levels[0][0]
    # Walk: accumulate cost until target_notional reached
    cum_contracts = 0.0
    cum_cost = 0.0
    levels_consumed = 0
    for ask_price, qty in ask_levels:
        levels_consumed += 1
        # Each contract costs ask_price USD. Take up to target_notional - cum_cost.
        remaining_budget = target_notional_usdc - cum_cost
        if remaining_budget <= 0:
            break
        max_contracts_at_level = remaining_budget / ask_price
        contracts_taken = min(qty, max_contracts_at_level)
        cum_contracts += contracts_taken
        cum_cost += contracts_taken * ask_price
        if cum_cost >= target_notional_usdc - 1e-9:
            break
    if cum_contracts == 0:
        return None
    avg_fill_price = cum_cost / cum_contracts
    max_fillable_usdc = cum_cost
    top_vs_walked_gap_cents = round((avg_fill_price - top_ask) * 100.0, 2)
    return WalkResult(
        avg_fill_price=round(avg_fill_price, 6),
        max_fillable_usdc=round(max_fillable_usdc, 4),
        levels_consumed=levels_consumed,
        top_ask=top_ask,
        top_vs_walked_gap_cents=top_vs_walked_gap_cents,
    )
```

- [ ] **Step 1.6: Run empty-book test, verify pass**

Run: `python -m pytest tests/test_orderbook_walk.py::test_walk_empty_book_returns_none -v`
Expected: PASS

- [ ] **Step 1.7: Add single-level walk test**

Append to `tests/test_orderbook_walk.py`:

```python
def test_walk_single_level_yes_side():
    # To buy YES: read NO bids → derive YES asks
    # NO bid at $0.95 → YES ask at $0.05 (qty 10 contracts = $0.50 notional max)
    client = _mock_client(yes_levels=[(0.04, 100)], no_levels=[(0.95, 10)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.top_ask == pytest.approx(0.05)
    assert result.avg_fill_price == pytest.approx(0.05)
    assert result.max_fillable_usdc == pytest.approx(0.50)  # 10 contracts × $0.05
    assert result.levels_consumed == 1
    assert result.top_vs_walked_gap_cents == pytest.approx(0.0)


def test_walk_multi_level_with_gap():
    # NO bids: [(0.95, 5), (0.90, 50)]
    # → YES asks: [($0.05, 5), ($0.10, 50)]
    # Target $25: take 5 contracts @ $0.05 ($0.25) + remaining $24.75 @ $0.10 → 247.5 contracts
    # Total: 252.5 contracts, cost $25.00, avg = $25.00 / 252.5 = $0.0990
    client = _mock_client(yes_levels=[], no_levels=[(0.95, 5), (0.90, 50)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.top_ask == pytest.approx(0.05)
    assert result.avg_fill_price == pytest.approx(0.09901, abs=1e-4)
    assert result.levels_consumed == 2
    assert result.top_vs_walked_gap_cents == pytest.approx(4.9, abs=0.1)
```

- [ ] **Step 1.8: Run new tests, verify pass**

Run: `python -m pytest tests/test_orderbook_walk.py -v`
Expected: 3 PASS

- [ ] **Step 1.9: Add API error test**

Append:

```python
def test_walk_api_error_returns_none():
    client = MagicMock()
    client.get_orderbook_levels.side_effect = RuntimeError("kalshi 502")
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is None


def test_walk_thin_book_returns_partial_fill():
    # NO bid: (0.99, 2) → YES ask $0.01, only 2 contracts available = $0.02 notional
    client = _mock_client(yes_levels=[], no_levels=[(0.99, 2)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.max_fillable_usdc == pytest.approx(0.02)
    assert result.max_fillable_usdc < 25.0  # partial fill
```

- [ ] **Step 1.10: Run all walk tests, verify pass**

Run: `python -m pytest tests/test_orderbook_walk.py -v`
Expected: 5 PASS

- [ ] **Step 1.11: Commit**

```bash
git add core/orderbook_walk.py tests/test_orderbook_walk.py
git commit -m "$(cat <<'EOF'
feat(orderbook_walk): stateless walker derives ASK levels from Kalshi BID-side

Kalshi /orderbook endpoint returns bid-side only (yes_dollars=YES bids,
no_dollars=NO bids). walk_orderbook converts via 1-X complementarity to
synthetic ASK levels, walks accumulating cost to target notional, reports
walked avg vs top-of-book gap. Foundation for ladder Phase 0 phantom-quote
defense.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: DB migration + extended record method

**Files:**
- Modify: `core/database.py` (CREATE TABLE around line 98, `record_ladder_violation` around line 507)
- Modify: `tests/test_ladder_violations_db.py`

The existing `ladder_violations` table is 2-leg-oriented (`leg_a_ticker`, `leg_b_ticker`). Phase 0 needs to log N-leg arbs (B-bucketed has 30+ legs). Add `walked_legs_json TEXT` to carry the full leg detail; keep `leg_a/leg_b` columns populated with the first two legs for backward query compatibility.

- [ ] **Step 2.1: Extend CREATE TABLE statement**

In `core/database.py`, find the `CREATE TABLE IF NOT EXISTS ladder_violations` block (around line 98). Add 5 new columns at the end before the closing `)`:

```python
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ladder_violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    series TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    ladder_type TEXT NOT NULL,
                    leg_a_ticker TEXT NOT NULL,
                    leg_b_ticker TEXT,
                    inversion_amount REAL NOT NULL,
                    leg_a_depth REAL,
                    leg_b_depth REAL,
                    action_taken TEXT NOT NULL,
                    sub_strategy TEXT NOT NULL,
                    walked_edge_usdc REAL,
                    walked_legs_json TEXT,
                    top_vs_walked_max_gap_cents REAL,
                    would_fire INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'phase0_paper'
                )
            """)
```

- [ ] **Step 2.2: Add idempotent ALTER TABLE migration for existing prod DB**

Immediately after the CREATE TABLE block (after the index creation), add:

```python
            # Idempotent column additions (Phase 0 resurrection 2026-05-19).
            # SQLite has no ADD COLUMN IF NOT EXISTS — use try/except per column.
            for col_def in (
                "walked_edge_usdc REAL",
                "walked_legs_json TEXT",
                "top_vs_walked_max_gap_cents REAL",
                "would_fire INTEGER DEFAULT 0",
                "phase TEXT DEFAULT 'phase0_paper'",
            ):
                col_name = col_def.split()[0]
                try:
                    await conn.execute(f"ALTER TABLE ladder_violations ADD COLUMN {col_def}")
                except Exception:
                    pass  # column already exists
```

- [ ] **Step 2.3: Extend `record_ladder_violation` method signature**

In `core/database.py`, find `async def record_ladder_violation` (around line 507) and add new keyword-only params:

```python
    async def record_ladder_violation(
        self, *, series: str, event_key: str, ladder_type: str,
        leg_a_ticker: str, leg_b_ticker: str | None,
        inversion_amount: float,
        leg_a_depth: float | None, leg_b_depth: float | None,
        action_taken: str, sub_strategy: str,
        # NEW Phase 0 fields (default None = backward compatible)
        walked_edge_usdc: float | None = None,
        walked_legs_json: str | None = None,
        top_vs_walked_max_gap_cents: float | None = None,
        would_fire: bool = False,
        phase: str = "phase0_paper",
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO ladder_violations "
                "(ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker, "
                " inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy, "
                " walked_edge_usdc, walked_legs_json, top_vs_walked_max_gap_cents, "
                " would_fire, phase) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker,
                 inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy,
                 walked_edge_usdc, walked_legs_json, top_vs_walked_max_gap_cents,
                 1 if would_fire else 0, phase),
            )
            await conn.commit()
            return cur.lastrowid
```

- [ ] **Step 2.4: Add test for new columns roundtrip**

Read the existing test file first:

Run: `cat tests/test_ladder_violations_db.py | head -50`

Then append a new test to `tests/test_ladder_violations_db.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_ladder_violation_walked_fields_roundtrip(tmp_path):
    from core.database import Database
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.init_db()
    await db.record_ladder_violation(
        series="KXBTC", event_key="26MAY1915", ladder_type="bucketed",
        leg_a_ticker="KXBTC-26MAY1915-B66250", leg_b_ticker="KXBTC-26MAY1915-B66350",
        inversion_amount=0.0, leg_a_depth=30.0, leg_b_depth=50.0,
        action_taken="phase0_log", sub_strategy="ladder_crypto",
        walked_edge_usdc=0.18,
        walked_legs_json='[{"ticker":"KXBTC-26MAY1915-B66250","walked_avg":0.05}]',
        top_vs_walked_max_gap_cents=1.5,
        would_fire=True, phase="phase0_paper",
    )
    rows = await db.fetch_recent_ladder_violations("ladder_crypto", limit=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["walked_edge_usdc"] == pytest.approx(0.18)
    assert r["top_vs_walked_max_gap_cents"] == pytest.approx(1.5)
    assert r["would_fire"] == 1
    assert r["phase"] == "phase0_paper"
    assert "KXBTC-26MAY1915-B66250" in r["walked_legs_json"]
```

- [ ] **Step 2.5: Run test, verify pass**

Run: `python -m pytest tests/test_ladder_violations_db.py -v`
Expected: existing tests + new test all PASS

- [ ] **Step 2.6: Verify migration is idempotent on existing DB**

Run a quick check that the migration logic runs twice without error:

```bash
python -c "
import asyncio
from core.database import Database
async def main():
    db = Database(db_path='/tmp/test_idempotent.db')
    await db.init_db()
    await db.init_db()  # second call must not crash
    print('idempotent OK')
asyncio.run(main())
"
```

Expected: prints `idempotent OK` with no exceptions.

- [ ] **Step 2.7: Commit**

```bash
git add core/database.py tests/test_ladder_violations_db.py
git commit -m "$(cat <<'EOF'
feat(db): extend ladder_violations for Phase 0 walked-edge logging

5 new columns (walked_edge_usdc, walked_legs_json, top_vs_walked_max_gap_cents,
would_fire, phase) with idempotent ALTER TABLE for existing prod. Extends
record_ladder_violation kwargs with backward-compatible defaults.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Phase 0 strategy mode in ladder_crypto.py

**Files:**
- Modify: `core/config.py` (add LADDER_PHASE0_* constants ~line 82)
- Modify: `strategies/ladder_crypto.py` (replace `_handle_group` fire path)
- Create: `tests/test_ladder_crypto.py`

- [ ] **Step 3.1: Add Phase 0 config constants**

In `core/config.py`, after line 82 (existing LADDER_STALE_ORDERBOOK_SEC), insert:

```python
        # Ladder Phase 0 resurrection (2026-05-19). Paper-only walked-edge
        # logging; phantom-quote defense via orderbook walk. Spec:
        # docs/superpowers/specs/2026-05-19-ladder-crypto-resurrection-design.md
        self.LADDER_PHASE0_MIN_WALKED_EDGE_USDC = float(
            os.getenv("LADDER_PHASE0_MIN_WALKED_EDGE_USDC", "0.15")
        )
        self.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS = float(
            os.getenv("LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS", "2.0")
        )
        self.LADDER_PHASE0_PROBE_NOTIONAL_USDC = float(
            os.getenv("LADDER_PHASE0_PROBE_NOTIONAL_USDC", "25.0")
        )
        self.LADDER_PHASE0_ENABLED = os.getenv("LADDER_PHASE0_ENABLED", "true").lower() == "true"
```

- [ ] **Step 3.2: Create failing test for would_fire=True path**

Create `tests/test_ladder_crypto.py`:

```python
"""Tests for ladder_crypto Phase 0 _handle_group logging path."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.ladder_crypto import LadderCryptoStrategy
from core.ladder_framework import LadderGroup, LadderLeg


def _make_config():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.LADDER_MIN_ARB_EDGE_USDC = 0.02
    cfg.LADDER_MIN_FADE_INVERSION = 0.05
    cfg.LADDER_MIN_DEPTH_USDC = 30.0
    cfg.LADDER_MAX_POSITION_PER_GROUP = 50.0
    cfg.LADDER_PHASE0_MIN_WALKED_EDGE_USDC = 0.15
    cfg.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS = 2.0
    cfg.LADDER_PHASE0_PROBE_NOTIONAL_USDC = 25.0
    cfg.LADDER_PHASE0_ENABLED = True
    return cfg


def _cumulative_legs(t1_yes_ask=0.10, t2_yes_ask=0.70):
    """Two cumulative legs with inversion-style ask gap.

    t1 (lower threshold), t2 (higher). Framework cumulative arb gate:
      t2.yes_bid > t1.yes_ask  AND  t1.yes_ask + t2.no_ask + 2*fee < 1 - min_edge
    Default: t1.yes_ask=0.10, t2.no_ask=0.30, cost=0.40, edge=1-0.40-0.14=0.46.
    """
    return (
        LadderLeg(
            ticker="KXBTC-26MAY1915-T66250", threshold=66250.0,
            yes_ask=t1_yes_ask, yes_bid=t1_yes_ask,
            no_ask=round(1.0 - t1_yes_ask, 4), no_bid=round(1.0 - t1_yes_ask, 4),
            yes_depth=100.0, no_depth=100.0,
        ),
        LadderLeg(
            ticker="KXBTC-26MAY1915-T66350", threshold=66350.0,
            yes_ask=t2_yes_ask, yes_bid=t2_yes_ask,
            no_ask=round(1.0 - t2_yes_ask, 4), no_bid=round(1.0 - t2_yes_ask, 4),
            yes_depth=100.0, no_depth=100.0,
        ),
    )


@pytest.mark.asyncio
async def test_phase0_would_fire_logs_violation(monkeypatch):
    """Cumulative arb with deep fillable books, no phantom → would_fire=True."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        # Return walked == top-of-book (no phantom). Prices match
        # _cumulative_legs defaults: t1.yes_ask=$0.10, t2.no_ask=$0.30.
        price = 0.10 if "T66250" in ticker else 0.30
        return orderbook_walk.WalkResult(
            avg_fill_price=price, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=price, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock()
    om.is_circuit_breaker_active.return_value = False
    om.market_order = AsyncMock()
    db = MagicMock()
    db.record_ladder_violation = AsyncMock(return_value=1)
    client = MagicMock()
    scanner = MagicMock()

    strat = LadderCryptoStrategy(cfg, om, scanner, client)
    strat._db = db  # injected; see Step 3.4

    legs = _cumulative_legs()  # framework detects arb edge $0.46
    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc", legs=legs)

    await strat._handle_group(group)

    assert om.market_order.await_count == 0, "Phase 0 must not fire"
    assert db.record_ladder_violation.await_count >= 1
    # Find the arb log (action_taken='phase0_arb_log')
    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    kw = arb_calls[0].kwargs
    assert kw["would_fire"] is True
    assert kw["phase"] == "phase0_paper"
    # walked_edge = 1 - (0.10 + 0.30) - 2*0.07 = 0.46
    assert kw["walked_edge_usdc"] == pytest.approx(0.46, abs=0.01)
```

- [ ] **Step 3.3: Run test, verify it fails**

Run: `python -m pytest tests/test_ladder_crypto.py::test_phase0_would_fire_logs_violation -v`
Expected: FAIL (multiple errors — `walk_orderbook` not imported, `_db` not injected, `_handle_group` still calls `market_order`)

- [ ] **Step 3.4: Modify `strategies/ladder_crypto.py` — add imports + DB injection**

At the top of `strategies/ladder_crypto.py`, replace the existing imports with:

```python
"""Ladder Crypto — KXBTC, KXETH price-bucket ladders.

Phase 0 (2026-05-19): Paper-only walked-edge logging. NO market_order calls.
See docs/superpowers/specs/2026-05-19-ladder-crypto-resurrection-design.md

A KXBTC/KXETH event publishes two market types side-by-side under the
same event key:

- `T<float>` cumulative markets — "$X or above" / "$X or below". Sparse
  (typically 2 per event). yes_ask is monotone in threshold.
- `B<int>`   bucketed markets   — "$X to $X+99.99" single $100 (BTC) or
  $40 (ETH) range. Dense (50+ per event). yes_ask peaks around ATM, NOT
  monotone — adjacent buckets on the low-prob side naturally have
  yes_ask values that rise toward ATM (e.g. 0.02 → 0.05 → 0.15).
"""
import json
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)
from core.orderbook_walk import walk_orderbook, WalkResult

logger = logging.getLogger("ladder_crypto")
```

Then in `LadderCryptoStrategy.__init__`, accept an optional `db` parameter:

```python
    def __init__(self, config, order_manager, market_scanner, kalshi_client, db=None):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._db = db
```

Then find where `LadderCryptoStrategy` is instantiated in `main.py` and pass the db. Search:

Run: `grep -n "LadderCryptoStrategy" /Users/taejunoh/Desktop/LFG/kalshi-arb/main.py`

Update the instantiation site to add `db=db` (or whatever the local DB variable is called — inspect the surrounding code to match).

- [ ] **Step 3.5: Replace `_handle_group` with Phase 0 logging path**

Replace the entire `_handle_group` method in `strategies/ladder_crypto.py` with:

```python
    async def _handle_group(self, group):
        cfg = self.config
        if not getattr(cfg, "LADDER_PHASE0_ENABLED", True):
            return
        if group.ladder_type == "bucketed":
            sum_mid = sum((l.yes_ask + l.yes_bid) / 2.0 for l in group.legs)
            if not (BUCKETED_SUM_MID_MIN <= sum_mid <= BUCKETED_SUM_MID_MAX):
                return

        arb_candidates = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        for arb in arb_candidates:
            await self._log_phase0_arb(group, arb)

        # Fades also logged (paper); single-leg path.
        fade_candidates = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        for fade in fade_candidates:
            await self._log_phase0_fade(group, fade)

    async def _log_phase0_arb(self, group, arb):
        """Walk each leg, compute walked-edge per contract-group, log to DB.

        Per-contract math (same units as framework's find_arb_opportunities):
          cumulative arb: buy 1 t1.yes + 1 t2.no → payoff $1, cost = sum of
            walked prices, fee = 2 × KALSHI_FEE_PER_CONTRACT
          bucketed arb: buy 1 contract on each of N mutually-exclusive legs
            → payoff $1, cost = sum of walked prices, fee = N × fee_per_contract
        edge per unit ∈ [-fee, ~$0.50] — directly comparable to the
        LADDER_PHASE0_MIN_WALKED_EDGE_USDC threshold ($0.15).
        """
        cfg = self.config
        probe = cfg.LADDER_PHASE0_PROBE_NOTIONAL_USDC
        walked = []
        max_gap = 0.0
        all_fillable = True
        walked_per_unit_cost = 0.0  # sum of walked $/contract across legs
        for leg in arb.legs:
            wr = walk_orderbook(self._client, leg.ticker, leg.side, probe)
            if wr is None:
                all_fillable = False
                walked.append({"ticker": leg.ticker, "side": leg.side, "walked": None})
                # Treat missing walk as worst-case for cost (full $1) so edge
                # goes negative and would_fire stays False.
                walked_per_unit_cost += 1.0
                continue
            walked.append({
                "ticker": leg.ticker, "side": leg.side,
                "top_ask": wr.top_ask, "walked_avg": wr.avg_fill_price,
                "max_fillable_usdc": wr.max_fillable_usdc,
                "gap_cents": wr.top_vs_walked_gap_cents,
            })
            if wr.max_fillable_usdc < probe:
                all_fillable = False
            max_gap = max(max_gap, wr.top_vs_walked_gap_cents)
            walked_per_unit_cost += wr.avg_fill_price
        n = len(arb.legs)
        fee_legs = 2 if group.ladder_type == "cumulative" else n
        walked_edge = 1.0 - walked_per_unit_cost - fee_legs * cfg.KALSHI_FEE_PER_CONTRACT
        would_fire = (
            walked_edge >= cfg.LADDER_PHASE0_MIN_WALKED_EDGE_USDC
            and all_fillable
            and max_gap <= cfg.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS
        )
        if self._db is not None:
            await self._db.record_ladder_violation(
                series=group.series, event_key=group.event_key,
                ladder_type=group.ladder_type,
                leg_a_ticker=arb.legs[0].ticker,
                leg_b_ticker=arb.legs[1].ticker if len(arb.legs) > 1 else None,
                inversion_amount=arb.expected_edge_usdc,
                leg_a_depth=None, leg_b_depth=None,
                action_taken="phase0_arb_log",
                sub_strategy=self.STRATEGY_NAME,
                walked_edge_usdc=round(walked_edge, 6),
                walked_legs_json=json.dumps(walked),
                top_vs_walked_max_gap_cents=round(max_gap, 2),
                would_fire=would_fire,
                phase="phase0_paper",
            )

    async def _log_phase0_fade(self, group, fade):
        """Single-leg fade: walk just the target leg."""
        cfg = self.config
        probe = cfg.LADDER_PHASE0_PROBE_NOTIONAL_USDC
        wr = walk_orderbook(self._client, fade.ticker, fade.side, probe)
        if wr is None:
            walked_legs = [{"ticker": fade.ticker, "side": fade.side, "walked": None}]
            walked_edge = None
            would_fire = False
            max_gap = None
        else:
            walked_legs = [{
                "ticker": fade.ticker, "side": fade.side,
                "top_ask": wr.top_ask, "walked_avg": wr.avg_fill_price,
                "max_fillable_usdc": wr.max_fillable_usdc,
                "gap_cents": wr.top_vs_walked_gap_cents,
            }]
            # Fade edge ≈ inversion_amount - (walked_avg - top_ask) - fee
            walked_edge = fade.inversion_amount - (wr.avg_fill_price - wr.top_ask) - cfg.KALSHI_FEE_PER_CONTRACT
            would_fire = (
                walked_edge >= cfg.LADDER_PHASE0_MIN_WALKED_EDGE_USDC
                and wr.max_fillable_usdc >= probe
                and wr.top_vs_walked_gap_cents <= cfg.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS
            )
            max_gap = wr.top_vs_walked_gap_cents
        if self._db is not None:
            await self._db.record_ladder_violation(
                series=group.series, event_key=group.event_key,
                ladder_type=group.ladder_type,
                leg_a_ticker=fade.ticker, leg_b_ticker=None,
                inversion_amount=fade.inversion_amount,
                leg_a_depth=None, leg_b_depth=None,
                action_taken="phase0_fade_log",
                sub_strategy=self.STRATEGY_NAME,
                walked_edge_usdc=round(walked_edge, 6) if walked_edge is not None else None,
                walked_legs_json=json.dumps(walked_legs),
                top_vs_walked_max_gap_cents=round(max_gap, 2) if max_gap is not None else None,
                would_fire=would_fire,
                phase="phase0_paper",
            )
```

- [ ] **Step 3.6: Run the would_fire test, verify pass**

Run: `python -m pytest tests/test_ladder_crypto.py::test_phase0_would_fire_logs_violation -v`
Expected: PASS

- [ ] **Step 3.7: Add phantom-rejection test**

Append to `tests/test_ladder_crypto.py`:

```python
@pytest.mark.asyncio
async def test_phase0_phantom_quote_rejected(monkeypatch):
    """Top-vs-walked gap > 2c on any leg → would_fire=False."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        # t1.yes leg has 3c walked gap (phantom); t2.no leg is clean
        if "T66250" in ticker:
            return orderbook_walk.WalkResult(
                avg_fill_price=0.13, max_fillable_usdc=25.0,
                levels_consumed=3, top_ask=0.10, top_vs_walked_gap_cents=3.0,
            )
        return orderbook_walk.WalkResult(
            avg_fill_price=0.30, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=0.30, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock(return_value=1)
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    kw = arb_calls[0].kwargs
    assert kw["would_fire"] is False
    assert kw["top_vs_walked_max_gap_cents"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_phase0_thin_book_rejected(monkeypatch):
    """max_fillable_usdc < probe_notional on any leg → would_fire=False."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        if "T66250" in ticker:
            # t1.yes leg only fillable for $5 (thin)
            return orderbook_walk.WalkResult(
                avg_fill_price=0.10, max_fillable_usdc=5.0,
                levels_consumed=1, top_ask=0.10, top_vs_walked_gap_cents=0.0,
            )
        return orderbook_walk.WalkResult(
            avg_fill_price=0.30, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=0.30, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock(return_value=1)
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    assert arb_calls[0].kwargs["would_fire"] is False


@pytest.mark.asyncio
async def test_phase0_no_market_order_calls(monkeypatch):
    """Strict invariant: Phase 0 NEVER calls order_manager.market_order."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        price = 0.10 if "T66250" in ticker else 0.30
        return orderbook_walk.WalkResult(
            avg_fill_price=price, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=price, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock()
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    assert om.market_order.await_count == 0
```

- [ ] **Step 3.8: Run all ladder_crypto tests, verify pass**

Run: `python -m pytest tests/test_ladder_crypto.py -v`
Expected: 4 PASS

- [ ] **Step 3.9: Run full test suite, verify no regressions**

Run: `python -m pytest tests/ -x --ignore=tests/test_market_scanner.py 2>&1 | tail -30`
Expected: all tests pass. If `test_market_scanner.py` requires live API and times out, the `--ignore` skips it.

- [ ] **Step 3.10: Wire DB injection in main.py**

Inspect `main.py` to find where `LadderCryptoStrategy(...)` is instantiated and pass the db argument:

Run: `grep -n "LadderCryptoStrategy(" /Users/taejunoh/Desktop/LFG/kalshi-arb/main.py`

For the matching line (typically `LadderCryptoStrategy(config, order_manager, market_scanner, kalshi_client)`), change to:

```python
LadderCryptoStrategy(config, order_manager, market_scanner, kalshi_client, db=db)
```

If `db` is not yet a local in that scope, find where database is initialized (search `Database(`) and pass that local variable name. Match the existing pattern used by other strategies that inject db (e.g., `crypto_observer`).

- [ ] **Step 3.11: Commit**

```bash
git add core/config.py strategies/ladder_crypto.py tests/test_ladder_crypto.py main.py
git commit -m "$(cat <<'EOF'
feat(ladder_crypto): Phase 0 paper-only walked-edge logging

Replaces _handle_group fire path with walked-cost simulation via
core.orderbook_walk. NO market_order calls; all candidate arbs + fades
logged to ladder_violations with walked_edge_usdc, gap, would_fire flag.
Threshold $0.15 + max 2c gap rejects phantom-quote noise. Config gated by
LADDER_PHASE0_ENABLED env var.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Daily Telegram summary cron

**Files:**
- Create: `scripts/ladder_crypto_phase0_summary.py`
- Create: `deploy/kalshi-ladder-phase0-summary.service`
- Create: `deploy/kalshi-ladder-phase0-summary.timer`

- [ ] **Step 4.1: Inspect existing summary script for pattern**

Run: `head -100 /Users/taejunoh/Desktop/LFG/kalshi-arb/scripts/phase0_observer_eval_alarm.py`

Read the Telegram-send helper pattern (likely imports from `core.telegram` or similar). Note the CLI flag pattern (`--db PATH`, `--dry-run`).

- [ ] **Step 4.2: Create summary script**

Create `scripts/ladder_crypto_phase0_summary.py`:

```python
"""Daily Telegram summary of ladder_crypto Phase 0 prevalence.

Counts walked-violation candidates in the last 24h, the 14d cumulative
gate progress, and phantom-rate (p90 of top_vs_walked_max_gap_cents).
Triggers Phase 1 promotion alarm when 14d would_fire count >= 10.

CLI: python3 scripts/ladder_crypto_phase0_summary.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ladder_phase0_summary")

PHASE1_GATE_14D_COUNT = 10  # Spec decision tree


def _query_summary(db_path: str) -> dict:
    """Return dict with 24h + 14d counts and phantom percentile."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cur = conn.cursor()
    # 24h would_fire count, split by series
    cur.execute("""
        SELECT series, COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-1 days')
        GROUP BY series
    """)
    by_series_24h = dict(cur.fetchall())
    # 24h avg walked_edge
    cur.execute("""
        SELECT AVG(walked_edge_usdc) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-1 days')
    """)
    avg_edge_24h = cur.fetchone()[0]
    # 14d cumulative would_fire count
    cur.execute("""
        SELECT COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-14 days')
    """)
    count_14d = cur.fetchone()[0]
    # Phantom rate: p90 of top_vs_walked_max_gap_cents on candidates (all detected, fire or not)
    cur.execute("""
        SELECT top_vs_walked_max_gap_cents FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND top_vs_walked_max_gap_cents IS NOT NULL
          AND ts >= datetime('now', '-14 days')
        ORDER BY top_vs_walked_max_gap_cents
    """)
    gaps = [r[0] for r in cur.fetchall()]
    if gaps:
        p90_idx = int(len(gaps) * 0.9)
        gap_p90 = gaps[min(p90_idx, len(gaps) - 1)]
    else:
        gap_p90 = None
    # Total candidates (any fire status) in last 48h — early-abort signal
    cur.execute("""
        SELECT COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND ts >= datetime('now', '-2 days')
    """)
    candidates_48h = cur.fetchone()[0]
    conn.close()
    return {
        "by_series_24h": by_series_24h,
        "avg_edge_24h": avg_edge_24h,
        "count_14d": count_14d,
        "gap_p90": gap_p90,
        "candidates_48h": candidates_48h,
    }


def _format_message(s: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    btc = s["by_series_24h"].get("KXBTC", 0)
    eth = s["by_series_24h"].get("KXETH", 0)
    avg_edge = s["avg_edge_24h"]
    avg_edge_str = f"${avg_edge:.3f}" if avg_edge is not None else "n/a"
    gap_p90_str = f"{s['gap_p90']:.1f}c" if s["gap_p90"] is not None else "n/a"
    lines = [
        f"ladder_crypto Phase 0 — daily ({today})",
        f"  BTC would_fire 24h: {btc}",
        f"  ETH would_fire 24h: {eth}",
        f"  avg walked_edge 24h: {avg_edge_str}",
        f"  phantom p90 gap (14d): {gap_p90_str}",
        f"  cumulative 14d: {s['count_14d']} / Phase 1 gate={PHASE1_GATE_14D_COUNT}"
        + (" ✅" if s["count_14d"] >= PHASE1_GATE_14D_COUNT else ""),
    ]
    # Early-abort flag
    if s["candidates_48h"] == 0:
        lines.append("  ⚠️ 48h candidates=0 — code-bug check required")
    return "\n".join(lines)


async def _send_telegram(message: str) -> None:
    try:
        from core.telegram import send_message  # adjust import to existing helper
        await send_message(message)
    except Exception as e:
        logger.warning(f"Telegram send failed (printing instead): {e}")
        print(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("BOT_DB_PATH", "data/bot.db"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = _query_summary(args.db)
    message = _format_message(summary)
    logger.info(message)
    if args.dry_run:
        return
    asyncio.run(_send_telegram(message))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4.3: Verify Telegram helper import path**

Run: `grep -rn "def send_message\|send_telegram" /Users/taejunoh/Desktop/LFG/kalshi-arb/core/ 2>/dev/null | head -5`

If the actual function is named differently (e.g., `send_telegram_message`), edit Step 4.2's `from core.telegram import send_message` to match.

- [ ] **Step 4.4: Smoke-test the script in dry-run**

Run:

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python3 scripts/ladder_crypto_phase0_summary.py --db data/bot.db --dry-run
```

Expected: prints "ladder_crypto Phase 0 — daily ..." with zeros (ladder_violations table likely empty for Phase 0 phase). No crash.

- [ ] **Step 4.5: Create systemd service unit**

Create `deploy/kalshi-ladder-phase0-summary.service`:

```ini
[Unit]
Description=Kalshi ladder_crypto Phase 0 daily summary (Telegram)
After=network.target kalshi-bot.service

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/kalshi-arb
EnvironmentFile=/home/ubuntu/kalshi-arb/.env
ExecStart=/home/ubuntu/kalshi-arb/venv/bin/python scripts/ladder_crypto_phase0_summary.py
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/ubuntu/kalshi-arb/data /home/ubuntu/kalshi-arb/logs
```

- [ ] **Step 4.6: Create systemd timer unit**

Create `deploy/kalshi-ladder-phase0-summary.timer`:

```ini
[Unit]
Description=Daily ladder_crypto Phase 0 summary trigger
Requires=kalshi-ladder-phase0-summary.service

[Timer]
# Daily at 14:00 UTC = 09:00 EST / 10:00 EDT — user awake, before market open
OnCalendar=*-*-* 14:00:00 UTC
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
```

- [ ] **Step 4.7: Commit**

```bash
git add scripts/ladder_crypto_phase0_summary.py \
        deploy/kalshi-ladder-phase0-summary.service \
        deploy/kalshi-ladder-phase0-summary.timer
git commit -m "$(cat <<'EOF'
feat(cron): daily ladder_crypto Phase 0 summary (Telegram)

Reports 24h would_fire counts split by KXBTC/KXETH, avg walked_edge,
14d cumulative count vs Phase 1 gate (>=10), phantom p90 gap, and
48h early-abort flag (candidates=0 → code bug check). systemd timer
daily 14:00 UTC (09:00 EST). Dry-run flag supported for smoke test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Deploy + smoke verify

**Files:** (no new files; deployment + verification)

- [ ] **Step 5.1: Push to master**

```bash
git push origin master
```

- [ ] **Step 5.2: Deploy on EC2**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot && sudo systemctl is-active kalshi-bot"
```

Expected: prints `active`.

- [ ] **Step 5.3: Install + enable summary timer**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 << 'EOF'
sudo cp /home/ubuntu/kalshi-arb/deploy/kalshi-ladder-phase0-summary.service /etc/systemd/system/
sudo cp /home/ubuntu/kalshi-arb/deploy/kalshi-ladder-phase0-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-ladder-phase0-summary.timer
sudo systemctl list-timers kalshi-ladder-phase0-summary.timer
EOF
```

Expected: timer is listed with NEXT firing time.

- [ ] **Step 5.4: Watch live logs for first scan cycle**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot -f --since '1 minute ago' | grep -i ladder"
```

Watch for ~2-3 minutes. Expected: at least one `ladder_crypto` scan log entry; no Python traceback. Ctrl+C to exit.

- [ ] **Step 5.5: Verify first DB rows after 10-15 min**

After ~15 minutes of bot uptime, query prod DB:

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "python3 -c \"
import sqlite3
c = sqlite3.connect('/home/ubuntu/kalshi-arb/data/bot.db').cursor()
c.execute(\\\"SELECT COUNT(*), SUM(would_fire) FROM ladder_violations WHERE phase='phase0_paper' AND ts >= datetime('now','-1 hour')\\\")
print('rows_1h, would_fire_1h:', c.fetchone())
c.execute(\\\"SELECT ts, series, walked_edge_usdc, top_vs_walked_max_gap_cents, would_fire FROM ladder_violations WHERE phase='phase0_paper' ORDER BY ts DESC LIMIT 5\\\")
for r in c.fetchall(): print(r)
\""
```

Expected: at least a few rows in the 1h window. If `rows_1h=0`, investigate (no KXBTC/ETH markets open? scanner not picking up? log read should show).

- [ ] **Step 5.6: Run summary script manually to verify it works on real data**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && venv/bin/python scripts/ladder_crypto_phase0_summary.py --dry-run"
```

Expected: prints the summary message. No crash.

- [ ] **Step 5.7: Save evaluation reminder**

Set a calendar / reminder for **2026-06-02** (14 days from deploy): run the decision tree from the spec.

This step is non-code; mark complete by manually noting the date.

---

## Phase 0 → Phase 1 decision (manual, 14d later)

Per the spec ([Phase 0 → 다음 단계 결정 트리](../specs/2026-05-19-ladder-crypto-resurrection-design.md#phase-0--다음-단계-결정-트리)):

Run on 2026-06-02:

```sql
SELECT COUNT(*) FROM ladder_violations
WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
  AND would_fire=1 AND walked_edge_usdc > 0
  AND ts >= datetime('now', '-14 days');
```

| Result | Action |
|---|---|
| ≥ 10 | Open new spec for Phase 1 (paper trader). Toggle `LADDER_CRYPTO_PHASE='phase1_paper'`. |
| 3-9 | Extend Phase 0 by 7 days. Reconsider threshold/notional. |
| ≤ 2 | Retire conclusion. Delete LADDER_PHASE0_ENABLED toggle (set to false). |

If `p90(top_vs_walked_max_gap_cents) > 5.0` AND count ≥ 10 → Phase 1 requires Kalshi WS orderbook (option b in spec) — open separate sub-project before live.

---

## Self-Review Checklist

After implementing all tasks, run through:

1. **Spec coverage**:
   - Orderbook walker (spec Component 1) → Task 1 ✅
   - DB extension (spec Component 3) → Task 2 ✅
   - Phase 0 strategy mode (spec Component 2) → Task 3 ✅
   - Daily summary cron (spec Component 4) → Task 4 ✅
   - Decision tree (spec Phase 0 → 다음 단계) → documented above ✅
   - Parameters (spec section) → Task 3 Step 3.1 ✅
   - Error handling (spec section) → embedded in Task 1 / Task 3 ✅

2. **Type consistency**: `WalkResult` defined in Task 1, used in Task 3 — fields match (`avg_fill_price`, `max_fillable_usdc`, `levels_consumed`, `top_ask`, `top_vs_walked_gap_cents`). DB columns added in Task 2 are referenced verbatim in Task 3 (`walked_edge_usdc`, `walked_legs_json`, `top_vs_walked_max_gap_cents`, `would_fire`, `phase`) and Task 4 query.

3. **Placeholder check**: No "TBD" / "TODO" / "fill in details" in any task. Step 3.10 has a small grep-and-match instruction for `LadderCryptoStrategy(` instantiation site — this is a real navigation step, not a placeholder.

4. **Decomposition**: 4 PRs (Tasks 1-4) are each independently mergeable. Task 5 is the deployment task that depends on all 4 being merged.
