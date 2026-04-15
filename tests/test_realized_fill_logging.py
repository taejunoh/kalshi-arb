"""Live market orders log realized fill price; paper stores NULL."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from core.order_manager import OrderManager
from core.database import Database


@pytest.mark.asyncio
async def test_trades_schema_has_realized_fill_price_column(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(trades)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "realized_fill_price" in cols


@pytest.mark.asyncio
async def test_record_trade_accepts_realized_fill_price(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    trade_id = await db.record_trade(
        "sports_longshot_v31", "KX-T1", "yes", 0.30, 3.0, 0.07,
        "filled", realized_fill_price=0.32,
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
    assert row["realized_fill_price"] == 0.32


@pytest.mark.asyncio
async def test_paper_order_stores_null_realized(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    om = OrderManager(config, kalshi_client=MagicMock(), database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._paper_market_order(
        "KX-T1", "yes", 3.0, 0.30, "sports_longshot", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    assert row["realized_fill_price"] is None


@pytest.mark.asyncio
async def test_live_order_extracts_realized_from_kalshi_response(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    # Kalshi market-order response with realized fill info
    client.place_market_order = MagicMock(return_value={
        "id": "ord123",
        "status": "filled",
        "taker_fill_cost": 96,   # cents paid total for this side
        "taker_fill_count": 3,   # contracts filled
    })
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._live_market_order(
        "KX-T1", "yes", 1.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    # 96 cents / 3 contracts / 100 = $0.32 per contract
    assert row["realized_fill_price"] == pytest.approx(0.32, abs=1e-6)


@pytest.mark.asyncio
async def test_live_order_fallback_when_fill_fields_missing(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    # Response without realized fields
    client.place_market_order = MagicMock(return_value={"id": "ord123", "status": "filled"})
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._live_market_order(
        "KX-T1", "yes", 1.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    # No fill info → NULL
    assert row["realized_fill_price"] is None
