import pytest
import pytest_asyncio
import aiosqlite
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_tables(db):
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert "trades" in tables
    assert "orders" in tables


@pytest.mark.asyncio
async def test_record_trade(db):
    await db.record_trade(
        strategy="sum_arb", market_id="market_123", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled",
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["strategy"] == "sum_arb"
    assert trades[0]["price"] == 0.48


@pytest.mark.asyncio
async def test_record_order(db):
    await db.record_order(
        order_id="order_abc", market_id="market_123", strategy="maker_preorder",
        side="BUY", price=0.47, size=50.0, order_type="limit", status="open",
    )
    orders = await db.get_open_orders()
    assert len(orders) == 1
    assert orders[0]["order_id"] == "order_abc"


@pytest.mark.asyncio
async def test_update_order_status(db):
    await db.record_order(
        order_id="order_abc", market_id="market_123", strategy="maker_preorder",
        side="BUY", price=0.47, size=50.0, order_type="limit", status="open",
    )
    await db.update_order_status("order_abc", "filled")
    orders = await db.get_open_orders()
    assert len(orders) == 0


@pytest.mark.asyncio
async def test_record_trade_returns_trade_id(db):
    """record_trade must return the new row id so callers can attach CLV snapshots."""
    trade_id_1 = await db.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled",
    )
    trade_id_2 = await db.record_trade(
        strategy="sum_arb", market_id="m2", direction="NO",
        price=0.52, size=50.0, fee=0.75, status="filled",
    )
    assert trade_id_1 == 1
    assert trade_id_2 == 2


@pytest.mark.asyncio
async def test_get_daily_pnl(db):
    await db.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled", pnl=2.0,
    )
    await db.record_trade(
        strategy="sum_arb", market_id="m2", direction="NO",
        price=0.49, size=50.0, fee=0.75, status="filled", pnl=-1.0,
    )
    daily_pnl = await db.get_daily_pnl()
    assert daily_pnl == 1.0
