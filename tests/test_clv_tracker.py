"""Pre-close CLV snapshot infrastructure."""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from core.clv_tracker import (CLVTracker, parse_game_start_utc,
                              resolve_event_start_utc)
from core.database import Database


# ── Parser tests ──────────────────────────────────────────────────────────

def test_parse_nba_ticker():
    # KXNBAGAME-26APR18MINDEN-MIN: Apr 18 2026, but no time? Let me look closer…
    # Actual Kalshi format has HHMM: KXMLBGAME-26APR142210NYMLAD-NYM → 22:10 ET Apr 14
    dt = parse_game_start_utc("KXMLBGAME-26APR142210NYMLAD-NYM")
    assert dt is not None
    # 22:10 EDT (Apr 14, 2026 — DST active) → 02:10 UTC Apr 15
    assert dt.year == 2026
    assert dt.month == 4
    assert dt.day == 15
    assert dt.hour == 2
    assert dt.minute == 10


def test_parse_nhl_ticker():
    dt = parse_game_start_utc("KXNHLGAME-26APR141900BOSNYR-BOS")
    assert dt is not None
    # 19:00 EDT → 23:00 UTC same day
    assert dt.day == 14
    assert dt.hour == 23


def test_parse_winter_ticker_uses_est():
    """Before DST kicks in (before 2nd Sunday of March), use EST (UTC-5)."""
    # Jan 15 2026, 20:00 EST → 01:00 UTC Jan 16
    dt = parse_game_start_utc("KXNBAGAME-26JAN152000LALBOS-LAL")
    assert dt is not None
    assert dt.month == 1
    assert dt.day == 16
    assert dt.hour == 1


def test_parse_unknown_ticker_returns_none():
    assert parse_game_start_utc("KXBTC-25MAR20-T70000") is None
    assert parse_game_start_utc("not a ticker") is None
    assert parse_game_start_utc("KXMLBGAME-BROKEN") is None


# ── Tracker tests ────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database


@pytest.mark.asyncio
async def test_captures_pre_close_when_in_window(db, monkeypatch):
    """Trade whose game starts in 3 minutes → snapshot captured."""
    await db.record_trade(strategy="sports_pinnacle_edge",
                           market_id="KXMLBGAME-26APR142210NYMLAD-NYM",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="paper_filled")
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.40, no_ask=0.62)

    client = MagicMock()
    client.get_market.return_value = {
        "yes_ask_dollars": "0.55", "no_ask_dollars": "0.47",
    }
    tracker = CLVTracker(client, db)

    # Freeze time to T-3min before game start (game = 02:10 UTC Apr 15)
    fake_now = datetime(2026, 4, 15, 2, 7, tzinfo=timezone.utc)
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)

    await tracker.capture_pre_close_snapshots()

    # pre_close row should exist
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT yes_ask, no_ask FROM clv_snapshots WHERE snapshot_at='pre_close'"
        )
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.55
    assert rows[0][1] == 0.47


@pytest.mark.asyncio
async def test_skips_trades_outside_window(db, monkeypatch):
    """Game starts in 2 hours — not close enough yet."""
    await db.record_trade(strategy="sports_pinnacle_edge",
                           market_id="KXMLBGAME-26APR142210NYMLAD-NYM",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="paper_filled")
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.40, no_ask=0.62)

    client = MagicMock()
    tracker = CLVTracker(client, db)

    fake_now = datetime(2026, 4, 15, 0, 10, tzinfo=timezone.utc)  # 2h before game
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)

    await tracker.capture_pre_close_snapshots()
    client.get_market.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_duplicate_pre_close(db, monkeypatch):
    """If a pre_close snapshot already exists, don't re-capture."""
    await db.record_trade(strategy="sports_pinnacle_edge",
                           market_id="KXMLBGAME-26APR142210NYMLAD-NYM",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="paper_filled")
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="pre_close",
                                  yes_ask=0.55, no_ask=0.47)

    client = MagicMock()
    tracker = CLVTracker(client, db)
    fake_now = datetime(2026, 4, 15, 2, 7, tzinfo=timezone.utc)
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)

    await tracker.capture_pre_close_snapshots()
    client.get_market.assert_not_called()


@pytest.mark.asyncio
async def test_captures_pre_close_for_live_trade(db, monkeypatch):
    """Live sports trades (status='filled', not 'paper_filled') must also
    get pre_close snapshots. Otherwise CLV+ silently regresses to win rate
    the moment a sports strategy goes live."""
    await db.record_trade(strategy="sports_pinnacle_edge",
                           market_id="KXMLBGAME-26APR142210NYMLAD-NYM",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="filled")  # <- live, not paper
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.40, no_ask=0.62)
    client = MagicMock()
    client.get_market.return_value = {"yes_ask_dollars": "0.55", "no_ask_dollars": "0.47"}
    tracker = CLVTracker(client, db)
    fake_now = datetime(2026, 4, 15, 2, 7, tzinfo=timezone.utc)
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)
    await tracker.capture_pre_close_snapshots()

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM clv_snapshots WHERE snapshot_at='pre_close'"
        )
        count = (await cursor.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_captures_pre_close_with_wider_behind_window(db, monkeypatch):
    """Widen BEHIND window to 3min — allows tick lag without missing games.
    Game started 2 minutes ago; still in window."""
    await db.record_trade(strategy="sports_pinnacle_edge",
                           market_id="KXMLBGAME-26APR142210NYMLAD-NYM",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="paper_filled")
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.40, no_ask=0.62)
    client = MagicMock()
    client.get_market.return_value = {"yes_ask_dollars": "0.55", "no_ask_dollars": "0.47"}
    tracker = CLVTracker(client, db)
    fake_now = datetime(2026, 4, 15, 2, 12, tzinfo=timezone.utc)  # T+2min
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)
    await tracker.capture_pre_close_snapshots()

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM clv_snapshots WHERE snapshot_at='pre_close'"
        )
        count = (await cursor.fetchone())[0]
    assert count == 1  # would have been 0 with BEHIND=1.0


@pytest.mark.asyncio
async def test_skips_non_sports_tickers(db, monkeypatch):
    """Only sports tickers get pre_close treatment."""
    await db.record_trade(strategy="mentions_trader", market_id="KXFEDMENTION-26APR",
                           direction="yes", price=0.40, size=3.0, fee=0.07,
                           status="paper_filled")
    client = MagicMock()
    tracker = CLVTracker(client, db)
    fake_now = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)
    await tracker.capture_pre_close_snapshots()
    client.get_market.assert_not_called()


# ── CLV rate prefers pre_close over closing ──────────────────────────────

@pytest.mark.asyncio
async def test_clv_rate_prefers_pre_close_over_closing(db):
    """A trade with both pre_close and closing snapshots: pre_close used.
    Tests that a trade whose pre_close shows UNFAVORABLE movement is marked
    CLV-, even if settlement snapshot (closing = 1.0 win) would mark it CLV+."""
    # YES trade at 0.60. pre_close = 0.50 (moved DOWN → CLV-).
    # closing = 1.0 (won), but we should prefer pre_close signal (CLV-).
    await db.record_trade(strategy="sports_pinnacle_edge", market_id="m1",
                           direction="yes", price=0.60, size=3.0, fee=0.07,
                           status="paper_settled", pnl=2.0)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                  yes_ask=0.60, no_ask=0.42)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="pre_close",
                                  yes_ask=0.50, no_ask=0.52)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="closing",
                                  yes_ask=1.00, no_ask=0.00)
    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    # pre_close yes_ask 0.50 < entry 0.60 → CLV- → rate 0.0
    # If we used 'closing' instead: 1.00 > 0.60 → CLV+ → rate 1.0
    assert rate == 0.0


# ── Date-only tickers: tennis / WNBA / UFC ────────────────────────────────
#
# These carry no start TIME, so the ticker path cannot work by construction.
# Before the minutes_to_start fallback they were skipped entirely and their
# CLV silently fell back to the degenerate `closing` snapshot.

def test_date_only_tickers_have_no_parseable_time():
    for tk in ("KXATPMATCH-26AUG19BORNAK-NAK",
               "KXWTAMATCH-26AUG19SHNRYB-RYB",
               "KXWNBAGAME-26AUG20ATLLA-LA",
               "KXUFCFIGHT-26AUG22DYEREE-DYE"):
        assert parse_game_start_utc(tk) is None, tk


def test_time_bearing_tickers_are_not_limited_to_nba_mlb_nhl():
    """NPB encodes HHMM and was excluded by the old allow-list regex."""
    dt = parse_game_start_utc("KXNPBGAME-26AUG190500YAKHAN-YAK")
    assert dt is not None
    assert (dt.month, dt.day, dt.hour) == (8, 19, 9)  # 05:00 EDT -> 09:00 UTC


def test_resolver_prefers_ticker_over_estimate():
    start, exact = resolve_event_start_utc(
        "KXMLBGAME-26APR142210NYMLAD-NYM",
        "2026-04-14T00:00:00", 9999.0)
    assert exact is True
    assert start == datetime(2026, 4, 15, 2, 10, tzinfo=timezone.utc)


def test_resolver_falls_back_to_minutes_to_start():
    start, exact = resolve_event_start_utc(
        "KXATPMATCH-26AUG19BORNAK-NAK", "2026-08-19T12:00:00", 90.0)
    assert exact is False
    assert start == datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)


def test_resolver_handles_space_separated_timestamp():
    """trades.timestamp appears with both 'T' and ' ' separators."""
    start, exact = resolve_event_start_utc(
        "KXWTAMATCH-26AUG19SHNRYB-RYB", "2026-08-19 12:00:00", 30.0)
    assert exact is False
    assert start == datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)


def test_resolver_returns_none_without_either_source():
    assert resolve_event_start_utc("KXATPMATCH-26AUG19BORNAK-NAK",
                                   "2026-08-19T12:00:00", None) == (None, False)
    assert resolve_event_start_utc("KXBTC-25MAR20-T70000",
                                   "not-a-timestamp", 30.0) == (None, False)


@pytest.mark.asyncio
async def test_captures_pre_close_for_tennis_via_estimate(db, monkeypatch):
    """The regression this fix exists for: a tennis trade must get a
    pre_close snapshot even though its ticker has no start time."""
    await db.record_trade(strategy="sports_tennis_underdog_v1",
                          market_id="KXATPMATCH-26AUG19BORNAK-NAK",
                          direction="yes", price=0.27, size=1.5, fee=0.02,
                          status="filled", minutes_to_start=120.0)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                 yes_ask=0.27, no_ask=0.75)

    client = MagicMock()
    client.get_market.return_value = {
        "yes_ask_dollars": "0.31", "no_ask_dollars": "0.71",
    }
    tracker = CLVTracker(client, db)

    # Entry recorded "now"; start = now + 120min. Freeze at T-4min.
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("SELECT timestamp FROM trades WHERE id=1")
        entered = (await cur.fetchone())[0]
    from core.clv_tracker import _parse_trade_ts
    fake_now = _parse_trade_ts(entered) + timedelta(minutes=116)
    monkeypatch.setattr("core.clv_tracker._now_utc", lambda: fake_now)

    assert await tracker.capture_pre_close_snapshots() == 1
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT yes_ask FROM clv_snapshots WHERE snapshot_at='pre_close'")
        rows = await cur.fetchall()
    assert [r[0] for r in rows] == [0.31]


@pytest.mark.asyncio
async def test_estimated_window_is_wider_than_exact(db, monkeypatch):
    """T-7min is outside the exact window (5) but inside the estimated one (8),
    so a drifting tennis start still gets captured."""
    await db.record_trade(strategy="sports_tennis_live_fade_v1",
                          market_id="KXWTAMATCH-26AUG19SHNRYB-RYB",
                          direction="no", price=0.18, size=2.9, fee=0.02,
                          status="filled", minutes_to_start=60.0)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry",
                                 yes_ask=0.82, no_ask=0.18)
    client = MagicMock()
    client.get_market.return_value = {"yes_ask_dollars": "0.86",
                                      "no_ask_dollars": "0.15"}
    tracker = CLVTracker(client, db)
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("SELECT timestamp FROM trades WHERE id=1")
        entered = (await cur.fetchone())[0]
    from core.clv_tracker import _parse_trade_ts
    assert tracker.PRE_CLOSE_WINDOW_MIN_AHEAD < 7 < tracker.EST_WINDOW_MIN_AHEAD
    monkeypatch.setattr("core.clv_tracker._now_utc",
                        lambda: _parse_trade_ts(entered) + timedelta(minutes=53))
    assert await tracker.capture_pre_close_snapshots() == 1
