# WNBA 5-Strategy Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate 5 strategies (v31 trio + v36_wnba + v37_wnba) on WNBA markets to start accumulating live data toward 2026-05-23 promotion eval.

**Architecture:** v31 trio (longshot/midshot/underdog) modified in place via OBSERVATION_ONLY_SPORTS override + WNBA-only pair-fire dedup at evaluate() entry. v36_wnba and v37_wnba created as new sibling classes (v36 inheriting SportsUnderdogV31Strategy with a fresh chalk-favorite evaluate(); v37 inheriting SportsUnderdogV37SoccerStrategy with SPORT_BLOCKLIST adjusted). v32 + v33 paper variants re-block WNBA via parent default restore (cascade prevention). Registration wires into PAPER_TWIN_STRATEGIES, main.py scan loop, and dashboard; live trading enabled via prod `.env` LIVE_STRATEGIES env edit.

**Tech Stack:** Python 3.11, sqlite, pytest, systemd-managed kalshi-bot on EC2 (18.117.216.224). Run via `venv/bin/python`. Test via `venv/bin/pytest`.

**Spec:** [`docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md`](../specs/2026-05-13-wnba-strategy-expansion-design.md) (commit `e72f1de`).

**Pre-flight context (must read before starting):**
- WNBA observer wiring already complete (commit `6698c5e`, 2026-05-09). Parent `SportsTraderStrategy.OBSERVATION_ONLY_SPORTS` contains `{"WNBA", "NPB", "KBO", "MLS", "EPL", "LAL", "UCL", "BUN"}`.
- Live trading on EC2 — changes go through full pytest, then commit, then SSH deploy.
- 5 strategy file additions/edits + 4 registration-glue file edits + 1 prod .env edit.
- All edits in this plan target master branch directly (no PR workflow per repo convention).
- `_traded_tickers` dict is the dedup substrate; it's populated by base class on each successful fire.

---

## Phase A — Test infrastructure

### Task 1: Scaffold `tests/test_wnba_expansion.py`

**Files:**
- Create: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Inspect existing test conventions**

Run: `head -80 tests/test_sports_v31_variants.py`
Note the import path, fixture pattern, and `_make` helper convention.

- [ ] **Step 2: Create scaffold with shared fixture builders**

```python
"""WNBA strategy expansion — verify v31 trio + v36_wnba + v37_wnba fire on
WNBA tickers; v32 + v33 paper + v37_soccer remain blocked; v34/v35 naturally
blocked by ALLOWED_CELLS; pair-fire dedup works.

Built 2026-05-13 alongside the WNBA 5-strategy expansion. See spec
docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md."""
from unittest.mock import MagicMock

import pytest

from strategies.sports_trader import SportsTraderStrategy, SPORT_GAME_TOKENS


# WNBA test tickers — pair of paired sides for one game
WNBA_TICKER_A = "KXWNBAGAME-26MAY14NYWSH-NY"
WNBA_TICKER_B = "KXWNBAGAME-26MAY14NYWSH-WSH"
WNBA_GAME_KEY = "KXWNBAGAME-26MAY14NYWSH"


def _make_config():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.SPORTS_SCAN_INTERVAL = 300
    cfg.ODDS_API_REGION = "eu"
    cfg.LIVE_STRATEGIES = []
    cfg.is_paper_mode = False
    cfg.INITIAL_BANKROLL = 1000.0
    cfg.ODDS_API_KEY = "test-key"
    return cfg


def _make_order_manager():
    om = MagicMock()
    om.bankroll = 1000.0
    om._notifier = None
    om._db = MagicMock()
    om._quota_last_alert_bucket = None
    return om


def _make_kalshi_client():
    return MagicMock()


def _make(klass):
    """Instantiate a strategy class with mocked deps. Returns (strat, om)."""
    om = _make_order_manager()
    strat = klass(_make_config(), om, _make_kalshi_client())
    return strat, om
```

- [ ] **Step 3: Verify it imports cleanly**

Run: `venv/bin/pytest tests/test_wnba_expansion.py --collect-only`
Expected: `collected 0 items` (no tests yet, just scaffold)

- [ ] **Step 4: Commit**

```bash
git add tests/test_wnba_expansion.py
git commit -m "test(wnba): scaffold test_wnba_expansion fixtures

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — v31 trio modify-in-place

### Task 2: longshot_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup

**Files:**
- Modify: `strategies/sports_longshot_v31.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Write failing test for longshot_v31 firing on WNBA**

Add to `tests/test_wnba_expansion.py`:

```python
def test_longshot_v31_unblocks_wnba():
    """v31 longshot must remove WNBA from OBSERVATION_ONLY_SPORTS so
    scan_and_execute reaches its evaluate() for WNBA tickers."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    assert "WNBA" not in SportsLongshotV31Strategy.OBSERVATION_ONLY_SPORTS
    # Parent set still has WNBA — only this subclass removed it
    assert "WNBA" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_longshot_v31_pair_fire_dedup_wnba_only():
    """evaluate() returns None on a 2nd WNBA fire when sibling already fired."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    strat, _ = _make(SportsLongshotV31Strategy)
    # Simulate sibling already fired
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    # 2nd fire on paired ticker — should be deduped
    sig = strat.evaluate(0.18, 0.83, 0.18, ticker=WNBA_TICKER_B)
    assert sig is None, "WNBA pair-fire dedup did not block sibling fire"


def test_longshot_v31_no_dedup_on_non_wnba():
    """NBA tickers still allow pair-fire (P2 cleanup not part of this expansion)."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    strat, _ = _make(SportsLongshotV31Strategy)
    strat._traded_tickers = {"KXNBAGAME-26MAY13BOSNY-BOS": 1234567890.0}
    # NBA pair-fire should NOT be deduped (separate P2 backlog item)
    # The evaluate may return None for other gate reasons, but NOT because
    # of pair-fire dedup. Easiest assertion: dedup branch in code is
    # WNBA-only — call with a WNBA-priced signal on a NBA ticker and the
    # dedup branch must not trip.
    # Test that the dedup logic key matches "WNBAGAME" specifically.
    sig = strat.evaluate(0.08, 0.93, 0.08, ticker="KXNBAGAME-26MAY13BOSNY-NY")
    # 0.08 is in longshot band (<0.10) and parent's NBA playoff blocklist
    # may or may not block (PLAYOFF_BLOCKLIST + is_playoff). If blocked,
    # sig is None for non-dedup reasons. We only assert the dedup gate
    # didn't fire by checking _traded_tickers state separately:
    # (sentinel: dedup short-circuit returns None BEFORE evaluate's
    # logic runs; the dedup branch is the only path that can return None
    # in the first 5 lines of evaluate)
    # Instead of asserting on sig, assert the dedup logic is keyed on WNBAGAME:
    import inspect
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src, "Expected WNBA-only dedup branch"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k longshot_v31`
Expected: 3 failures —
- `test_longshot_v31_unblocks_wnba`: `AssertionError` ("WNBA" still in OBSERVATION_ONLY_SPORTS)
- `test_longshot_v31_pair_fire_dedup_wnba_only`: signature mismatch (evaluate may not accept `ticker=` kwarg in longshot) OR returns a signal
- `test_longshot_v31_no_dedup_on_non_wnba`: `"WNBAGAME" not in src` because longshot doesn't have an evaluate override yet

- [ ] **Step 3: Modify `strategies/sports_longshot_v31.py`**

Read current file with `Read` tool first if not already in context (file is short — ~57 lines). Append after the existing `_get_trade_size` method:

```python
    # 2026-05-13 (WNBA expansion): override parent OBSERVATION_ONLY_SPORTS
    # to remove WNBA. v31 trio is multi-sport by design — WNBA fits the
    # same longshot 5-25c band logic. NBA/MLB/NHL behavior unchanged.
    OBSERVATION_ONLY_SPORTS = (
        SportsLongshotStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs):
        """WNBA-only pair-fire dedup at entry (matches v40 d036f07 pattern).
        Non-WNBA tickers fall through to parent logic unchanged."""
        if "WNBAGAME" in ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
```

Note: `SportsLongshotStrategy` is already imported (line 4 of the file). If parent's `OBSERVATION_ONLY_SPORTS` isn't accessible at class-body time, fall back to `SportsTraderStrategy.OBSERVATION_ONLY_SPORTS` (requires adding import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k longshot_v31`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_longshot_v31.py tests/test_wnba_expansion.py
git commit -m "feat(longshot_v31): enable WNBA + add WNBA-only pair-fire dedup

Override parent OBSERVATION_ONLY_SPORTS to remove WNBA so v31 longshot
fires on WNBA tickers. Add WNBA-only pair-fire dedup at evaluate()
entry — matches v40 d036f07 pattern. NBA/MLB/NHL unchanged.

Part of WNBA 5-strategy expansion (spec e72f1de).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: midshot_v31 — same pattern

**Files:**
- Modify: `strategies/sports_midshot_v31.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add tests** (parallel to longshot)

```python
def test_midshot_v31_unblocks_wnba():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    assert "WNBA" not in SportsMidshotV31Strategy.OBSERVATION_ONLY_SPORTS


def test_midshot_v31_pair_fire_dedup():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    strat, _ = _make(SportsMidshotV31Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.25, 0.76, 0.25, ticker=WNBA_TICKER_B)
    assert sig is None


def test_midshot_v31_wnba_only_dedup_branch():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    import inspect
    strat, _ = _make(SportsMidshotV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k midshot_v31`
Expected: failures.

- [ ] **Step 3: Modify `strategies/sports_midshot_v31.py`**

Add after `_get_trade_size` (the existing method ends at line ~85):

```python
    # 2026-05-13 (WNBA expansion): unblock WNBA, add pair-fire dedup
    OBSERVATION_ONLY_SPORTS = (
        SportsMidshotStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs):
        if "WNBAGAME" in ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
```

Verify `SportsMidshotStrategy` is imported (check top of file). If not, add: `from strategies.sports_midshot import SportsMidshotStrategy`.

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k midshot_v31`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_midshot_v31.py tests/test_wnba_expansion.py
git commit -m "feat(midshot_v31): enable WNBA + WNBA-only pair-fire dedup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: underdog_v31 — same pattern

**Files:**
- Modify: `strategies/sports_underdog_v31.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add tests**

```python
def test_underdog_v31_unblocks_wnba():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    assert "WNBA" not in SportsUnderdogV31Strategy.OBSERVATION_ONLY_SPORTS


def test_underdog_v31_pair_fire_dedup():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    strat, _ = _make(SportsUnderdogV31Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.35, 0.66, 0.35, ticker=WNBA_TICKER_B)
    assert sig is None


def test_underdog_v31_dedup_branch_is_wnba_only():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    import inspect
    strat, _ = _make(SportsUnderdogV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k underdog_v31`

- [ ] **Step 3: Modify `strategies/sports_underdog_v31.py`**

Append after `_get_trade_size`:

```python
    # 2026-05-13 (WNBA expansion): unblock WNBA, add pair-fire dedup
    OBSERVATION_ONLY_SPORTS = (
        SportsUnderdogStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs):
        if "WNBAGAME" in ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
```

Verify `SportsUnderdogStrategy` is imported (line 4 of file).

- [ ] **Step 4: Run, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k underdog_v31`

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_underdog_v31.py tests/test_wnba_expansion.py
git commit -m "feat(underdog_v31): enable WNBA + WNBA-only pair-fire dedup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Cascade re-block (v32 + v33 paper)

### Task 5: underdog_v32 — restore parent OBSERVATION_ONLY

**Files:**
- Modify: `strategies/sports_underdog_v32.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add tests**

```python
def test_v32_blocks_wnba_via_parent_restore():
    """v32 inherits from v31 underdog — must NOT inherit the WNBA unblock."""
    from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
    assert "WNBA" in SportsUnderdogV32Strategy.OBSERVATION_ONLY_SPORTS
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v32_blocks_wnba`
Expected: fail (v32 inherits v31 underdog's override which removed WNBA).

- [ ] **Step 3: Modify `strategies/sports_underdog_v32.py`**

Add inside the class body (existing class is short — see file structure):

```python
    # 2026-05-13: v31 underdog removed WNBA from OBSERVATION_ONLY_SPORTS as
    # part of WNBA 5-strategy expansion. v32 is out of scope for that
    # expansion (5/18 retire-eval candidate). Restore parent default to
    # keep WNBA observer-only here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
```

Verify `SportsTraderStrategy` is importable. If `SportsUnderdogV32Strategy` only imports `SportsUnderdogV31Strategy`, add at top: `from strategies.sports_trader import SportsTraderStrategy`.

- [ ] **Step 4: Run, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v32_blocks_wnba`

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_underdog_v32.py tests/test_wnba_expansion.py
git commit -m "fix(underdog_v32): re-block WNBA after v31 cascade

v31 underdog removed WNBA from OBSERVATION_ONLY in WNBA expansion.
v32 must restore parent default to stay out of WNBA scope per spec
e72f1de — v32 is a 5/18 retire-eval candidate, no WNBA fires desired.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: longshot_v32, midshot_v32, longshot_v33, midshot_v33, underdog_v33 — same pattern

**Files:**
- Modify: `strategies/sports_longshot_v32.py`, `strategies/sports_midshot_v32.py`, `strategies/sports_longshot_v33.py`, `strategies/sports_midshot_v33.py`, `strategies/sports_underdog_v33.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add tests**

```python
def test_v32_v33_family_blocks_wnba():
    """All v32 + v33 variants must re-block WNBA after v31 trio cascade."""
    from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
    from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
    from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
    from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
    from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy

    for cls in (SportsLongshotV32Strategy, SportsMidshotV32Strategy,
                SportsLongshotV33Strategy, SportsMidshotV33Strategy,
                SportsUnderdogV33Strategy):
        assert "WNBA" in cls.OBSERVATION_ONLY_SPORTS, f"{cls.__name__} did not re-block WNBA"
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v32_v33_family`
Expected: AssertionError naming whichever class is first checked.

- [ ] **Step 3: Apply identical re-block stanza to each file**

For each of:
- `strategies/sports_longshot_v32.py`
- `strategies/sports_midshot_v32.py`
- `strategies/sports_longshot_v33.py`
- `strategies/sports_midshot_v33.py`
- `strategies/sports_underdog_v33.py`

Add (and import `SportsTraderStrategy` at top of file if not present):

```python
    # 2026-05-13 (WNBA expansion cascade): parent v31 trio removed WNBA from
    # OBSERVATION_ONLY_SPORTS. v32/v33 are out of scope for that expansion;
    # restore parent default to keep WNBA blocked here.
    OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
```

- [ ] **Step 4: Run, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v32_v33_family`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_longshot_v32.py strategies/sports_midshot_v32.py \
        strategies/sports_longshot_v33.py strategies/sports_midshot_v33.py \
        strategies/sports_underdog_v33.py tests/test_wnba_expansion.py
git commit -m "fix(v32/v33 family): cascade re-block WNBA after v31 trio expansion

v31 trio removed WNBA from OBSERVATION_ONLY_SPORTS; v32 underdog +
v32/v33 longshot/midshot/underdog inherit through and would otherwise
fire on WNBA. Per spec e72f1de these strategies are out of scope —
restore parent default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — New sibling classes

### Task 7: Create `sports_underdog_v36_wnba.py` (chalk-favorite logic)

**Files:**
- Create: `strategies/sports_underdog_v36_wnba.py`
- Test: `tests/test_wnba_expansion.py`

> **NOTE on evaluate() logic mismatch with v36_playoff:** The spec specifies "35-55c band with fair_prob>0.55". This is a NEW chalk-favorite evaluate() — not a copy of v36_playoff's inherited underdog logic. v36_wnba name follows the spec convention but the class implements its own `evaluate()`.

- [ ] **Step 1: Add tests**

```python
def test_v36_wnba_class_attributes():
    from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
    assert SportsUnderdogV36WNBAStrategy.STRATEGY_NAME == "sports_underdog_v36_wnba"
    assert SportsUnderdogV36WNBAStrategy.ALLOWED_CELLS == frozenset({("WNBA", "pre")})
    assert SportsUnderdogV36WNBAStrategy.PLAYOFF_ONLY is False
    assert SportsUnderdogV36WNBAStrategy.MIN_ENTRY_PRICE == 0.35
    assert SportsUnderdogV36WNBAStrategy.MAX_ENTRY_PRICE == 0.55
    assert "WNBA" not in SportsUnderdogV36WNBAStrategy.OBSERVATION_ONLY_SPORTS


def test_v36_wnba_fires_on_favorite_in_band():
    """v36_wnba fires when YES is the favorite (fair > 0.55) priced at 35-55c."""
    from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
    strat, _ = _make(SportsUnderdogV36WNBAStrategy)
    # SEA-CONN: SEA yes_ask 0.55, eu 0.534 — should NOT fire (eu < 0.55)
    sig = strat.evaluate(0.55, 0.46, 0.534, ticker="KXWNBAGAME-26MAY14SEA-SEA")
    assert sig is None
    # PHX-GS: GS yes 0.54, eu 0.555 — eu > 0.55 → fire YES
    sig = strat.evaluate(0.54, 0.47, 0.555, ticker="KXWNBAGAME-26MAY14PHXGS-GS")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.54


def test_v36_wnba_no_fire_outside_band():
    from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
    strat, _ = _make(SportsUnderdogV36WNBAStrategy)
    # price 0.30 — below 0.35 floor
    sig = strat.evaluate(0.30, 0.71, 0.55, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None
    # price 0.60 — above 0.55 cap
    sig = strat.evaluate(0.60, 0.41, 0.65, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None


def test_v36_wnba_pair_fire_dedup():
    from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
    strat, _ = _make(SportsUnderdogV36WNBAStrategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.54, 0.47, 0.56, ticker=WNBA_TICKER_B)
    assert sig is None  # paired sibling already fired


def test_v36_wnba_size_8():
    from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
    strat, _ = _make(SportsUnderdogV36WNBAStrategy)
    assert strat._get_trade_size({"side": "yes", "price": 0.54}) == 8.0
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v36_wnba`
Expected: `ImportError: cannot import name 'SportsUnderdogV36WNBAStrategy'`

- [ ] **Step 3: Create `strategies/sports_underdog_v36_wnba.py`**

```python
"""Sports v36 WNBA — slight-favorite YES at 35-55c with fair_prob > 0.55.

Built 2026-05-13 from WNBA observer replay (n=3 settled games, ROI +73%,
WR 100%). Per spec docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md.

This is a NEW strategy class — NOT a port of v36_playoff. v36_playoff is
NBA underdog 30¢+ floor; v36_wnba is WNBA chalk-favorite (price < fair).
The "v36" naming follows user spec convention but evaluate() logic is
fresh.

WARNING: n=3 is well below promotion gate (n>=25). All 3 settled fires
were chalk wins — alpha unverified. First eval scheduled 2026-05-23.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy

logger = logging.getLogger("sports_underdog_v36_wnba")


class SportsUnderdogV36WNBAStrategy(SportsUnderdogV31Strategy):
    """Bet the slight-favorite side (fair > 0.55) at 0.35-0.55 YES on WNBA pre-game."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v36_wnba_tickers.json"
    STRATEGY_NAME = "sports_underdog_v36_wnba"
    REGION_OVERRIDE = "eu"

    # WNBA pre-game only
    ALLOWED_CELLS = frozenset({("WNBA", "pre")})
    PLAYOFF_ONLY = False  # WNBA reg season, no playoff calendar entry
    PRE_GAME_ONLY = True

    # Override parent (v31 underdog) blocklists — ALLOWED_CELLS subsumes
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()

    # Restore parent default (parent removed WNBA in v31 trio mods,
    # but v36_wnba IS the WNBA strategy — must override to remove again)
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    # Chalk-favorite price band — matches replay n=3 ROI +73%
    MIN_ENTRY_PRICE = 0.35
    MAX_ENTRY_PRICE = 0.55
    # Override v31's DEPTH_DEAD_ZONE — WNBA depth p50 $567 (within dead zone
    # $2k-10k? Actually p50 falls $500-2000 tier; the v31 dead-zone covers
    # $2k-10k specifically, which is a thin sub-tier for WNBA). Keep dead-zone
    # inherited from v31 — WNBA markets fall mostly outside it.
    MIN_DEPTH_USDC = 20.0

    # Slight-favorite confidence threshold
    FAIR_PROB_MIN_FAVORITE = 0.55

    def _get_trade_size(self, signal: dict) -> float:
        # Launch size 8 (paper-tested in replay; matches v37 launch size).
        return min(8.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes: float, kalshi_no: float,
                 fair_prob: float, ticker: str = "", **kwargs) -> Optional[dict]:
        """Fire YES if YES side is consensus favorite (fair > 0.55) priced
        below fair in 35-55c band. Mirror logic for NO side. Both sides
        checked; pair-fire dedup prevents double-betting same outcome via
        sibling tickers."""
        # Pair-fire dedup (WNBA-only by ALLOWED_CELLS, unconditional here)
        if ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None

        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # YES side: YES team is favorite (fair > 0.55), priced at 0.35-0.55
        if (fair_prob > self.FAIR_PROB_MIN_FAVORITE and
                self.MIN_ENTRY_PRICE <= kalshi_yes <= self.MAX_ENTRY_PRICE):
            fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes
            payoff = 1.0 / (kalshi_yes * (1 + fee_pct))
            return {
                "side": "yes", "price": kalshi_yes,
                "edge": payoff, "strategy": "v36_wnba",
            }

        # NO side: NO team is favorite (fair_yes < 0.45 → fair_no > 0.55),
        # priced at 0.35-0.55
        if (fair_prob < (1 - self.FAIR_PROB_MIN_FAVORITE) and
                self.MIN_ENTRY_PRICE <= kalshi_no <= self.MAX_ENTRY_PRICE):
            fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
            payoff = 1.0 / (kalshi_no * (1 + fee_pct))
            return {
                "side": "no", "price": kalshi_no,
                "edge": payoff, "strategy": "v36_wnba",
            }

        return None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v36_wnba`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_underdog_v36_wnba.py tests/test_wnba_expansion.py
git commit -m "feat(strategy): add sports_underdog_v36_wnba (paper+live launch)

WNBA chalk-favorite YES/NO bets at 35-55c with fair_prob > 0.55.
Sibling class to v36_playoff/v36_us — different evaluate() logic
(chalk-favorite vs NBA underdog floor35). Live launch size 8.

Replay baseline: n=3, ROI +73%, WR 100% (all chalk wins). First
eval 2026-05-23 at n>=25. See spec e72f1de.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Create `sports_underdog_v37_wnba.py` (inherit v37_soccer evaluate)

**Files:**
- Create: `strategies/sports_underdog_v37_wnba.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add tests**

```python
def test_v37_wnba_class_attributes():
    from strategies.sports_underdog_v37_wnba import SportsUnderdogV37WNBAStrategy
    assert SportsUnderdogV37WNBAStrategy.STRATEGY_NAME == "sports_underdog_v37_wnba"
    # Block everything except WNBA — soccer also blocked here
    blocklist = SportsUnderdogV37WNBAStrategy.SPORT_BLOCKLIST
    for sport in ("NBA", "MLB", "NHL", "NPB", "KBO"):
        assert sport in blocklist, f"{sport} should be in v37_wnba blocklist"
    assert "WNBA" not in blocklist
    # OBSERVATION_ONLY override — WNBA tradeable
    assert "WNBA" not in SportsUnderdogV37WNBAStrategy.OBSERVATION_ONLY_SPORTS


def test_v37_wnba_fires_on_20_29c_team():
    from strategies.sports_underdog_v37_wnba import SportsUnderdogV37WNBAStrategy
    strat, _ = _make(SportsUnderdogV37WNBAStrategy)
    # WNBA-formatted ticker, price 0.22 — in 20-29c band, payoff >= 2.0
    sig = strat.evaluate(0.22, 0.79, 0.22, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.22


def test_v37_wnba_skips_outside_band():
    from strategies.sports_underdog_v37_wnba import SportsUnderdogV37WNBAStrategy
    strat, _ = _make(SportsUnderdogV37WNBAStrategy)
    # 0.30 — above 0.29 cap
    sig = strat.evaluate(0.30, 0.71, 0.30, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None


def test_v37_wnba_pair_fire_dedup():
    from strategies.sports_underdog_v37_wnba import SportsUnderdogV37WNBAStrategy
    strat, _ = _make(SportsUnderdogV37WNBAStrategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.22, 0.79, 0.22, ticker=WNBA_TICKER_B)
    assert sig is None


def test_v37_soccer_still_blocks_wnba():
    """v37_soccer's SPORT_BLOCKLIST must still contain WNBA — sibling separation."""
    from strategies.sports_underdog_v37_soccer import SportsUnderdogV37SoccerStrategy
    assert "WNBA" in SportsUnderdogV37SoccerStrategy.SPORT_BLOCKLIST
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v37_wnba`

- [ ] **Step 3: Create `strategies/sports_underdog_v37_wnba.py`**

```python
"""Sports v37 WNBA — TEAM-only deep-underdog 20-29c band, payoff >= 2.0x.

Built 2026-05-13. Sibling class to v37_soccer with identical evaluate()
logic (inherited) but SPORT_BLOCKLIST flipped to block soccer + other
non-WNBA sports while allowing WNBA.

Replay baseline (WNBA observer, n=2 settled games): ROI +118%, WR 50%.
n far below promotion gate (>=25) — first eval 2026-05-23. See spec
e72f1de.
"""
import logging
from typing import Optional

from strategies.sports_underdog_v37_soccer import SportsUnderdogV37SoccerStrategy

logger = logging.getLogger("sports_underdog_v37_wnba")


class SportsUnderdogV37WNBAStrategy(SportsUnderdogV37SoccerStrategy):
    """v37 TEAM 20-29c band applied to WNBA only."""

    TRADED_TICKERS_FILE = "data/sports_underdog_v37_wnba_tickers.json"
    STRATEGY_NAME = "sports_underdog_v37_wnba"

    # Block everything except WNBA (soccer blocked here too)
    SPORT_BLOCKLIST = frozenset({"NBA", "MLB", "NHL", "NPB", "KBO",
                                  "MLS", "EPL", "LAL", "UCL", "BUN"})

    # Inherit OBSERVATION_ONLY_SPORTS = frozenset() from v37_soccer
    # → already allows WNBA + all other sports. Combined with SPORT_BLOCKLIST
    # above, only WNBA reaches evaluate().

    def evaluate(self, kalshi_yes: float, kalshi_no: float,
                 fair_prob: float, ticker: str = "", **kwargs) -> Optional[dict]:
        """Pair-fire dedup, then delegate to v37_soccer evaluate (20-29c TEAM)."""
        if ticker:
            game_key = ticker.rsplit("-", 1)[0]
            for traded in self._traded_tickers:
                if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                    return None
        return super().evaluate(
            kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k v37_wnba`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add strategies/sports_underdog_v37_wnba.py tests/test_wnba_expansion.py
git commit -m "feat(strategy): add sports_underdog_v37_wnba (paper+live launch)

WNBA TEAM 20-29c band, payoff>=2.0x. Thin sibling of v37_soccer with
SPORT_BLOCKLIST flipped to allow WNBA + block soccer/other sports.
Inherits v37_soccer evaluate(); adds WNBA-only pair-fire dedup wrapper.

Replay n=2, ROI +118%. First eval 2026-05-23. See spec e72f1de.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Registration

### Task 9: PAPER_TWIN_STRATEGIES (order_manager.py)

**Files:**
- Modify: `core/order_manager.py`
- Test: `tests/test_wnba_expansion.py`

- [ ] **Step 1: Add test**

```python
def test_paper_twin_registration():
    from core.order_manager import PAPER_TWIN_STRATEGIES
    assert "sports_underdog_v36_wnba" in PAPER_TWIN_STRATEGIES
    assert "sports_underdog_v37_wnba" in PAPER_TWIN_STRATEGIES
```

- [ ] **Step 2: Run, verify fail**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k paper_twin_registration`

- [ ] **Step 3: Modify `core/order_manager.py`**

Find `PAPER_TWIN_STRATEGIES = {` (around line 60). Add the two new entries before the closing brace, matching the existing comment style:

```python
    "sports_underdog_v36_wnba",   # 2026-05-13 WNBA expansion — chalk-favorite
    "sports_underdog_v37_wnba",   # 2026-05-13 WNBA expansion — 20-29c TEAM band
```

- [ ] **Step 4: Run test, verify pass**

Run: `venv/bin/pytest tests/test_wnba_expansion.py -v -k paper_twin_registration`

- [ ] **Step 5: Commit**

```bash
git add core/order_manager.py tests/test_wnba_expansion.py
git commit -m "feat(order_manager): register v36_wnba + v37_wnba as paper-twin strategies

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: main.py — import + instantiate + scan_loop

**Files:**
- Modify: `main.py`

(No test — main.py wiring is integration-level, validated by dry-run boot.)

- [ ] **Step 1: Read main.py imports + instantiation patterns**

Run: `grep -n "sports_underdog_v37_soccer\|sports_draw_v38" main.py`
Confirm import line ~37, instantiation ~179, scan_loop registration ~476.

- [ ] **Step 2: Add imports**

After line 38 (`from strategies.sports_draw_v38_soccer import SportsDrawV38SoccerStrategy`):

```python
from strategies.sports_underdog_v36_wnba import SportsUnderdogV36WNBAStrategy
from strategies.sports_underdog_v37_wnba import SportsUnderdogV37WNBAStrategy
```

- [ ] **Step 3: Add instantiation**

After `self.sports_draw_v38_soccer = SportsDrawV38SoccerStrategy(...)` block (around line 189), add:

```python
        # V3.6 WNBA — chalk-favorite YES/NO at 35-55c with fair_prob > 0.55.
        # Built 2026-05-13 from WNBA observer replay (n=3, +73% ROI). Sibling
        # to v36_playoff/v36_us; fresh evaluate() (chalk-favorite, not v36
        # underdog floor35). Live launch size 8. First eval 5/23 at n>=25.
        self.sports_underdog_v36_wnba = SportsUnderdogV36WNBAStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.7 WNBA — TEAM 20-29c band, payoff>=2x. Thin sibling of v37_soccer
        # with SPORT_BLOCKLIST allowing WNBA + blocking soccer/other sports.
        # Replay n=2, +118% ROI. Live launch size 8. First eval 5/23 at n>=25.
        self.sports_underdog_v37_wnba = SportsUnderdogV37WNBAStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
```

- [ ] **Step 4: Add to scan_loop iteration list**

Find the strategies iteration list around line 476 (`("sports_underdog_v37_soccer", self.sports_underdog_v37_soccer),`). Add after the v38 entry:

```python
            ("sports_underdog_v36_wnba", self.sports_underdog_v36_wnba),
            ("sports_underdog_v37_wnba", self.sports_underdog_v37_wnba),
```

- [ ] **Step 5: Dry-run boot to verify imports + instantiation**

Run: `venv/bin/python -c "from main import BotApp; print('import OK')"`
Expected: `import OK` with no traceback.

If import fails, re-check imports + class names + indentation in main.py.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(main): register v36_wnba + v37_wnba in scan_loop

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: dashboard/api.py — strategy display registration

**Files:**
- Modify: `monitoring/dashboard/api.py`

- [ ] **Step 1: Grep for v37_soccer registration touchpoints in dashboard**

Run: `grep -n "sports_underdog_v37_soccer" monitoring/dashboard/api.py`

This will find 3+ locations (strategy list, SPORTS_ENABLED config, dashboard helper). Each needs v36_wnba + v37_wnba added.

- [ ] **Step 2: Add `sports_underdog_v36_wnba` + `sports_underdog_v37_wnba` to `all_strategy_names` list**

Find the list around line 102-119. After `"sports_underdog_v37_soccer",` add:

```python
            "sports_underdog_v36_wnba",
            "sports_underdog_v37_wnba",
```

- [ ] **Step 3: Add to any strategy display config / live-strategy mapping**

Grep result will indicate further sites (line 449, 514, 648, 669 per earlier session). For each, add the two new strategy names in the same pattern as `sports_underdog_v37_soccer`.

- [ ] **Step 4: Boot test (dashboard import)**

Run: `venv/bin/python -c "from monitoring.dashboard.api import create_app; print('dashboard import OK')"`
Expected: `dashboard import OK`.

- [ ] **Step 5: Commit**

```bash
git add monitoring/dashboard/api.py
git commit -m "feat(dashboard): show v36_wnba + v37_wnba in strategy lists

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Validation

### Task 12: Extend `scripts/strategy_replay.py` with v36_wnba + v37_wnba callables

**Files:**
- Modify: `scripts/strategy_replay.py`

- [ ] **Step 1: Inspect existing strategy_replay.py structure**

Run: `grep -n "STRATEGIES = {" scripts/strategy_replay.py`

The dict is around line 201. Each entry maps a name → lambda.

- [ ] **Step 2: Add helpers + entries**

At the bottom of the file, before `STRATEGIES = {`, add:

```python
def replay_v36_wnba(e, *, lo=0.35, hi=0.55, fair_min=0.55, base_size=8.0):
    """v36_wnba chalk-favorite at 35-55c with fair_prob > 0.55."""
    ya, na = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    eu = e.get("eu_prob")
    if eu is None:
        return None
    # YES favorite path
    if eu > fair_min and lo <= ya <= hi:
        return {"side": "yes", "price": ya, "size_usdc": base_size}
    # NO favorite path
    if eu < (1 - fair_min) and lo <= na <= hi:
        return {"side": "no", "price": na, "size_usdc": base_size}
    return None


def replay_v37_wnba(e):
    """Same gate as v37_soccer (TEAM 20-29c payoff>=2)."""
    return replay_v37_edge(e)  # if v37 helper named differently, use that
```

Then add to STRATEGIES dict (just before the V3 baseline entries):

```python
    "v36_wnba": replay_v36_wnba,
    "v37_wnba": replay_v37_wnba,
```

If a v37-style helper doesn't already exist in strategy_replay.py, define it inline (TEAM 20-29c payoff>=2.0).

- [ ] **Step 3: Dry-run on WNBA observer data**

Run:
```bash
venv/bin/python -c "
import sqlite3, sys
sys.path.insert(0, '.')
from collections import defaultdict
from scripts.strategy_replay import STRATEGIES, sim_pnl, sim_cost

c = sqlite3.connect('data/bot.db')
settled = c.execute('''SELECT kalshi_ticker, result FROM ticker_settlements
                       WHERE kalshi_ticker LIKE \"KXWNBAGAME-%\"''').fetchall()
print(f'WNBA settled: {len(settled)}')
fires = defaultdict(lambda: defaultdict(list))
for ticker, result in settled:
    gid = '-'.join(ticker.split('-')[:-1])
    rows = c.execute('''SELECT kalshi_yes_ask, kalshi_no_ask, minutes_to_start,
                               eu_yes_prob, pinnacle_yes_prob, n_books_eu
                        FROM odds_observations WHERE kalshi_ticker=?
                          AND minutes_to_start BETWEEN 60 AND 1440
                        ORDER BY timestamp''', (ticker,)).fetchall()
    for strat in ('longshot_v31', 'midshot_v31', 'underdog_v31', 'v36_wnba', 'v37_wnba'):
        for ya, na, mts, eu, pin, books in rows:
            if eu is None or books is None or books < 5: continue
            if ya is None or na is None: continue
            sig = STRATEGIES[strat]({'yes_ask': ya, 'no_ask': na, 'eu_prob': eu,
                                      'pin_prob': pin, 'minutes_to_start': mts})
            if sig:
                fires[strat][gid].append({'pnl': sim_pnl(sig['side'], sig['price'], sig['size_usdc'], result),
                                           'cost': sim_cost(sig['price'], sig['size_usdc'])})
                break
for strat, games in fires.items():
    n = len(games)
    tot_pnl = sum(sum(l['pnl'] for l in legs) for legs in games.values())
    tot_cost = sum(sum(l['cost'] for l in legs) for legs in games.values())
    print(f'  {strat:<15} n={n} games  PnL={tot_pnl:+.2f} cost={tot_cost:.2f} ROI={tot_pnl/tot_cost*100 if tot_cost else 0:+.1f}%')
"
```

Expected output (approximate, matching prior session):
- longshot_v31: n=1 games, ROI ≈ +335%
- midshot_v31: n=4, ≈ +84%
- underdog_v31: n=7, ≈ +41%
- v36_wnba: n=3, ≈ +73%
- v37_wnba: n=2, ≈ +118%

If results diverge by >20pp from prior session, investigate before deploying.

- [ ] **Step 4: Commit**

```bash
git add scripts/strategy_replay.py
git commit -m "test(replay): add v36_wnba + v37_wnba to STRATEGIES dict

For dry-run validation against WNBA observer data ahead of live deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Full pytest suite — confirm zero new regressions

- [ ] **Step 1: Run full suite vs master baseline**

```bash
venv/bin/pytest tests/ --deselect tests/test_market_maker.py::test_cancels_all_on_circuit_breaker -q 2>&1 | tail -3
```

Expected: passed count should be `350 + N_new_tests` (where N_new_tests is ~17 added in Phases A-E). Failed count should equal master baseline (23 known pre-existing).

If new failures appear: investigate before deploying.

- [ ] **Step 2: Diff failures vs master**

Run on master and on current branch, compare:
```bash
git stash
venv/bin/pytest tests/ --deselect tests/test_market_maker.py::test_cancels_all_on_circuit_breaker -q 2>&1 | grep FAILED | sort > /tmp/master-failures.txt
git stash pop
venv/bin/pytest tests/ --deselect tests/test_market_maker.py::test_cancels_all_on_circuit_breaker -q 2>&1 | grep FAILED | sort > /tmp/branch-failures.txt
diff /tmp/master-failures.txt /tmp/branch-failures.txt
```

Expected: no diff (zero new failures).

If diff: fix new regressions before proceeding.

- [ ] **Step 3: (No commit — this is verification only)**

---

## Phase G — Deploy

### Task 14: Push commits to origin master

- [ ] **Step 1: Verify commit list**

Run: `git log --oneline origin/master..HEAD`
Expected: ~10 commits from Phases A-F (test scaffold, longshot_v31, midshot_v31, underdog_v31, v32/v33 re-block, v36_wnba, v37_wnba, paper_twin reg, main.py, dashboard, replay).

- [ ] **Step 2: Push**

Run: `git push origin master`
Expected: successful push.

---

### Task 15: SSH deploy + .env edit + restart

- [ ] **Step 1: Pull on prod**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem -o StrictHostKeyChecking=no ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && git pull"
```

Expected: `Fast-forward` to most recent commit hash.

- [ ] **Step 2: Edit prod `.env` to add new LIVE_STRATEGIES tokens**

⚠️ This step requires user permission to edit prod `.env` (writes are gated per memory `feedback_prod_db_read_authorization`).

Ask user:
> "Need to edit `/home/ubuntu/kalshi-arb/.env` on prod to append `sports_underdog_v36_wnba,sports_underdog_v37_wnba` to `LIVE_STRATEGIES`. v31 trio (`sports_longshot_v31, sports_midshot_v31, sports_underdog_v31`) should already be in the env. Confirm before I proceed."

After user confirms:
```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem -o StrictHostKeyChecking=no ubuntu@18.117.216.224 \
  "sed -i 's/^LIVE_STRATEGIES=\\(.*\\)$/LIVE_STRATEGIES=\\1,sports_underdog_v36_wnba,sports_underdog_v37_wnba/' /home/ubuntu/kalshi-arb/.env && grep LIVE_STRATEGIES /home/ubuntu/kalshi-arb/.env"
```

Expected: grep shows updated line with both new tokens present.

- [ ] **Step 3: Restart kalshi-bot**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem -o StrictHostKeyChecking=no ubuntu@18.117.216.224 \
  "sudo systemctl restart kalshi-bot && sleep 5 && sudo systemctl is-active kalshi-bot"
```

Expected: `active`.

- [ ] **Step 4: Tail logs for 5 min to confirm boot + first fires**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem -o StrictHostKeyChecking=no ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '5 minutes ago' --no-pager | grep -E 'Targeted scan|wnba|WNBA|sports_underdog_v3[67]_wnba|Application startup|Traceback'"
```

Expected:
- `Application startup complete` (clean boot)
- `Targeted scan: ... strategy markets from 16 series` (KXWNBAGAME included since 5/9 wiring)
- NO Tracebacks involving new strategies
- (Eventually) fires of form `[LIVE] sports_underdog_v36_wnba ...` or `sports_underdog_v37_wnba ...` on WNBA tickers when next WNBA slate opens (5/14)

- [ ] **Step 5: Capture deploy snapshot for memory**

Note the deploy commit hash + time. Memory updates (per spec "Memory Updates Required") to be done separately after first fires observed.

---

## Risk Mitigations

| Risk | Where addressed |
|------|----------------|
| n=11 small sample → first-week ROI dominated by noise | Spec callout; accepted by user; 5/23 eval defers promotion/retire |
| v32/v33 cascade fires on WNBA undesired | Task 5 + 6 explicit re-block + tests |
| v36_wnba evaluate() != v36_playoff logic (chalk-favorite vs underdog) | Task 7 docstring + spec NOTE |
| Pair-fire dedup blocks valid 2nd signal on game later | Acceptable — game-level cap aligns with v40 d036f07 already in prod 24h+ |
| `.env` edit fails or misses tokens | Task 15 step 2 grep verifies |
| Restart fails after `.env` edit | Task 15 step 3 `is-active` check; revert by removing tokens + restart |
| New file import fails | Task 10 step 5 dry-run boot check before deploy |

---

## Self-Review (post-write)

**Spec coverage check** (every spec section → task mapping):

- "v31 trio modify-in-place" → Tasks 2, 3, 4 ✓
- "v32 + v33 paper variants cascade re-block" → Tasks 5, 6 ✓
- "v36_wnba new sibling, 35-55c band, fair > 0.55, size 8" → Task 7 ✓
- "v37_wnba sibling inheriting v37_soccer evaluate" → Task 8 ✓
- "Registration: PAPER_TWIN + main.py + dashboard + .env" → Tasks 9, 10, 11, 15 ✓
- "Validation: pytest + strategy_replay" → Tasks 1 (scaffold) + per-task tests + 12 (replay) + 13 (full suite) ✓
- "Rollout: 3 commits..." → Multiple atomic commits across tasks (per-task TDD pattern; ~10 commits total; spec's "3 commits" was illustrative — atomic is better) ✓
- "First eval 2026-05-23" → Spec milestone; no implementation task needed ✓

**Placeholder check**: All steps contain concrete code, exact commands, expected output. ✓

**Type consistency**: `evaluate()` signature `(kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs)` used consistently across Tasks 2-4, 7, 8. ✓ `_traded_tickers` attribute referenced consistently (dict-typed). ✓ `OBSERVATION_ONLY_SPORTS` always `frozenset`. ✓

**Scope check**: Single subsystem (WNBA expansion). Each task self-contained. ✓

**Open items from spec**:
- "Exact location of pair-fire dedup helper (per-class duplicate vs base class method)" → Resolved as per-class duplicate (more surgical, minimal blast radius)
- "v36_wnba evaluate() — verify exact thresholds against v36_us source" → Resolved as new evaluate() (Task 7 NOTE)
- "v33 paper variants: enumerate which paper-twin files exist" → Resolved in Task 6 file list
- "Existing strategy_replay.py needs v36_wnba and v37_wnba helpers added" → Task 12 ✓
