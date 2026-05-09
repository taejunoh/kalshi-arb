# Ladder Consistency Arb (A1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateless ladder-consistency framework + 4 sub-strategies (crypto / fed / approval / shutdown) that detect monotonicity & sum-to-1 violations on Kalshi multi-leg markets and execute hybrid arb-then-fade trades, with explicit DB dedup guards extended to ladder_* strategies.

**Architecture:** Pure-function `core/ladder_framework.py` exposes detection (`group_legs_into_ladders`, `find_arb_opportunities`, `find_fade_opportunities`) on frozen dataclasses. Each sub-strategy injects a `suffix_parser` + `event_key_extractor` callback, owns its universe filter, and registers as an independent `strategy_name` for paper/live toggle + circuit breaker + promotion_eval. A new `ladder_violations` SQLite table logs detection events for Phase 0 prevalence research before any trade fires.

**Tech Stack:** Python 3, aiosqlite, pytest, asyncio. Codebase patterns: `strategies/sum_arb.py` (양면 fire), `strategies/spread_scanner.py` (universe scan), `strategies/sports_underdog_v37_soccer.py` (recent strategy class shape), `core/order_manager.py:438` (sports-only DB dedup that we extend).

**Spec:** `docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md` (commit `05c000c`).

---

## File Structure

**Create:**
- `core/ladder_framework.py` — dataclasses + detection functions (stateless, ~300 LOC)
- `strategies/ladder_crypto.py` — KXBTC / KXETH (~120 LOC)
- `strategies/ladder_fed.py` — KXFEDDECISION (~100 LOC)
- `strategies/ladder_approval.py` — KXTRUMPAPPROVAL\* / KX538APPROVE\* (~110 LOC)
- `strategies/ladder_shutdown.py` — KXNUMSHUTDOWNS / KXGOVTSHUTLENGTH (~90 LOC)
- `scripts/research/ladder_violation_scanner.py` — Phase 0 prevalence (~150 LOC)
- `tests/test_ladder_framework.py` — pure-function tests (~250 LOC)
- `tests/test_ladder_dedup_guard.py` — V1 paper_filled dedup integration test (~80 LOC)
- `tests/test_ladder_strategies.py` — 4 sub-strategy integration tests (~200 LOC)

**Modify:**
- `core/database.py` — add `ladder_violations` table + `record_ladder_violation` method
- `core/order_manager.py` — extend 4 dedup-guard sites from sports-only to also accept `ladder_*` prefix
- `core/config.py` — add `LADDER_*` env vars + defaults
- `main.py` — import + instantiate + register 4 sub-strategies in `_strategy_loop`

---

## Task 1: Framework dataclasses

**Files:**
- Create: `core/ladder_framework.py`
- Create: `tests/test_ladder_framework.py`

- [ ] **Step 1.1: Write the failing test (dataclass shapes)**

```python
# tests/test_ladder_framework.py
import pytest
from core.ladder_framework import LadderLeg, LadderGroup, ArbLeg, ArbTrade, FadeTrade


def test_ladder_leg_is_frozen():
    leg = LadderLeg(
        ticker="KXBTC-26MAY09-1500-B70250", threshold=70250.0,
        yes_ask=0.45, yes_bid=0.43, no_ask=0.57, no_bid=0.55,
        yes_depth=120.0, no_depth=80.0,
    )
    assert leg.ticker == "KXBTC-26MAY09-1500-B70250"
    assert leg.threshold == 70250.0
    with pytest.raises(Exception):  # frozen → can't reassign
        leg.threshold = 0


def test_ladder_group_holds_sorted_legs():
    legs = (
        LadderLeg("T1", 70250.0, 0.45, 0.43, 0.57, 0.55, 120.0, 80.0),
        LadderLeg("T2", 70350.0, 0.42, 0.40, 0.60, 0.58, 100.0, 90.0),
    )
    g = LadderGroup(
        series="KXBTC", event_key="26MAY09-1500",
        ladder_type="cumulative", direction="asc", legs=legs,
    )
    assert g.legs[0].threshold < g.legs[1].threshold
    assert g.ladder_type == "cumulative"


def test_arb_trade_carries_legs_and_edge():
    trade = ArbTrade(
        group_key="KXBTC-26MAY09-1500",
        legs=(
            ArbLeg(ticker="T1", side="yes", size_usdc=25.0, price=0.45),
            ArbLeg(ticker="T2", side="no", size_usdc=25.0, price=0.50),
        ),
        expected_edge_usdc=2.50,
    )
    assert len(trade.legs) == 2
    assert trade.expected_edge_usdc == 2.50


def test_fade_trade_records_inversion_amount():
    f = FadeTrade(
        group_key="KXFED-FOMC-26JUN", ticker="KXFED-FOMC-26JUN-C25",
        side="yes", size_usdc=20.0, price=0.30, inversion_amount=0.07,
    )
    assert f.inversion_amount == 0.07
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: FAIL with `ImportError: cannot import name 'LadderLeg' from 'core.ladder_framework'`

- [ ] **Step 1.3: Write minimal implementation**

```python
# core/ladder_framework.py
"""Stateless ladder consistency framework — detection only, no I/O.

Detects monotonicity / sum-to-1 violations on multi-leg Kalshi ladders
(cumulative threshold ladders like KXBTC, bucketed exclusive-outcome
ladders like KXFEDDECISION) and produces ArbTrade / FadeTrade signals
for sub-strategies to execute.

Spec: docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md
"""
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class LadderLeg:
    ticker: str
    threshold: float
    yes_ask: float
    yes_bid: float
    no_ask: float
    no_bid: float
    yes_depth: float
    no_depth: float


@dataclass(frozen=True)
class LadderGroup:
    series: str
    event_key: str
    ladder_type: Literal["cumulative", "bucketed"]
    direction: Literal["asc", "desc"]
    legs: tuple[LadderLeg, ...]


@dataclass(frozen=True)
class ArbLeg:
    ticker: str
    side: Literal["yes", "no"]
    size_usdc: float
    price: float


@dataclass(frozen=True)
class ArbTrade:
    group_key: str
    legs: tuple[ArbLeg, ...]
    expected_edge_usdc: float


@dataclass(frozen=True)
class FadeTrade:
    group_key: str
    ticker: str
    side: Literal["yes", "no"]
    size_usdc: float
    price: float
    inversion_amount: float
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 4 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add core/ladder_framework.py tests/test_ladder_framework.py
git commit -m "$(cat <<'EOF'
feat(ladder): add ladder_framework dataclasses (LadderLeg/Group/ArbTrade/FadeTrade)

Foundation for A1 ladder consistency arb (4 sub-strategies share these stateless
dataclasses). Frozen + sorted-legs invariant established by tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: group_legs_into_ladders

**Files:**
- Modify: `core/ladder_framework.py`
- Modify: `tests/test_ladder_framework.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_ladder_framework.py`:
```python
from core.ladder_framework import group_legs_into_ladders


def _btc_market(suffix: str, yes_ask: float, yes_bid: float, depth: float = 100.0) -> dict:
    return {
        "ticker": f"KXBTC-26MAY09-1500-{suffix}",
        "yes_ask": yes_ask, "yes_bid": yes_bid,
        "no_ask": 1.0 - yes_bid, "no_bid": 1.0 - yes_ask,
        "yes_depth": depth, "no_depth": depth,
    }


def _btc_suffix_parser(suffix: str) -> float | None:
    if not suffix.startswith("B"):
        return None
    try:
        return float(suffix[1:])
    except ValueError:
        return None


def _btc_event_key(m: dict) -> str:
    parts = m["ticker"].split("-")
    return "-".join(parts[1:-1])  # 26MAY09-1500


def test_group_legs_groups_by_event_key_and_sorts_asc():
    markets = [
        _btc_market("B70450", 0.40, 0.38),
        _btc_market("B70250", 0.45, 0.43),
        _btc_market("B70350", 0.42, 0.40),
    ]
    groups = group_legs_into_ladders(
        markets,
        suffix_parser=_btc_suffix_parser,
        ladder_type="cumulative",
        event_key_extractor=_btc_event_key,
        direction="asc",
    )
    assert len(groups) == 1
    g = groups[0]
    assert g.event_key == "26MAY09-1500"
    assert g.series == "KXBTC"
    assert [l.threshold for l in g.legs] == [70250.0, 70350.0, 70450.0]
    assert g.direction == "asc"


def test_group_legs_skips_unparseable_suffix():
    markets = [
        _btc_market("B70250", 0.45, 0.43),
        {"ticker": "KXBTC-26MAY09-1500-INVALID",
         "yes_ask": 0.5, "yes_bid": 0.5, "no_ask": 0.5, "no_bid": 0.5,
         "yes_depth": 100.0, "no_depth": 100.0},
    ]
    groups = group_legs_into_ladders(
        markets, _btc_suffix_parser, "cumulative", _btc_event_key,
    )
    assert len(groups) == 1
    assert len(groups[0].legs) == 1


def test_group_legs_desc_direction_reverses_sort():
    # KXTRUMPAPPROVALBELOW: "≤ X%" — bigger X is more permissive (more likely)
    markets = [
        {"ticker": "KXAB-26MAY-37", "yes_ask": 0.55, "yes_bid": 0.53,
         "no_ask": 0.47, "no_bid": 0.45, "yes_depth": 50.0, "no_depth": 50.0},
        {"ticker": "KXAB-26MAY-33", "yes_ask": 0.20, "yes_bid": 0.18,
         "no_ask": 0.82, "no_bid": 0.80, "yes_depth": 50.0, "no_depth": 50.0},
    ]
    groups = group_legs_into_ladders(
        markets,
        suffix_parser=lambda s: float(s) if s.isdigit() else None,
        ladder_type="cumulative",
        event_key_extractor=lambda m: m["ticker"].rsplit("-", 1)[0],
        direction="desc",
    )
    g = groups[0]
    # desc: bigger threshold first. 37 > 33 → first leg is 37.
    assert [l.threshold for l in g.legs] == [37.0, 33.0]


def test_group_legs_separates_distinct_event_keys():
    markets = [
        _btc_market("B70250", 0.45, 0.43),
        # different close time
        {"ticker": "KXBTC-26MAY10-1500-B70250", "yes_ask": 0.40,
         "yes_bid": 0.38, "no_ask": 0.62, "no_bid": 0.60,
         "yes_depth": 100.0, "no_depth": 100.0},
    ]
    groups = group_legs_into_ladders(
        markets, _btc_suffix_parser, "cumulative", _btc_event_key,
    )
    assert len(groups) == 2
    assert {g.event_key for g in groups} == {"26MAY09-1500", "26MAY10-1500"}
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_framework.py::test_group_legs_groups_by_event_key_and_sorts_asc -v`
Expected: FAIL with `ImportError: cannot import name 'group_legs_into_ladders'`

- [ ] **Step 2.3: Write minimal implementation**

Append to `core/ladder_framework.py`:
```python
def group_legs_into_ladders(
    markets: list[dict],
    suffix_parser: Callable[[str], float | None],
    ladder_type: Literal["cumulative", "bucketed"],
    event_key_extractor: Callable[[dict], str],
    direction: Literal["asc", "desc"] = "asc",
) -> list[LadderGroup]:
    """Group market dicts into LadderGroups by event_key.

    `markets`: dicts with 'ticker' and float-dollar prices in keys
    'yes_ask', 'yes_bid', 'no_ask', 'no_bid', plus 'yes_depth', 'no_depth'.
    Caller (sub-strategy) is responsible for normalizing Kalshi's cents-int
    fields to dollar floats before calling.
    """
    by_event: dict[str, list[LadderLeg]] = {}
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker:
            continue
        suffix = ticker.rsplit("-", 1)[-1]
        threshold = suffix_parser(suffix)
        if threshold is None:
            continue
        event_key = event_key_extractor(m)
        if not event_key:
            continue
        leg = LadderLeg(
            ticker=ticker,
            threshold=threshold,
            yes_ask=float(m.get("yes_ask") or 0.0),
            yes_bid=float(m.get("yes_bid") or 0.0),
            no_ask=float(m.get("no_ask") or 0.0),
            no_bid=float(m.get("no_bid") or 0.0),
            yes_depth=float(m.get("yes_depth") or 0.0),
            no_depth=float(m.get("no_depth") or 0.0),
        )
        by_event.setdefault(event_key, []).append(leg)

    out: list[LadderGroup] = []
    for event_key, legs in by_event.items():
        sorted_legs = sorted(legs, key=lambda l: l.threshold,
                             reverse=(direction == "desc"))
        series = sorted_legs[0].ticker.split("-")[0]
        out.append(LadderGroup(
            series=series, event_key=event_key, ladder_type=ladder_type,
            direction=direction, legs=tuple(sorted_legs),
        ))
    return out
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 8 tests PASS (4 from Task 1 + 4 new).

- [ ] **Step 2.5: Commit**

```bash
git add core/ladder_framework.py tests/test_ladder_framework.py
git commit -m "$(cat <<'EOF'
feat(ladder): add group_legs_into_ladders (suffix + event_key grouping)

Pure function — sub-strategy injects suffix_parser + event_key_extractor +
direction. Tests cover asc/desc sort, multi-event separation, unparseable
suffix skip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: find_arb_opportunities (cumulative)

**Files:**
- Modify: `core/ladder_framework.py`
- Modify: `tests/test_ladder_framework.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_ladder_framework.py`:
```python
from core.ladder_framework import find_arb_opportunities


def _leg(ticker: str, threshold: float, yes_ask: float, yes_bid: float,
         depth: float = 100.0) -> LadderLeg:
    return LadderLeg(
        ticker=ticker, threshold=threshold,
        yes_ask=yes_ask, yes_bid=yes_bid,
        no_ask=1.0 - yes_bid, no_bid=1.0 - yes_ask,
        yes_depth=depth, no_depth=depth,
    )


def _cum_group(legs: list[LadderLeg]) -> LadderGroup:
    return LadderGroup(
        series="X", event_key="E1", ladder_type="cumulative",
        direction="asc", legs=tuple(legs),
    )


def test_arb_fires_on_cumulative_inversion_above_fee_budget():
    # T1 (easier threshold) yes_ask 0.40, T2 (stricter) yes_bid 0.60
    # Inversion = 0.20. Cost = 0.40 + 0.40 (no @ T2) = 0.80. + 2·fee 0.14 = 0.94 < 1.
    # Worst payoff = 1.0 → edge = 0.06.
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.40, yes_bid=0.38, depth=200.0),
        _leg("T2", 2.0, yes_ask=0.62, yes_bid=0.60, depth=200.0),
    ])
    trades = find_arb_opportunities(
        g, fee=0.07, min_edge_usdc=0.02, min_depth_usdc=50.0,
        max_position_usdc=50.0,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.legs[0].ticker == "T1" and t.legs[0].side == "yes"
    assert t.legs[1].ticker == "T2" and t.legs[1].side == "no"
    assert t.expected_edge_usdc > 0.05


def test_arb_skips_when_inversion_below_fee_budget():
    # Inversion = 0.05. Cost = 0.45 + 0.50 = 0.95. + 2·fee = 1.09 > 1 → no edge.
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.45, yes_bid=0.43),
        _leg("T2", 2.0, yes_ask=0.52, yes_bid=0.50),
    ])
    trades = find_arb_opportunities(g, fee=0.07, min_edge_usdc=0.02,
                                     min_depth_usdc=50.0, max_position_usdc=50.0)
    assert trades == []


def test_arb_skips_when_depth_insufficient():
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.40, yes_bid=0.38, depth=20.0),
        _leg("T2", 2.0, yes_ask=0.62, yes_bid=0.60, depth=200.0),
    ])
    trades = find_arb_opportunities(g, fee=0.07, min_edge_usdc=0.02,
                                     min_depth_usdc=50.0, max_position_usdc=50.0)
    assert trades == []


def test_arb_skips_when_no_inversion():
    # Strictly monotonic (no inversion).
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.50, yes_bid=0.48),
        _leg("T2", 2.0, yes_ask=0.40, yes_bid=0.38),
    ])
    trades = find_arb_opportunities(g, fee=0.07, min_edge_usdc=0.02,
                                     min_depth_usdc=50.0, max_position_usdc=50.0)
    assert trades == []
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_framework.py::test_arb_fires_on_cumulative_inversion_above_fee_budget -v`
Expected: FAIL with `ImportError: cannot import name 'find_arb_opportunities'`

- [ ] **Step 3.3: Write minimal implementation**

Append to `core/ladder_framework.py`:
```python
def find_arb_opportunities(
    group: LadderGroup,
    fee: float,
    min_edge_usdc: float,
    min_depth_usdc: float,
    max_position_usdc: float,
) -> list[ArbTrade]:
    """Detect adjacent inversion (cumulative) or sum-to-1 violation (bucketed)
    that produces a riskless arb above the fee budget. Sub-strategy is
    responsible for de-duplicating multiple opportunities on the same group.
    """
    trades: list[ArbTrade] = []
    if group.ladder_type == "cumulative":
        legs = group.legs  # already sorted by group_legs_into_ladders
        for i in range(len(legs) - 1):
            t1, t2 = legs[i], legs[i + 1]
            # Inversion: easier threshold's yes_ask < stricter threshold's yes_bid
            inv = t2.yes_bid - t1.yes_ask
            if inv <= 0:
                continue
            # Worst-case payoff = $1; cost = yes_ask(T1) + no_ask(T2)
            cost = t1.yes_ask + t2.no_ask
            edge = 1.0 - cost - 2 * fee
            if edge < min_edge_usdc:
                continue
            if t1.yes_depth < min_depth_usdc or t2.no_depth < min_depth_usdc:
                continue
            half = max_position_usdc / 2.0
            trades.append(ArbTrade(
                group_key=f"{group.series}-{group.event_key}",
                legs=(
                    ArbLeg(ticker=t1.ticker, side="yes",
                           size_usdc=half, price=t1.yes_ask),
                    ArbLeg(ticker=t2.ticker, side="no",
                           size_usdc=half, price=t2.no_ask),
                ),
                expected_edge_usdc=round(edge * (max_position_usdc / 1.0), 4),
            ))
    elif group.ladder_type == "bucketed":
        # implemented in Task 4
        pass
    return trades
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 12 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/ladder_framework.py tests/test_ladder_framework.py
git commit -m "$(cat <<'EOF'
feat(ladder): cumulative inversion arb detection in find_arb_opportunities

Walks adjacent leg pairs, requires inversion magnitude to clear cost + 2·fee,
skips on depth shortfall. Bucketed branch stubbed for Task 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: find_arb_opportunities (bucketed)

**Files:**
- Modify: `core/ladder_framework.py`
- Modify: `tests/test_ladder_framework.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_ladder_framework.py`:
```python
def _bucket_group(legs: list[LadderLeg]) -> LadderGroup:
    return LadderGroup(
        series="KXFEDDECISION", event_key="FOMC-26JUN",
        ladder_type="bucketed", direction="asc", legs=tuple(legs),
    )


def test_arb_fires_on_bucketed_sum_below_fee_budget():
    # 4 mutually-exclusive outcomes, sum_yes_ask = 0.25+0.20+0.15+0.30 = 0.90.
    # Plus 4·fee = 0.28 → 1.18 > 1, no buy-YES arb. NO side: sum_yes_bid = 0.85.
    # n - sum_yes_bid = 4 - 0.85 = 3.15. payoff = n - 1 = 3. cost+fees = 3.15+0.28=3.43. negative.
    # Make it tighter: drop ask sum well below 1 - margin.
    g = _bucket_group([
        _leg("C50", -0.50, yes_ask=0.05, yes_bid=0.04, depth=200.0),
        _leg("C25", -0.25, yes_ask=0.15, yes_bid=0.13, depth=200.0),
        _leg("H0",   0.00, yes_ask=0.50, yes_bid=0.48, depth=200.0),
        _leg("H25",  0.25, yes_ask=0.10, yes_bid=0.08, depth=200.0),
    ])
    # sum_yes_ask = 0.80, n=4, fees=0.28 → cost 1.08 > 1 → NO arb.
    # Need sum_yes_ask + n·fee < 1 - min_edge.
    g2 = _bucket_group([
        _leg("C50", -0.50, yes_ask=0.05, yes_bid=0.04, depth=200.0),
        _leg("C25", -0.25, yes_ask=0.10, yes_bid=0.08, depth=200.0),
        _leg("H0",   0.00, yes_ask=0.40, yes_bid=0.38, depth=200.0),
        _leg("H25",  0.25, yes_ask=0.05, yes_bid=0.04, depth=200.0),
    ])
    # sum_yes_ask = 0.60, +4·0.07 = 0.88 < 1 - 0.02 → arb (buy YES on all).
    trades = find_arb_opportunities(
        g2, fee=0.07, min_edge_usdc=0.02,
        min_depth_usdc=50.0, max_position_usdc=80.0,
    )
    assert len(trades) == 1
    t = trades[0]
    assert len(t.legs) == 4
    assert all(l.side == "yes" for l in t.legs)


def test_arb_fires_on_bucketed_sum_above_one():
    # sum_yes_bid = 0.95+0.20+0.10+0.05 = 1.30 → buy NO on all.
    # cost = (n - sum_yes_bid) + n·fee = (4 - 1.30) + 0.28 = 2.98. payoff = n-1 = 3.
    # edge = 3 - 2.98 = 0.02 ≥ min_edge_usdc.
    g = _bucket_group([
        _leg("C50", -0.50, yes_ask=0.97, yes_bid=0.95, depth=200.0),
        _leg("C25", -0.25, yes_ask=0.22, yes_bid=0.20, depth=200.0),
        _leg("H0",   0.00, yes_ask=0.12, yes_bid=0.10, depth=200.0),
        _leg("H25",  0.25, yes_ask=0.07, yes_bid=0.05, depth=200.0),
    ])
    trades = find_arb_opportunities(
        g, fee=0.07, min_edge_usdc=0.02,
        min_depth_usdc=50.0, max_position_usdc=80.0,
    )
    assert len(trades) == 1
    assert all(l.side == "no" for l in trades[0].legs)


def test_bucketed_arb_skips_when_sum_within_fee_budget():
    # sum_yes_ask = 0.95, +4·0.07 = 1.23 → no buy-YES arb. sum_yes_bid = 0.91, NO side: cost 3.09+0.28=3.37 vs payoff 3 → no.
    g = _bucket_group([
        _leg("C25", -0.25, yes_ask=0.20, yes_bid=0.18, depth=200.0),
        _leg("H0",   0.00, yes_ask=0.50, yes_bid=0.48, depth=200.0),
        _leg("H25",  0.25, yes_ask=0.25, yes_bid=0.23, depth=200.0),
    ])
    trades = find_arb_opportunities(
        g, fee=0.07, min_edge_usdc=0.02,
        min_depth_usdc=50.0, max_position_usdc=60.0,
    )
    assert trades == []
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_framework.py::test_arb_fires_on_bucketed_sum_below_fee_budget -v`
Expected: FAIL (returns empty — bucketed branch stubbed).

- [ ] **Step 4.3: Replace bucketed branch with real implementation**

In `core/ladder_framework.py`, replace the `elif group.ladder_type == "bucketed": pass` block with:

```python
    elif group.ladder_type == "bucketed":
        legs = group.legs
        n = len(legs)
        if n < 2:
            return trades
        sum_yes_ask = sum(l.yes_ask for l in legs)
        sum_yes_bid = sum(l.yes_bid for l in legs)
        per_leg = max_position_usdc / n

        # Buy YES on all legs: cost = sum_yes_ask + n·fee, payoff = $1
        cost_yes = sum_yes_ask + n * fee
        edge_yes = 1.0 - cost_yes
        if edge_yes >= min_edge_usdc and all(l.yes_depth >= min_depth_usdc for l in legs):
            trades.append(ArbTrade(
                group_key=f"{group.series}-{group.event_key}",
                legs=tuple(
                    ArbLeg(ticker=l.ticker, side="yes",
                           size_usdc=per_leg, price=l.yes_ask)
                    for l in legs
                ),
                expected_edge_usdc=round(edge_yes * max_position_usdc, 4),
            ))
            return trades

        # Buy NO on all: cost = (n - sum_yes_bid) + n·fee, payoff = n - 1
        cost_no = (n - sum_yes_bid) + n * fee
        edge_no = (n - 1) - cost_no
        if edge_no >= min_edge_usdc and all(l.no_depth >= min_depth_usdc for l in legs):
            trades.append(ArbTrade(
                group_key=f"{group.series}-{group.event_key}",
                legs=tuple(
                    ArbLeg(ticker=l.ticker, side="no",
                           size_usdc=per_leg, price=l.no_ask)
                    for l in legs
                ),
                expected_edge_usdc=round(edge_no * max_position_usdc, 4),
            ))
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 15 tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add core/ladder_framework.py tests/test_ladder_framework.py
git commit -m "$(cat <<'EOF'
feat(ladder): bucketed sum-to-1 arb detection (full-leg YES/NO)

Implements both sides: sum_yes_ask < 1 - fee_budget → buy YES on all,
sum_yes_bid > 1 + fee_budget → buy NO on all. Per-leg sizing = max_position
/ n_legs. n < 2 short-circuit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: find_fade_opportunities (cumulative + bucketed)

**Files:**
- Modify: `core/ladder_framework.py`
- Modify: `tests/test_ladder_framework.py`

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_ladder_framework.py`:
```python
from core.ladder_framework import find_fade_opportunities


def test_fade_fires_on_cumulative_when_arb_too_small():
    # Inversion 0.05 — too small for arb at fee 0.07 but ≥ MIN_FADE_INVERSION.
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.45, yes_bid=0.43),
        _leg("T2", 2.0, yes_ask=0.52, yes_bid=0.50),
    ])
    fades = find_fade_opportunities(g, fee=0.07, min_inversion=0.05, max_size_usdc=20.0)
    assert len(fades) == 1
    assert fades[0].ticker == "T1"
    assert fades[0].side == "yes"
    assert fades[0].inversion_amount >= 0.05


def test_fade_skips_below_min_inversion():
    g = _cum_group([
        _leg("T1", 1.0, yes_ask=0.50, yes_bid=0.48),
        _leg("T2", 2.0, yes_ask=0.52, yes_bid=0.50),
    ])
    fades = find_fade_opportunities(g, fee=0.07, min_inversion=0.05, max_size_usdc=20.0)
    assert fades == []


def test_fade_picks_most_underpriced_leg_in_bucketed():
    # implied(C50) = 1 - (0.18+0.50+0.20) = 0.12. yes_ask(C50) = 0.04 → deviation 0.08.
    # implied(H25) = 1 - (0.04+0.18+0.50) = 0.28. yes_ask(H25) = 0.20 → deviation 0.08.
    # implied(C25) = 1 - (0.04+0.50+0.20) = 0.26. yes_ask = 0.18 → deviation 0.08. (tie)
    # implied(H0) = 1 - (0.04+0.18+0.20) = 0.58. yes_ask = 0.50 → deviation 0.08. (tie)
    # Pick first by sort order on tie.
    g = _bucket_group([
        _leg("C50", -0.50, yes_ask=0.04, yes_bid=0.03),
        _leg("C25", -0.25, yes_ask=0.18, yes_bid=0.16),
        _leg("H0",   0.00, yes_ask=0.50, yes_bid=0.48),
        _leg("H25",  0.25, yes_ask=0.20, yes_bid=0.18),
    ])
    fades = find_fade_opportunities(g, fee=0.07, min_inversion=0.05, max_size_usdc=20.0)
    assert len(fades) == 1
    assert fades[0].inversion_amount > 0.05
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 3 NEW tests FAIL with `ImportError`.

- [ ] **Step 5.3: Write minimal implementation**

Append to `core/ladder_framework.py`:
```python
def find_fade_opportunities(
    group: LadderGroup,
    fee: float,
    min_inversion: float,
    max_size_usdc: float,
) -> list[FadeTrade]:
    """Single-leg directional fade when arb path is unavailable.

    cumulative: pick easier-threshold leg whose yes_ask is below stricter
                leg's yes_bid by ≥ min_inversion.
    bucketed:   pick leg whose yes_ask is most underpriced relative to
                implied probability (= 1 - sum_others_yes_mid).
    """
    fades: list[FadeTrade] = []
    if group.ladder_type == "cumulative":
        legs = group.legs
        for i in range(len(legs) - 1):
            t1, t2 = legs[i], legs[i + 1]
            inv = t2.yes_bid - t1.yes_ask
            if inv >= min_inversion:
                fades.append(FadeTrade(
                    group_key=f"{group.series}-{group.event_key}",
                    ticker=t1.ticker, side="yes",
                    size_usdc=max_size_usdc, price=t1.yes_ask,
                    inversion_amount=round(inv, 4),
                ))
    elif group.ladder_type == "bucketed":
        legs = group.legs
        if len(legs) < 2:
            return fades
        # implied(leg_i) = 1 - sum(yes_mid for other legs)
        yes_mid = [(l.yes_ask + l.yes_bid) / 2.0 for l in legs]
        sum_mid = sum(yes_mid)
        best_idx = -1
        best_dev = -1.0
        for i, l in enumerate(legs):
            implied = 1.0 - (sum_mid - yes_mid[i])
            dev = implied - l.yes_ask
            if dev > best_dev:
                best_dev = dev
                best_idx = i
        if best_dev >= min_inversion and best_idx >= 0:
            target = legs[best_idx]
            fades.append(FadeTrade(
                group_key=f"{group.series}-{group.event_key}",
                ticker=target.ticker, side="yes",
                size_usdc=max_size_usdc, price=target.yes_ask,
                inversion_amount=round(best_dev, 4),
            ))
    return fades
```

- [ ] **Step 5.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_framework.py -v`
Expected: 18 tests PASS.

- [ ] **Step 5.5: Commit**

```bash
git add core/ladder_framework.py tests/test_ladder_framework.py
git commit -m "$(cat <<'EOF'
feat(ladder): single-leg fade fallback for cumulative + bucketed

cumulative: pick easier-threshold leg with inversion ≥ min. bucketed: pick
leg with largest implied-vs-ask deviation (uses yes_mid for implied).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: ladder_violations table + DB write helper

**Files:**
- Modify: `core/database.py`
- Create: `tests/test_ladder_violations_db.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_ladder_violations_db.py
import asyncio
import os
import tempfile

import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_ladder_violation_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_ladder_violation(
            series="KXBTC", event_key="26MAY09-1500",
            ladder_type="cumulative",
            leg_a_ticker="KXBTC-26MAY09-1500-B70250",
            leg_b_ticker="KXBTC-26MAY09-1500-B70350",
            inversion_amount=0.07,
            leg_a_depth=120.0, leg_b_depth=80.0,
            action_taken="arb",
            sub_strategy="ladder_crypto",
        )
        rows = await db.fetch_recent_ladder_violations("ladder_crypto", limit=5)
        assert len(rows) == 1
        r = rows[0]
        assert r["series"] == "KXBTC"
        assert r["inversion_amount"] == 0.07
        assert r["action_taken"] == "arb"
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_violations_db.py -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'record_ladder_violation'`.

- [ ] **Step 6.3: Add CREATE TABLE + helpers to `core/database.py`**

In `core/database.py`, inside `initialize()` after the existing `odds_observations` block, add:
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
                    sub_strategy TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lv_strategy_ts "
                "ON ladder_violations(sub_strategy, ts)"
            )
```

Then add two methods to the `Database` class (anywhere convenient, e.g., near `has_existing_trade`):
```python
    async def record_ladder_violation(
        self, *, series: str, event_key: str, ladder_type: str,
        leg_a_ticker: str, leg_b_ticker: str | None,
        inversion_amount: float,
        leg_a_depth: float | None, leg_b_depth: float | None,
        action_taken: str, sub_strategy: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO ladder_violations "
                "(ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker, "
                " inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker,
                 inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_ladder_violations(
        self, sub_strategy: str, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM ladder_violations WHERE sub_strategy = ? "
                "ORDER BY ts DESC LIMIT ?",
                (sub_strategy, limit),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
```

- [ ] **Step 6.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_violations_db.py -v`
Expected: PASS.

- [ ] **Step 6.5: Commit**

```bash
git add core/database.py tests/test_ladder_violations_db.py
git commit -m "$(cat <<'EOF'
feat(db): add ladder_violations table + record/fetch helpers

Schema-only addition for Phase 0 prevalence research; sub-strategy logs
each detected inversion + action_taken (arb/fade/skip_depth/skip_edge).
Indexed on (sub_strategy, ts) for fast per-strategy queries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Extend has_existing_trade gate to ladder_* strategies

**Files:**
- Modify: `core/order_manager.py` (4 sites)
- Create: `tests/test_ladder_dedup_guard.py`

- [ ] **Step 7.1: Write the failing test (V1 jam scenario)**

```python
# tests/test_ladder_dedup_guard.py
import asyncio
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_ladder_strategy_blocked_when_paper_filled_exists():
    """Reproduces V1 over-fill: ladder_shutdown attempting re-entry on a
    market that already has paper_filled in DB must be blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"  # ensure paper path
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()

        # Seed an existing paper_filled trade for ladder_shutdown
        await db.record_trade(
            strategy="ladder_shutdown",
            market_id="KXNUMSHUTDOWNS-27JAN01-T2",
            direction="yes", price=0.30, size=10.0, fee=0.07,
            status="paper_filled",
        )

        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0  # bypass _persist_bankroll dependency

        result = await om._paper_market_order(
            ticker="KXNUMSHUTDOWNS-27JAN01-T2",
            side="yes", size_usdc=10.0, price=0.30,
            strategy="ladder_shutdown",
            market_id="KXNUMSHUTDOWNS-27JAN01-T2",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_dedup_guard.py -v`
Expected: FAIL — current gate is `strategy.startswith("sports_")`, ladder_* skips dedup → second insert proceeds.

- [ ] **Step 7.3: Extend dedup gate at 4 sites in `core/order_manager.py`**

For each of the four matched lines (438, 465, 485, 565, 716 — confirm exact lines via grep), change:
```python
if strategy.startswith("sports_") and await self._db.has_existing_trade(strategy, market_id):
```
to:
```python
if (strategy.startswith("sports_") or strategy.startswith("ladder_")) \
        and await self._db.has_existing_trade(strategy, market_id):
```

Verify all four sites updated:
```bash
grep -n 'startswith("sports_") or strategy.startswith("ladder_")' core/order_manager.py | wc -l
```
Expected: 4 (or whatever count grep originally found at sports_-only).

Also update the comment at `core/order_manager.py:432-437` to mention ladder_* as another one-shot category:
```python
        # DB-side dedup applies to:
        #   sports_* — single game has one outcome, re-entry is always a bug.
        #   ladder_* — single Kalshi event ladder leg has one outcome; V1
        #              shutdown_fade over-fired 13× on KXNUMSHUTDOWNS by
        #              skipping this guard. See known_bugs.md.
        # Other strategies (mentions, market_maker, spread_scanner)
        # legitimately re-enter the same ticker.
```

- [ ] **Step 7.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_dedup_guard.py -v`
Expected: PASS.

Also re-run sports tests to verify no regression:
```bash
pytest tests/ -k "sports or order_manager" -v
```
Expected: existing tests still PASS.

- [ ] **Step 7.5: Commit**

```bash
git add core/order_manager.py tests/test_ladder_dedup_guard.py
git commit -m "$(cat <<'EOF'
fix(order_manager): extend has_existing_trade dedup gate to ladder_* strategies

V1 shutdown_fade over-fired 13× because dedup gate was sports-only. New
ladder_* sub-strategies must respect the same one-shot-per-(strategy,
market_id) invariant — extends the gate at 4 sites (paper_market,
paper_limit, live_market, +balance-check site). Updates comment to
record the V1 root cause.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Ladder config knobs

**Files:**
- Modify: `core/config.py`
- Create: `tests/test_ladder_config.py`

- [ ] **Step 8.1: Write the failing test**

```python
# tests/test_ladder_config.py
import os
from unittest.mock import patch

from core.config import Config


def test_ladder_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for key in ("LADDER_MIN_ARB_EDGE_USDC", "LADDER_MIN_FADE_INVERSION",
                    "LADDER_MIN_DEPTH_USDC", "LADDER_MAX_POSITION_PER_GROUP",
                    "LADDER_STALE_ORDERBOOK_SEC"):
            os.environ.pop(key, None)
        cfg = Config()
        assert cfg.LADDER_MIN_ARB_EDGE_USDC == 0.02
        assert cfg.LADDER_MIN_FADE_INVERSION == 0.05
        assert cfg.LADDER_MIN_DEPTH_USDC == 30.0
        assert cfg.LADDER_MAX_POSITION_PER_GROUP == 50.0
        assert cfg.LADDER_STALE_ORDERBOOK_SEC == 10.0


def test_ladder_config_env_override():
    with patch.dict(os.environ, {"LADDER_MIN_ARB_EDGE_USDC": "0.05",
                                  "LADDER_MAX_POSITION_PER_GROUP": "75"}):
        cfg = Config()
        assert cfg.LADDER_MIN_ARB_EDGE_USDC == 0.05
        assert cfg.LADDER_MAX_POSITION_PER_GROUP == 75.0
```

- [ ] **Step 8.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'LADDER_MIN_ARB_EDGE_USDC'`.

- [ ] **Step 8.3: Add config attributes**

Append to the body of `Config.__init__` (or wherever other env-parsed attrs live) in `core/config.py`:
```python
        # Ladder consistency arb (A1) — 4 sub-strategy shared knobs.
        # Per-sub-strategy override pattern: each sub-strategy class can
        # override class-level constants instead of env vars.
        self.LADDER_MIN_ARB_EDGE_USDC = float(os.getenv("LADDER_MIN_ARB_EDGE_USDC", "0.02"))
        self.LADDER_MIN_FADE_INVERSION = float(os.getenv("LADDER_MIN_FADE_INVERSION", "0.05"))
        self.LADDER_MIN_DEPTH_USDC = float(os.getenv("LADDER_MIN_DEPTH_USDC", "30"))
        self.LADDER_MAX_POSITION_PER_GROUP = float(os.getenv("LADDER_MAX_POSITION_PER_GROUP", "50"))
        self.LADDER_STALE_ORDERBOOK_SEC = float(os.getenv("LADDER_STALE_ORDERBOOK_SEC", "10"))
```

If `import os` is missing at top of `core/config.py`, add it.

- [ ] **Step 8.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_config.py -v`
Expected: PASS.

- [ ] **Step 8.5: Commit**

```bash
git add core/config.py tests/test_ladder_config.py
git commit -m "$(cat <<'EOF'
feat(config): add LADDER_* env knobs (5 thresholds)

Shared by 4 ladder_* sub-strategies. Defaults match spec: arb edge 2c, fade
inversion 5c, depth $30, max position $50/group, stale orderbook 10s.
Per-sub-strategy override via class constants (sports_underdog_v37 pattern).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: ladder_shutdown.py (smallest universe + V1 dedup integration test)

**Files:**
- Create: `strategies/ladder_shutdown.py`
- Create: `tests/test_ladder_shutdown_strategy.py`

- [ ] **Step 9.1: Write the failing test**

```python
# tests/test_ladder_shutdown_strategy.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.ladder_shutdown import LadderShutdownStrategy


@pytest.mark.asyncio
async def test_suffix_parser_t_and_g():
    s = LadderShutdownStrategy(
        config=MagicMock(LADDER_MIN_ARB_EDGE_USDC=0.02,
                          LADDER_MIN_FADE_INVERSION=0.05,
                          LADDER_MIN_DEPTH_USDC=30.0,
                          LADDER_MAX_POSITION_PER_GROUP=50.0,
                          KALSHI_FEE_PER_CONTRACT=0.07),
        order_manager=MagicMock(), market_scanner=MagicMock(),
        kalshi_client=MagicMock(),
    )
    assert s._parse_suffix("T2") == 2.0
    assert s._parse_suffix("T17") == 17.0
    assert s._parse_suffix("G95") == 95.0
    assert s._parse_suffix("BAD") is None


@pytest.mark.asyncio
async def test_event_key_extraction():
    s = LadderShutdownStrategy(
        config=MagicMock(), order_manager=MagicMock(),
        market_scanner=MagicMock(), kalshi_client=MagicMock(),
    )
    m = {"ticker": "KXNUMSHUTDOWNS-27JAN01-T2"}
    assert s._extract_event_key(m) == "27JAN01"


@pytest.mark.asyncio
async def test_strategy_universe_filter_includes_only_targeted_series():
    s = LadderShutdownStrategy(
        config=MagicMock(), order_manager=MagicMock(),
        market_scanner=MagicMock(), kalshi_client=MagicMock(),
    )
    assert s._is_target_market({"ticker": "KXNUMSHUTDOWNS-27JAN01-T2"}) is True
    assert s._is_target_market({"ticker": "KXGOVTSHUTLENGTH-26FEB07-G95"}) is True
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
    assert s._is_target_market({"ticker": "KXFEDMENTION-26APR-YIEL"}) is False
```

- [ ] **Step 9.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_shutdown_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.ladder_shutdown'`.

- [ ] **Step 9.3: Write minimal implementation**

```python
# strategies/ladder_shutdown.py
"""Ladder Shutdown — KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH cumulative ladders.

Smallest universe (4 + variable open) — designed primarily as the V1
shutdown_fade replacement that respects the DB dedup invariant. V1 had
121 paper_filled rows on KXNUMSHUTDOWNS-T2/T3 by skipping the dedup
guard; that guard now extends to ladder_* (see core/order_manager.py
comment block).
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_shutdown")

TARGET_SERIES = frozenset({"KXNUMSHUTDOWNS", "KXGOVTSHUTLENGTH"})


class LadderShutdownStrategy:
    STRATEGY_NAME = "ladder_shutdown"
    LADDER_TYPE = "cumulative"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if len(suffix) < 2:
            return None
        if suffix[0] not in ("T", "G"):
            return None
        try:
            return float(suffix[1:])
        except ValueError:
            return None

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        parts = m.get("ticker", "").split("-")
        # KXNUMSHUTDOWNS-27JAN01-T2 → 27JAN01
        return parts[1] if len(parts) >= 3 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        ticker = m.get("ticker", "")
        series = ticker.split("-")[0]
        return series in TARGET_SERIES

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        # Pull open universe — caller-side filter
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        # Enrich with depth (caller responsibility — sub-strategy fills in)
        enriched = []
        for m in markets:
            yes_price, no_price = self._client.get_market_prices(m["ticker"])
            if yes_price is None or no_price is None:
                continue
            yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
            enriched.append({
                **m,
                "yes_ask": yes_price, "yes_bid": yes_price,
                "no_ask": no_price, "no_bid": no_price,
                "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
            })

        groups = group_legs_into_ladders(
            enriched, suffix_parser=self._parse_suffix,
            ladder_type=self.LADDER_TYPE,
            event_key_extractor=self._extract_event_key,
            direction=self.DIRECTION,
        )
        for g in groups:
            await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb_trades = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb_trades:
            await self._fire_arb(arb_trades[0])
            return  # one fire per group per scan
        fade_trades = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade_trades:
            await self._fire_fade(fade_trades[0])

    async def _fire_arb(self, trade):
        logger.info(
            f"[LADDER_SHUTDOWN] ARB {trade.group_key} "
            f"edge=${trade.expected_edge_usdc:.2f}"
        )
        for leg in trade.legs:
            await self._order_manager.market_order(
                ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                price=leg.price, strategy=self.STRATEGY_NAME,
                market_id=leg.ticker,
            )

    async def _fire_fade(self, trade):
        logger.info(
            f"[LADDER_SHUTDOWN] FADE {trade.ticker} {trade.side} "
            f"inv={trade.inversion_amount:.4f}"
        )
        await self._order_manager.market_order(
            ticker=trade.ticker, side=trade.side, size_usdc=trade.size_usdc,
            price=trade.price, strategy=self.STRATEGY_NAME,
            market_id=trade.ticker,
        )
```

- [ ] **Step 9.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_shutdown_strategy.py -v`
Expected: 3 tests PASS.

Also re-run the V1 dedup test to confirm it still works for ladder_shutdown:
```bash
pytest tests/test_ladder_dedup_guard.py -v
```
Expected: PASS (already covered by Task 7).

- [ ] **Step 9.5: Commit**

```bash
git add strategies/ladder_shutdown.py tests/test_ladder_shutdown_strategy.py
git commit -m "$(cat <<'EOF'
feat(ladder): add ladder_shutdown strategy (KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH)

Smallest universe (V1 dedup test bed). Cumulative asc, T<int>/G<int>
suffix. Hybrid arb-then-fade via shared ladder_framework. DB dedup guard
already extended in Task 7 prevents V1's 121-row over-fill regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: ladder_approval.py (cumulative with desc handling)

**Files:**
- Create: `strategies/ladder_approval.py`
- Create: `tests/test_ladder_approval_strategy.py`

- [ ] **Step 10.1: Write the failing test**

```python
# tests/test_ladder_approval_strategy.py
from unittest.mock import MagicMock

import pytest

from strategies.ladder_approval import LadderApprovalStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=20.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_integer_suffix():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("47") == 47.0
    assert s._parse_suffix("3") == 3.0
    assert s._parse_suffix("BELOW") is None


def test_direction_for_below_series():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._direction_for("KXTRUMPAPPROVALYEAR") == "asc"
    assert s._direction_for("KXTRUMPAPPROVALBELOW") == "desc"
    assert s._direction_for("KX538APPROVE") == "asc"
    assert s._direction_for("UNKNOWN") == "asc"  # default


def test_universe_filter():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXTRUMPAPPROVALYEAR-26-47"}) is True
    assert s._is_target_market({"ticker": "KXTRUMPAPPROVALBELOW-26MAY-37"}) is True
    assert s._is_target_market({"ticker": "KX538APPROVE-26W19-47"}) is True
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
```

- [ ] **Step 10.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_approval_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 10.3: Write minimal implementation**

```python
# strategies/ladder_approval.py
"""Ladder Approval — KXTRUMPAPPROVAL*, KX538APPROVE* cumulative ladders.

Includes both asc ("≥X%" — KXTRUMPAPPROVALYEAR, KX538APPROVE*) and desc
("≤X%" — KXTRUMPAPPROVALBELOW). Sub-strategy partitions universe by series
prefix and runs framework once per direction. Approval prices move slowly
→ ladder violations are typically structural and persist long enough for
the fade fallback to converge.
"""
import logging
from collections import defaultdict
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_approval")

ASC_SERIES = frozenset({
    "KXTRUMPAPPROVALYEAR", "KX538APPROVE", "KX538APPROVEMAX",
    "KX538APPROVEMIN", "KX538APPROVEY",
})
DESC_SERIES = frozenset({"KXTRUMPAPPROVALBELOW"})
TARGET_SERIES = ASC_SERIES | DESC_SERIES


class LadderApprovalStrategy:
    STRATEGY_NAME = "ladder_approval"
    LADDER_TYPE = "cumulative"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if not suffix.isdigit():
            return None
        return float(suffix)

    @staticmethod
    def _direction_for(series: str) -> str:
        if series in DESC_SERIES:
            return "desc"
        return "asc"

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        # KXTRUMPAPPROVALYEAR-26-47 → "26"
        # KX538APPROVE-26W19-47 → "26W19"
        # KXTRUMPAPPROVALBELOW-26MAY-37 → "26MAY"
        parts = m.get("ticker", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 3 else ""

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        enriched = []
        for m in markets:
            yes_price, no_price = self._client.get_market_prices(m["ticker"])
            if yes_price is None or no_price is None:
                continue
            yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
            enriched.append({
                **m,
                "yes_ask": yes_price, "yes_bid": yes_price,
                "no_ask": no_price, "no_bid": no_price,
                "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
            })

        # Partition by direction (asc vs desc series)
        by_dir = defaultdict(list)
        for m in enriched:
            series = m["ticker"].split("-")[0]
            by_dir[self._direction_for(series)].append(m)

        cfg = self.config
        for direction, ms in by_dir.items():
            groups = group_legs_into_ladders(
                ms, suffix_parser=self._parse_suffix,
                ladder_type=self.LADDER_TYPE,
                event_key_extractor=self._extract_event_key,
                direction=direction,
            )
            for g in groups:
                await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb:
            for leg in arb[0].legs:
                await self._order_manager.market_order(
                    ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                    price=leg.price, strategy=self.STRATEGY_NAME, market_id=leg.ticker,
                )
            return
        fade = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade:
            t = fade[0]
            await self._order_manager.market_order(
                ticker=t.ticker, side=t.side, size_usdc=t.size_usdc,
                price=t.price, strategy=self.STRATEGY_NAME, market_id=t.ticker,
            )
```

- [ ] **Step 10.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_approval_strategy.py -v`
Expected: 3 tests PASS.

- [ ] **Step 10.5: Commit**

```bash
git add strategies/ladder_approval.py tests/test_ladder_approval_strategy.py
git commit -m "$(cat <<'EOF'
feat(ladder): add ladder_approval (KXTRUMPAPPROVAL*, KX538APPROVE*)

Cumulative with per-series direction routing — APPROVALBELOW desc, others
asc. Partitions universe by direction and invokes framework once per
direction (sort-only differs).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: ladder_fed.py (bucketed)

**Files:**
- Create: `strategies/ladder_fed.py`
- Create: `tests/test_ladder_fed_strategy.py`

- [ ] **Step 11.1: Write the failing test**

```python
# tests/test_ladder_fed_strategy.py
from unittest.mock import MagicMock

import pytest

from strategies.ladder_fed import LadderFedStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_c_h_suffix():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("C25") == -25.0
    assert s._parse_suffix("C50") == -50.0
    assert s._parse_suffix("H0") == 0.0
    assert s._parse_suffix("H25") == 25.0
    assert s._parse_suffix("BAD") is None


def test_event_key_is_fomc_meeting_date():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # KXFEDDECISION-26JUN-C25 → 26JUN
    assert s._extract_event_key({"ticker": "KXFEDDECISION-26JUN-C25"}) == "26JUN"


def test_universe_filter():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is True
    assert s._is_target_market({"ticker": "KXFEDMENTION-26APR-YIEL"}) is False
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
```

- [ ] **Step 11.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_fed_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 11.3: Write minimal implementation**

```python
# strategies/ladder_fed.py
"""Ladder Fed — KXFEDDECISION bucketed mutually-exclusive outcome ladder.

Each FOMC meeting becomes one bucketed group: cuts (C25, C50, ...), holds
(H0), hikes (H25, ...). sum_yes ≈ 1 invariant. 8 meetings/yr → mostly
idle; primary fire window is the 2 weeks pre-FOMC reprice.

Conflict with C1 (FOMC window sub-project, separate spec): if both
strategies fire on the same ticker, ladder_fed has dedup priority via
the standard DB has_existing_trade gate (extended to ladder_* in Task 7).
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_fed")

TARGET_SERIES = frozenset({"KXFEDDECISION"})


class LadderFedStrategy:
    STRATEGY_NAME = "ladder_fed"
    LADDER_TYPE = "bucketed"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if len(suffix) < 2:
            return None
        head, tail = suffix[0], suffix[1:]
        if head not in ("C", "H"):
            return None
        try:
            mag = float(tail)
        except ValueError:
            return None
        return -mag if head == "C" else mag

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        parts = m.get("ticker", "").split("-")
        return parts[1] if len(parts) >= 3 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        enriched = []
        for m in markets:
            yes_price, no_price = self._client.get_market_prices(m["ticker"])
            if yes_price is None or no_price is None:
                continue
            yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
            enriched.append({
                **m,
                "yes_ask": yes_price, "yes_bid": yes_price,
                "no_ask": no_price, "no_bid": no_price,
                "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
            })

        groups = group_legs_into_ladders(
            enriched, suffix_parser=self._parse_suffix,
            ladder_type=self.LADDER_TYPE,
            event_key_extractor=self._extract_event_key,
            direction=self.DIRECTION,
        )
        for g in groups:
            await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb:
            for leg in arb[0].legs:
                await self._order_manager.market_order(
                    ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                    price=leg.price, strategy=self.STRATEGY_NAME, market_id=leg.ticker,
                )
            return
        fade = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade:
            t = fade[0]
            await self._order_manager.market_order(
                ticker=t.ticker, side=t.side, size_usdc=t.size_usdc,
                price=t.price, strategy=self.STRATEGY_NAME, market_id=t.ticker,
            )
```

- [ ] **Step 11.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_fed_strategy.py -v`
Expected: 3 tests PASS.

- [ ] **Step 11.5: Commit**

```bash
git add strategies/ladder_fed.py tests/test_ladder_fed_strategy.py
git commit -m "$(cat <<'EOF'
feat(ladder): add ladder_fed (KXFEDDECISION bucketed sum-to-1)

C<bps> = cut, H<bps> = hold/hike. Idle outside the FOMC reprice window.
Dedup priority over C1 FOMC window sub-project via standard DB guard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: ladder_crypto.py (largest universe + ATM ±2 leg filter)

**Files:**
- Create: `strategies/ladder_crypto.py`
- Create: `tests/test_ladder_crypto_strategy.py`

- [ ] **Step 12.1: Write the failing test**

```python
# tests/test_ladder_crypto_strategy.py
from unittest.mock import MagicMock

import pytest

from strategies.ladder_crypto import LadderCryptoStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        LADDER_STALE_ORDERBOOK_SEC=10.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_b_suffix():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("B70250") == 70250.0
    assert s._parse_suffix("B1310") == 1310.0
    assert s._parse_suffix("BAD") is None
    assert s._parse_suffix("") is None


def test_event_key_includes_close_window():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # KXBTC-26MAY09-1500-B70250 → 26MAY09-1500
    assert s._extract_event_key(
        {"ticker": "KXBTC-26MAY09-1500-B70250"}
    ) == "26MAY09-1500"


def test_atm_window_filter_keeps_only_within_n():
    """Leg whose threshold is within ATM ± window survives; far OTM/ITM dropped."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    legs = [
        {"ticker": f"KXBTC-26MAY09-1500-B{p}",
         "yes_ask": 0.5, "yes_bid": 0.48, "no_ask": 0.52, "no_bid": 0.50,
         "yes_depth": 100.0, "no_depth": 100.0}
        for p in (70000, 70100, 70200, 70300, 70400, 70500, 70600)
    ]
    # ATM = 70300. Window = ±2 buckets ($100 each) → keep 70100..70500
    filtered = s._atm_window_filter(legs, atm_price=70300.0, window_n=2,
                                     bucket_size=100.0)
    keep = sorted(int(m["ticker"].rsplit("-B", 1)[-1]) for m in filtered)
    assert keep == [70100, 70200, 70300, 70400, 70500]


def test_universe_filter():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is True
    assert s._is_target_market({"ticker": "KXETH-26MAY09-1500-B1310"}) is True
    assert s._is_target_market({"ticker": "KXSOL-26MAY09-1500-B100"}) is False
```

- [ ] **Step 12.2: Run test to verify it fails**

Run: `pytest tests/test_ladder_crypto_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 12.3: Write minimal implementation**

```python
# strategies/ladder_crypto.py
"""Ladder Crypto — KXBTC, KXETH cumulative price-bucket ladders.

KXBTC has $100 grid, KXETH has $40 grid → typically ~50 legs per close
event. ATM ±N filter narrows to the active leg cluster (far OTM/ITM
have wide spreads + thin depth). Spot anchor (Coinbase WS) is deferred
to follow-up — Open question 4 in spec.
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_crypto")

TARGET_SERIES = frozenset({"KXBTC", "KXETH"})
BUCKET_SIZE_BY_SERIES = {"KXBTC": 100.0, "KXETH": 40.0}
ATM_WINDOW_N = 2  # keep ATM ± N buckets only


class LadderCryptoStrategy:
    STRATEGY_NAME = "ladder_crypto"
    LADDER_TYPE = "cumulative"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if not suffix.startswith("B") or len(suffix) < 2:
            return None
        try:
            return float(suffix[1:])
        except ValueError:
            return None

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        # KXBTC-26MAY09-1500-B70250 → 26MAY09-1500
        parts = m.get("ticker", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 4 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _atm_proxy(legs: list[dict]) -> Optional[float]:
        """Use the leg whose yes_ask is closest to 0.50 as ATM proxy.

        This avoids needing a live Coinbase feed for the v0 cut. Spot
        anchor (real Coinbase WS) is a follow-up.
        """
        best = None
        best_dist = 1.0
        for m in legs:
            ya = float(m.get("yes_ask") or 0.0)
            d = abs(ya - 0.50)
            if d < best_dist:
                best_dist = d
                # Threshold from suffix
                suf = m["ticker"].rsplit("-", 1)[-1]
                try:
                    best = float(suf[1:])
                except ValueError:
                    continue
        return best

    @staticmethod
    def _atm_window_filter(legs: list[dict], atm_price: float, window_n: int,
                           bucket_size: float) -> list[dict]:
        lo = atm_price - window_n * bucket_size
        hi = atm_price + window_n * bucket_size
        out = []
        for m in legs:
            suf = m["ticker"].rsplit("-", 1)[-1]
            try:
                thr = float(suf[1:])
            except ValueError:
                continue
            if lo <= thr <= hi:
                out.append(m)
        return out

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        # Partition by series (each has its own bucket size)
        by_series: dict[str, list[dict]] = {}
        for m in markets:
            series = m["ticker"].split("-")[0]
            by_series.setdefault(series, []).append(m)

        for series, ms in by_series.items():
            enriched = []
            for m in ms:
                yes_price, no_price = self._client.get_market_prices(m["ticker"])
                if yes_price is None or no_price is None:
                    continue
                yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
                enriched.append({
                    **m,
                    "yes_ask": yes_price, "yes_bid": yes_price,
                    "no_ask": no_price, "no_bid": no_price,
                    "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
                })

            atm = self._atm_proxy(enriched)
            if atm is None:
                continue
            bucket_size = BUCKET_SIZE_BY_SERIES.get(series, 100.0)
            atm_legs = self._atm_window_filter(enriched, atm, ATM_WINDOW_N, bucket_size)
            if not atm_legs:
                continue

            groups = group_legs_into_ladders(
                atm_legs, suffix_parser=self._parse_suffix,
                ladder_type=self.LADDER_TYPE,
                event_key_extractor=self._extract_event_key,
                direction=self.DIRECTION,
            )
            for g in groups:
                await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb:
            for leg in arb[0].legs:
                await self._order_manager.market_order(
                    ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                    price=leg.price, strategy=self.STRATEGY_NAME, market_id=leg.ticker,
                )
            return
        fade = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade:
            t = fade[0]
            await self._order_manager.market_order(
                ticker=t.ticker, side=t.side, size_usdc=t.size_usdc,
                price=t.price, strategy=self.STRATEGY_NAME, market_id=t.ticker,
            )
```

- [ ] **Step 12.4: Run test to verify it passes**

Run: `pytest tests/test_ladder_crypto_strategy.py -v`
Expected: 4 tests PASS.

- [ ] **Step 12.5: Commit**

```bash
git add strategies/ladder_crypto.py tests/test_ladder_crypto_strategy.py
git commit -m "$(cat <<'EOF'
feat(ladder): add ladder_crypto (KXBTC, KXETH ATM ±2 cumulative)

ATM proxy = leg whose yes_ask closest to 0.50 (avoids needing live
Coinbase feed for v0). Per-series bucket size ($100 BTC, $40 ETH). Spot
anchor (real Coinbase WS) deferred to follow-up — see spec open Q4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: main.py registration

**Files:**
- Modify: `main.py`

- [ ] **Step 13.1: Smoke check (manual) — confirm the app currently starts**

Run: `python -c "import main; print('OK')"`
Expected: prints `OK` (no import errors).

- [ ] **Step 13.2: Add imports**

In `main.py`, near the existing strategy imports (around line 13), add:
```python
from strategies.ladder_crypto import LadderCryptoStrategy
from strategies.ladder_fed import LadderFedStrategy
from strategies.ladder_approval import LadderApprovalStrategy
from strategies.ladder_shutdown import LadderShutdownStrategy
```

- [ ] **Step 13.3: Add instance creation in `_init`**

In `main.py`, after the last `self.sports_*` instance creation (around line 200; locate via `grep -n "self.sports_v4_edge = " main.py`), append:
```python
        # Ladder Consistency Arb (A1) — paper-only at launch.
        # 4 sub-strategies share core/ladder_framework.py. See spec:
        # docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md
        self.ladder_shutdown = LadderShutdownStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.ladder_approval = LadderApprovalStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.ladder_fed = LadderFedStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.ladder_crypto = LadderCryptoStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
```

- [ ] **Step 13.4: Add to `_strategy_loop` strategies list**

In `main.py`, in the `_strategy_loop` method's `strategies = [...]` list (around line 308), append before the closing bracket:
```python
            # Ladder Consistency Arb (A1) — paper-only at launch (no entry in
            # LIVE_STRATEGIES env). Phase 0 prevalence research must validate
            # per-sub-strategy fire rate before live promotion.
            ("ladder_shutdown", self.ladder_shutdown),
            ("ladder_approval", self.ladder_approval),
            ("ladder_fed", self.ladder_fed),
            ("ladder_crypto", self.ladder_crypto),
```

- [ ] **Step 13.5: Smoke check after wiring**

Run: `python -c "import main; print('OK')"`
Expected: prints `OK` with no import errors.

Optional: if pytest collects integration tests, run `pytest tests/ -v` to confirm no regression.

- [ ] **Step 13.6: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): register 4 ladder_* sub-strategies (paper-only)

ladder_shutdown / ladder_approval / ladder_fed / ladder_crypto wired into
_strategy_loop. No entry in LIVE_STRATEGIES env → all 4 paper-only at
launch per paper-first rule. Phase 0 prevalence research (Task 14) must
validate fire rate before any live promotion.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Phase 0 prevalence research scanner

**Files:**
- Create: `scripts/research/ladder_violation_scanner.py`

- [ ] **Step 14.1: Write the script**

Note: this is a standalone research script — no TDD framework required. The plan tests it via a 5-min dry run after creation.

```python
# scripts/research/ladder_violation_scanner.py
"""Ladder violation prevalence scanner — Phase 0 of A1.

Polls KXBTC/KXETH/KXFEDDECISION/KXTRUMPAPPROVAL*/KX538APPROVE*/
KXNUMSHUTDOWNS/KXGOVTSHUTLENGTH every minute, runs framework detection
WITHOUT firing trades, logs every detected inversion to
ladder_violations table for offline analysis.

Run for ≥2 weeks before deciding which sub-strategies pass Phase 0
gate (≥1 violation/day + simulated ROI ≥ +1% arb / +3% fade).

Usage:
  python scripts/research/ladder_violation_scanner.py --interval 60 --once
  python scripts/research/ladder_violation_scanner.py --interval 60
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)
from strategies.ladder_crypto import LadderCryptoStrategy
from strategies.ladder_fed import LadderFedStrategy
from strategies.ladder_approval import LadderApprovalStrategy
from strategies.ladder_shutdown import LadderShutdownStrategy

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ladder_scanner")

# Each entry: (sub_strategy_name, target_series, suffix_parser, event_key,
#              ladder_type, direction, parser_strategy_class)
SCANNERS = [
    ("ladder_crypto", ("KXBTC", "KXETH"), LadderCryptoStrategy, "cumulative", "asc"),
    ("ladder_fed", ("KXFEDDECISION",), LadderFedStrategy, "bucketed", "asc"),
    ("ladder_approval",
     ("KXTRUMPAPPROVALYEAR", "KXTRUMPAPPROVALBELOW", "KX538APPROVE",
      "KX538APPROVEMAX", "KX538APPROVEMIN", "KX538APPROVEY"),
     LadderApprovalStrategy, "cumulative", "mixed"),  # asc/desc partitioned per series
    ("ladder_shutdown", ("KXNUMSHUTDOWNS", "KXGOVTSHUTLENGTH"),
     LadderShutdownStrategy, "cumulative", "asc"),
]


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


def normalize_market(m: dict) -> dict:
    """Convert Kalshi cents → dollars for framework consumption."""
    return {
        **m,
        "yes_ask": (m.get("yes_ask") or 0) / 100.0,
        "yes_bid": (m.get("yes_bid") or 0) / 100.0,
        "no_ask": (m.get("no_ask") or 0) / 100.0,
        "no_bid": (m.get("no_bid") or 0) / 100.0,
        # Phase 0: depth probe omitted (orderbook calls are expensive at
        # scan cadence). depth=999 to bypass framework's depth gate
        # — we still log inversion magnitude for offline depth backfill.
        "yes_depth": 999.0, "no_depth": 999.0,
    }


async def scan_once(client: KalshiClient, db: Database, cfg: Config) -> dict:
    summary = defaultdict(int)
    for entry in SCANNERS:
        sub_name, series_list, parser_cls, ladder_type, direction = entry
        # Build a parser instance to reuse static parser methods
        parser = parser_cls(cfg, None, None, None)
        for series in series_list:
            raw = fetch_open_markets(client, series)
            if not raw:
                continue
            normalized = [normalize_market(m) for m in raw]
            # Approval has mixed direction — partition per series
            if direction == "mixed":
                d = parser._direction_for(series)  # type: ignore[attr-defined]
            else:
                d = direction
            groups = group_legs_into_ladders(
                normalized, suffix_parser=parser._parse_suffix,
                ladder_type=ladder_type,
                event_key_extractor=parser._extract_event_key,
                direction=d,
            )
            for g in groups:
                arb = find_arb_opportunities(
                    g, fee=cfg.KALSHI_FEE_PER_CONTRACT,
                    min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
                    min_depth_usdc=0.0,  # Phase 0: ignore depth gate
                    max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
                )
                if arb:
                    for t in arb:
                        a, b = (t.legs[0].ticker,
                                t.legs[1].ticker if len(t.legs) > 1 else None)
                        await db.record_ladder_violation(
                            series=g.series, event_key=g.event_key,
                            ladder_type=ladder_type,
                            leg_a_ticker=a, leg_b_ticker=b,
                            inversion_amount=t.expected_edge_usdc,
                            leg_a_depth=None, leg_b_depth=None,
                            action_taken="arb", sub_strategy=sub_name,
                        )
                        summary[f"{sub_name}/arb"] += 1
                else:
                    fade = find_fade_opportunities(
                        g, fee=cfg.KALSHI_FEE_PER_CONTRACT,
                        min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
                        max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
                    )
                    if fade:
                        for f in fade:
                            await db.record_ladder_violation(
                                series=g.series, event_key=g.event_key,
                                ladder_type=ladder_type,
                                leg_a_ticker=f.ticker, leg_b_ticker=None,
                                inversion_amount=f.inversion_amount,
                                leg_a_depth=None, leg_b_depth=None,
                                action_taken="fade", sub_strategy=sub_name,
                            )
                            summary[f"{sub_name}/fade"] += 1
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

    log.info(f"Starting ladder violation scanner — interval={args.interval}s")
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
        await asyncio.sleep(max(1, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 14.2: Run a single dry scan**

Run: `python scripts/research/ladder_violation_scanner.py --once`
Expected: 1-3 minute runtime, INFO logs from each scanner, final `scan complete in Xs: {sub_strategy/action: count}` summary.

If any sub-strategy returns 0 results across all series (likely if no current inversion), that's normal — Phase 0 is a 2-week observation window.

Verify DB rows landed:
```bash
sqlite3 data/bot.db "SELECT sub_strategy, action_taken, COUNT(*) FROM ladder_violations GROUP BY sub_strategy, action_taken;"
```

- [ ] **Step 14.3: Commit**

```bash
git add scripts/research/ladder_violation_scanner.py
git commit -m "$(cat <<'EOF'
feat(ladder): Phase 0 prevalence scanner (no-fire detection logger)

Polls all 4 sub-strategy universes at configurable cadence, runs framework
detection without trading, logs every inversion to ladder_violations
table. 2-week run feeds Phase 0 gate (≥1/day, ROI ≥+1% arb / +3% fade).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

After completing all 14 tasks, perform the following checks before declaring the plan complete:

**1. Spec coverage:**
- [ ] core/ladder_framework.py exists with 4 dataclasses + 3 detection functions (Tasks 1-5) ✓
- [ ] 4 sub-strategies (Tasks 9-12) ✓
- [ ] DB ladder_violations table (Task 6) ✓
- [ ] Dedup gate extended to ladder_* (Task 7) ✓
- [ ] Config knobs (Task 8) ✓
- [ ] main.py registration (Task 13) ✓
- [ ] Phase 0 research script (Task 14) ✓
- [ ] V1 paper_filled dedup integration test (Task 7 + Task 9) ✓
- [ ] Spec mentions Telegram alerts + dashboard panels — NOT covered in this plan. Defer to operational follow-up after Phase 0 confirms viability. (Spec section 4.5 explicit; deferral matches "ship code first, instrument once we know it fires" pragmatism.)
- [ ] Spec mentions promotion_eval ladder_* entries — NOT covered. Defer to Phase 1→2 transition; promotion_eval changes are 1-line additions.
- [ ] Spec mentions Phase 1 paper deployment + Phase 2 promotion criteria — operational, not coding tasks. Out of scope for this plan.

**2. Placeholder scan:** No "TBD", no "implement later" — every task has actual test + impl code.

**3. Type consistency:**
- LadderLeg fields used identically across Tasks 1, 2, 3, 4, 5 ✓
- LadderGroup.legs is `tuple[LadderLeg, ...]` ✓
- ArbTrade.legs is `tuple[ArbLeg, ...]` ✓
- Sub-strategy class API: `(config, order_manager, market_scanner, kalshi_client)` ctor + `scan_and_execute()` method — matches sum_arb pattern ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-ladder-consistency-arb-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
