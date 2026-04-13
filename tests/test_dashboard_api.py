import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from monitoring.dashboard.api import create_app
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    # Seed some test data
    await database.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled", pnl=4.0,
    )
    await database.record_trade(
        strategy="maker_preorder", market_id="m2", direction="BUY",
        price=0.47, size=50.0, fee=0.0, status="filled", pnl=2.0,
    )
    await database.record_order(
        order_id="order_1", market_id="m3", strategy="maker_preorder",
        side="BUY", price=0.48, size=25.0, order_type="limit", status="open",
    )
    yield database
    await database.close()


@pytest_asyncio.fixture
async def client(db):
    app = create_app(db, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bankroll"] == 1000.0
    assert "daily_pnl" in data
    assert "trade_count" in data


@pytest.mark.asyncio
async def test_get_trades(client):
    resp = await client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_get_trades_filter_by_strategy(client):
    resp = await client.get("/api/trades?strategy=sum_arb")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["strategy"] == "sum_arb"


@pytest.mark.asyncio
async def test_get_open_orders(client):
    resp = await client.get("/api/orders")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["order_id"] == "order_1"


@pytest.mark.asyncio
async def test_get_performance(client):
    resp = await client.get("/api/performance")
    assert resp.status_code == 200
    data = resp.json()
    assert "sum_arb" in data
    assert "maker_preorder" in data
    assert data["sum_arb"]["live"]["trade_count"] == 1
    assert data["sum_arb"]["live"]["total_pnl"] == 4.0


@pytest.mark.asyncio
async def test_strategy_status_marks_loop_disabled(db):
    """Strategies removed from main.py _strategy_loop should report active=false
    with reason='removed_from_loop'. Currently: sports_trader, maker_preorder."""
    class MockConfig:
        LIVE_STRATEGIES = set()
        MM_ENABLED = True
        SHUTDOWN_FADE_ENABLED = True
        SPREAD_SCANNER_ENABLED = True
        MENTIONS_ENABLED = True
        ECON_ENABLED = True
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sports_trader"]["active"] is False
    assert data["sports_trader"]["reason"] == "removed_from_loop"
    assert data["maker_preorder"]["active"] is False
    assert data["maker_preorder"]["reason"] == "removed_from_loop"
    # sports subclasses remain active (in loop, flag on)
    assert data["sports_longshot"]["active"] is True
    assert data["sports_longshot"]["reason"] is None


@pytest.mark.asyncio
async def test_strategy_status_marks_env_disabled(db):
    """Strategies with ENABLED flag=false should report active=false with
    reason like 'MM_ENABLED=false'."""
    class MockConfig:
        LIVE_STRATEGIES = set()
        MM_ENABLED = False
        SHUTDOWN_FADE_ENABLED = False
        SPREAD_SCANNER_ENABLED = True
        MENTIONS_ENABLED = True
        ECON_ENABLED = True
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    data = resp.json()
    assert data["market_maker"]["active"] is False
    assert data["market_maker"]["reason"] == "MM_ENABLED=false"
    assert data["shutdown_fade"]["active"] is False
    assert data["shutdown_fade"]["reason"] == "SHUTDOWN_FADE_ENABLED=false"
    assert data["spread_scanner"]["active"] is True
    assert data["mentions_trader"]["mode"] == "PAPER"


@pytest.mark.asyncio
async def test_strategy_status_mode_live_for_live_strategies(db):
    """mentions_trader in LIVE_STRATEGIES should show mode=LIVE."""
    class MockConfig:
        LIVE_STRATEGIES = {"mentions_trader"}
        MM_ENABLED = False
        SHUTDOWN_FADE_ENABLED = False
        SPREAD_SCANNER_ENABLED = False
        MENTIONS_ENABLED = True
        ECON_ENABLED = False
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    data = resp.json()
    assert data["mentions_trader"]["mode"] == "LIVE"
    assert data["mentions_trader"]["active"] is True
    assert data["sports_midshot"]["mode"] == "PAPER"
