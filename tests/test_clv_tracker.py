"""Pre-close CLV snapshot infrastructure."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from core.clv_tracker import CLVTracker, parse_game_start_utc
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
