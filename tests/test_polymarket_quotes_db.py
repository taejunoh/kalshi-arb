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
