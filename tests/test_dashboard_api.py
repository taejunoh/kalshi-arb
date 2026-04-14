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
async def test_get_performance_paper_bucket_from_db(db):
    """Paper bucket is built from DB trades with paper_* status."""
    # Seed a paper trade (sum_arb trade from fixture was 'filled' = Live;
    # Live data now comes from Kalshi API, so seed a paper one for this test)
    await db.record_trade(
        strategy="sports_midshot", market_id="KXNBAGAME-test", direction="yes",
        price=0.30, size=3.0, fee=0.07, status="paper_settled", pnl=6.0,
    )
    app = create_app(db, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    assert data["sports_midshot"]["paper"]["trade_count"] == 1
    assert data["sports_midshot"]["paper"]["total_pnl"] == 6.0
    assert data["sports_midshot"]["paper"]["wins"] == 1
    # Live bucket empty when no kalshi_client provided
    assert data["sports_midshot"]["live"]["trade_count"] == 0


@pytest.mark.asyncio
async def test_get_performance_live_bucket_from_kalshi(db):
    """Live bucket is reconstructed from Kalshi API — positions + settlements."""
    class MockKalshi:
        def get_positions(self):
            return [
                {"ticker": "KXFEDMENTION-26APR-SHOC", "position_fp": "156",
                 "market_exposure_dollars": "45.16"},
                {"ticker": "KXCPI-26APR-T0.4", "position_fp": "1",
                 "market_exposure_dollars": "0.38"},
                {"ticker": "KXFEDMENTION-26APR-CLOSED", "position_fp": "0",
                 "market_exposure_dollars": "0"},  # zero position, skip
            ]
        def get_settlements(self, limit=200):
            return [
                # Mentions trader winning settle
                {"ticker": "KXFEDMENTION-26MAR-OLD",
                 "revenue": 2000, "yes_total_cost_dollars": "10.00",
                 "no_total_cost_dollars": "0", "fee_cost": "0.50",
                 "market_result": "yes"},
                # Econ trader losing settle
                {"ticker": "KXCPI-26MAR-T0.3",
                 "revenue": 0, "yes_total_cost_dollars": "0",
                 "no_total_cost_dollars": "5.00", "fee_cost": "0.10",
                 "market_result": "yes"},
            ]

    app = create_app(db, kalshi_client=MockKalshi())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    # mentions_trader: 1 open position (SHOC) + 1 winning settle = 2 trades
    m = data["mentions_trader"]["live"]
    assert m["trade_count"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 0
    # net = 20 - 10 - 0.5 = 9.5 (settled only; open position has no PnL yet)
    assert m["total_pnl"] == pytest.approx(9.5, abs=0.01)
    # total_size = 10 (settled cost) + 45.16 (open exposure)
    assert m["total_size"] == pytest.approx(55.16, abs=0.01)
    # econ_trader: 1 open position (CPI-T0.4) + 1 losing settle
    e = data["econ_trader"]["live"]
    assert e["trade_count"] == 2
    assert e["losses"] == 1
    assert e["total_pnl"] == pytest.approx(-5.10, abs=0.01)  # 0 - 5 - 0.10


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
async def test_settlement_history_categorizes_by_strategy_name(db):
    """Settlement history groups settlements into strategy-name categories.
    Ensures econ_trader settlements don't get dumped into 'other' bucket."""
    class MockKalshi:
        def get_settlements(self, limit=200):
            return [
                # Econ trader loss
                {"ticker": "KXCPI-26MAR-T0.3", "revenue": 0,
                 "yes_total_cost_dollars": "0", "no_total_cost_dollars": "5.00",
                 "fee_cost": "0.10", "market_result": "yes", "settled_time": "2026-04-10T13:00Z"},
                # Sports trader win
                {"ticker": "KXNBAGAME-26APR10TEAM-X", "revenue": 1800,
                 "yes_total_cost_dollars": "6.00", "no_total_cost_dollars": "0",
                 "fee_cost": "0.30", "market_result": "yes", "settled_time": "2026-04-11T02:00Z"},
                # Mentions trader (future)
                {"ticker": "KXFEDMENTION-26APR-FAKE", "revenue": 1000,
                 "yes_total_cost_dollars": "8.00", "no_total_cost_dollars": "0",
                 "fee_cost": "0.20", "market_result": "yes", "settled_time": "2026-04-30T20:00Z"},
                # Shutdown fade
                {"ticker": "KXNUMSHUTDOWNS-27JAN01-T2", "revenue": 2000,
                 "yes_total_cost_dollars": "0", "no_total_cost_dollars": "5.00",
                 "fee_cost": "0.10", "market_result": "no", "settled_time": "2027-01-01T15:00Z"},
            ]
        def get_positions(self):
            return []

    app = create_app(db, kalshi_client=MockKalshi())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/settlement-history")
    data = resp.json()
    assert "econ_trader" in data
    assert data["econ_trader"]["losses"] == 1
    assert data["sports_trader"]["wins"] == 1
    assert data["mentions_trader"]["wins"] == 1
    assert data["shutdown_fade"]["wins"] == 1
    # 'other' bucket stays empty because all tickers match a known strategy
    assert data["other"]["wins"] == 0 and data["other"]["losses"] == 0


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
