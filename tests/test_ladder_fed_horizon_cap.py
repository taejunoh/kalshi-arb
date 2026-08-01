"""ladder_fed near-dated horizon filter + open-exposure cap.

Both gates exist because ladder_fed booked 63 entries between 2026-05-11 and
2026-07-23 and settled ZERO of them: it kept entering FOMC meetings up to 18
months out (26JUL … 28JAN), so no leg could ever return a result while $750 of
paper capital stayed locked. The horizon filter keeps it on meetings that
actually resolve; the cap bounds how much it can tie up in the meantime.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.database import Database
from strategies.ladder_fed import LadderFedStrategy


# Anchored to the real clock, not a fixed date: the gate-wiring tests drive the
# REAL scan_and_execute, which calls datetime.now() internally, so a hardcoded
# NOW rots as real time passes it (this exact test broke 4 days after it was
# written - the fixture 26JUL meeting closed and fell out of the horizon).
# The pure-function tests pass NOW explicitly, so a moving anchor is safe for
# them too: only the offsets matter. See feedback_test_date_bomb_rolling_window.
NOW = datetime.now(timezone.utc)


def _cfg(horizon=2, cap=100.0):
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
        LADDER_FED_HORIZON_MEETINGS=horizon,
        LADDER_FED_MAX_OPEN_EXPOSURE_USDC=cap,
    )


def _mkt(event: str, suffix: str, close: datetime) -> dict:
    return {
        "ticker": f"KXFEDDECISION-{event}-{suffix}",
        "close_time": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _universe() -> list[dict]:
    """Four meetings, deliberately out of chronological order in the list."""
    return [
        _mkt("28JAN", "C25", NOW + timedelta(days=547)),
        _mkt("28JAN", "H0", NOW + timedelta(days=547)),
        _mkt("26SEP", "C25", NOW + timedelta(days=50)),
        _mkt("26SEP", "H0", NOW + timedelta(days=50)),
        _mkt("26JUL", "C25", NOW + timedelta(days=1)),
        _mkt("26JUL", "H0", NOW + timedelta(days=1)),
        _mkt("27DEC", "H25", NOW + timedelta(days=500)),
    ]


# ── horizon filter ────────────────────────────────────────────────────

def test_keeps_only_the_nearest_n_meetings():
    s = LadderFedStrategy(_cfg(horizon=2), MagicMock(), MagicMock(), MagicMock())
    kept = {m["ticker"] for m in s._near_dated_markets(_universe(), NOW)}
    assert kept == {
        "KXFEDDECISION-26JUL-C25", "KXFEDDECISION-26JUL-H0",
        "KXFEDDECISION-26SEP-C25", "KXFEDDECISION-26SEP-H0",
    }


def test_horizon_one_keeps_only_the_front_meeting():
    s = LadderFedStrategy(_cfg(horizon=1), MagicMock(), MagicMock(), MagicMock())
    kept = {m["ticker"].split("-")[1] for m in s._near_dated_markets(_universe(), NOW)}
    assert kept == {"26JUL"}


def test_meetings_already_closed_are_dropped_and_do_not_consume_a_slot():
    """A meeting whose close_time has passed must not eat one of the N slots —
    otherwise the front slot silently rots and the strategy goes quiet."""
    s = LadderFedStrategy(_cfg(horizon=2), MagicMock(), MagicMock(), MagicMock())
    universe = _universe() + [_mkt("26JUN", "C25", NOW - timedelta(days=30))]
    kept = {m["ticker"].split("-")[1] for m in s._near_dated_markets(universe, NOW)}
    assert kept == {"26JUL", "26SEP"}


def test_leg_with_unparseable_close_time_is_dropped():
    s = LadderFedStrategy(_cfg(horizon=2), MagicMock(), MagicMock(), MagicMock())
    universe = _universe() + [{"ticker": "KXFEDDECISION-26AUG-C25", "close_time": "nonsense"}]
    kept = {m["ticker"] for m in s._near_dated_markets(universe, NOW)}
    assert "KXFEDDECISION-26AUG-C25" not in kept
    assert len(kept) == 4


def test_horizon_wider_than_the_calendar_keeps_everything_open():
    s = LadderFedStrategy(_cfg(horizon=99), MagicMock(), MagicMock(), MagicMock())
    assert len(s._near_dated_markets(_universe(), NOW)) == len(_universe())


# ── exposure cap ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exposure_counts_only_in_horizon_tickers():
    """Legacy long-dated positions must NOT count toward the cap — they cannot
    settle for years, so counting them would freeze the strategy permanently
    rather than bounding it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        # in-horizon
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUL-C25",
            direction="yes", price=0.30, size=20.0, fee=0.07, status="paper_filled",
        )
        # out-of-horizon legacy
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-28JAN-H0",
            direction="yes", price=0.30, size=500.0, fee=0.07, status="paper_filled",
        )
        scoped = await db.strategy_open_exposure(
            "ladder_fed", ["KXFEDDECISION-26JUL-C25", "KXFEDDECISION-26SEP-H0"],
        )
        assert scoped == 20.0
        assert await db.strategy_open_exposure("ladder_fed") == 520.0


@pytest.mark.asyncio
async def test_exposure_excludes_settled_and_other_strategies():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUL-C25",
            direction="yes", price=0.30, size=20.0, fee=0.07, status="paper_settled",
        )
        await db.record_trade(
            strategy="macro_event", market_id="KXFEDDECISION-26JUL-C25",
            direction="yes", price=0.30, size=33.0, fee=0.07, status="paper_filled",
        )
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUL-H0",
            direction="yes", price=0.30, size=11.0, fee=0.07, status="paper_filled",
        )
        assert await db.strategy_open_exposure("ladder_fed") == 11.0


@pytest.mark.asyncio
async def test_empty_ticker_scope_means_zero_not_everything():
    """`[]` must mean 'nothing in scope', never degrade to an unscoped SUM."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUL-C25",
            direction="yes", price=0.30, size=20.0, fee=0.07, status="paper_filled",
        )
        assert await db.strategy_open_exposure("ladder_fed", []) == 0.0


# ── gate wiring in scan_and_execute ───────────────────────────────────

def _strategy_with_db(cfg, exposure: float):
    om = MagicMock()
    om.is_circuit_breaker_active.return_value = False
    om.market_order = AsyncMock()
    om._db = MagicMock()
    om._db.strategy_open_exposure = AsyncMock(return_value=exposure)
    scanner = MagicMock()
    scanner.get_all_markets.return_value = _universe()
    client = MagicMock()
    return LadderFedStrategy(cfg, om, scanner, client), om


@pytest.mark.asyncio
async def test_scan_stops_before_enrichment_when_cap_is_reached():
    """The cap must short-circuit BEFORE _enrich_markets_sync — enrichment is
    2 Kalshi API calls per leg and was once the event-loop starvation source."""
    s, om = _strategy_with_db(_cfg(cap=100.0), exposure=100.0)
    s._enrich_markets_sync = MagicMock(side_effect=AssertionError("enriched despite cap"))
    await s.scan_and_execute()
    om.market_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_headroom_caps_the_per_group_position(monkeypatch):
    """With $92 of $100 used, the remaining $8 — not LADDER_MAX_POSITION_PER_GROUP
    ($50) — must bound what a group may commit."""
    s, om = _strategy_with_db(_cfg(cap=100.0), exposure=92.0)
    s._enrich_markets_sync = MagicMock(side_effect=lambda markets, client: markets)

    seen = {}

    def _fake_arb(group, **kw):
        seen["max_position_usdc"] = kw["max_position_usdc"]
        return []

    monkeypatch.setattr("strategies.ladder_fed.find_arb_opportunities", _fake_arb)
    monkeypatch.setattr("strategies.ladder_fed.find_fade_opportunities", lambda *a, **k: [])
    await s.scan_and_execute()
    assert seen["max_position_usdc"] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_only_in_horizon_tickers_are_scoped_into_the_exposure_query():
    s, om = _strategy_with_db(_cfg(horizon=2, cap=100.0), exposure=0.0)
    s._enrich_markets_sync = MagicMock(return_value=[])
    await s.scan_and_execute()
    _, scoped = om._db.strategy_open_exposure.await_args.args
    assert set(scoped) == {
        "KXFEDDECISION-26JUL-C25", "KXFEDDECISION-26JUL-H0",
        "KXFEDDECISION-26SEP-C25", "KXFEDDECISION-26SEP-H0",
    }


@pytest.mark.asyncio
async def test_missing_db_does_not_disable_the_strategy():
    """No _db (unit harnesses) → fall back to the configured cap as headroom
    rather than silently trading unbounded or going dark."""
    s, om = _strategy_with_db(_cfg(cap=100.0), exposure=0.0)
    om._db = None
    s._enrich_markets_sync = MagicMock(return_value=[])
    await s.scan_and_execute()  # must not raise
