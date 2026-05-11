import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_and_fetch_nearest_crypto_iv():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_iv("BTC", "2026-05-13", 0.65)
        await db.record_crypto_iv("BTC", "2026-05-20", 0.62)
        await db.record_crypto_iv("ETH", "2026-05-13", 0.75)

        row = await db.fetch_nearest_crypto_iv("BTC")
        assert row["asset"] == "BTC"
        assert row["expiry_date"] == "2026-05-13"
        assert row["atm_iv"] == 0.65

        eth = await db.fetch_nearest_crypto_iv("ETH")
        assert eth["expiry_date"] == "2026-05-13"
        assert eth["atm_iv"] == 0.75


@pytest.mark.asyncio
async def test_record_crypto_iv_upserts():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_iv("BTC", "2026-05-13", 0.65)
        await db.record_crypto_iv("BTC", "2026-05-13", 0.70)
        row = await db.fetch_nearest_crypto_iv("BTC")
        assert row["atm_iv"] == 0.70


@pytest.mark.asyncio
async def test_fetch_nearest_returns_none_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        assert await db.fetch_nearest_crypto_iv("BTC") is None
