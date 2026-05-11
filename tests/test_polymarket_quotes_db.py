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
