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


@pytest.mark.asyncio
async def test_record_crypto_fair_value_signal_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_fair_value_signal(
            ticker="KXBTC-26MAY1017-T89749.99", asset="BTC",
            spot=90000.0, strike=89749.99,
            vol=0.65, vol_source="deribit",
            time_to_close_secs=3600,
            fair_prob=0.55, kalshi_implied=0.40, deviation=0.15,
            side="yes", expected_size_usdc=30.0, action_taken="fired",
        )
        rows = await db.fetch_recent_crypto_fair_value_signals(limit=5)
        assert len(rows) == 1
        r = rows[0]
        assert r["asset"] == "BTC"
        assert r["vol_source"] == "deribit"
        assert r["deviation"] == 0.15
        assert r["action_taken"] == "fired"


@pytest.mark.asyncio
async def test_record_crypto_fair_value_signal_with_skip_action():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_crypto_fair_value_signal(
            ticker="KXBTC-26MAY1017-T89749.99", asset="BTC",
            spot=90000.0, strike=89749.99,
            vol=0.65, vol_source="deribit",
            time_to_close_secs=3600,
            fair_prob=0.55, kalshi_implied=0.50, deviation=0.05,
            side=None, expected_size_usdc=None, action_taken="skip_min_deviation",
        )
        rows = await db.fetch_recent_crypto_fair_value_signals(limit=5)
        assert rows[0]["action_taken"] == "skip_min_deviation"
        assert rows[0]["side"] is None
