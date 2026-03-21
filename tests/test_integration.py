import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from core.config import Config
from core.database import Database
from core.order_manager import OrderManager
from core.market_scanner import MarketScanner
from strategies.sum_arb import SumArbStrategy
from strategies.maker_preorder import MakerPreorderStrategy


class FakeKalshiClient:
    def __init__(self):
        self.markets = [
            {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
        ]
        self._prices = {"KXBTC-25MAR20-T70000": (0.40, 0.40)}
        self._depths = {"KXBTC-25MAR20-T70000": (200.0, 150.0)}

    def get_active_markets(self):
        return self.markets

    def get_market_prices(self, ticker):
        return self._prices.get(ticker, (None, None))

    def get_order_book_depth(self, ticker):
        return self._depths.get(ticker, (0.0, 0.0))


@pytest_asyncio.fixture
async def setup(tmp_path):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    config.SUM_ARB_MIN_GAP = 0.03
    config.KALSHI_FEE_PER_CONTRACT = 0.07

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    client = FakeKalshiClient()
    scanner = MarketScanner(client)
    scanner.update_markets(client.get_active_markets())

    order_mgr = OrderManager(config, client, db)
    sum_arb = SumArbStrategy(config, order_mgr, scanner, client)
    maker = MakerPreorderStrategy(config, order_mgr, scanner, client)

    return {"config": config, "db": db, "client": client, "scanner": scanner, "order_mgr": order_mgr, "sum_arb": sum_arb, "maker": maker}


@pytest.mark.asyncio
async def test_full_sum_arb_paper_flow(setup):
    s = setup
    await s["sum_arb"].scan_and_execute()
    trades = await s["db"].get_trades()
    assert len(trades) == 2
    assert s["scanner"].is_locked("KXBTC-25MAR20-T70000")
    assert s["order_mgr"].bankroll < 1000.0


@pytest.mark.asyncio
async def test_no_trade_when_no_opportunity(setup):
    s = setup
    s["client"]._prices = {"KXBTC-25MAR20-T70000": (0.50, 0.49)}
    await s["sum_arb"].scan_and_execute()
    trades = await s["db"].get_trades()
    assert len(trades) == 0
    assert s["order_mgr"].bankroll == 1000.0


@pytest.mark.asyncio
async def test_maker_preorder_places_orders_on_short_market(setup):
    s = setup
    close_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    s["client"].markets.append({
        "ticker": "KXBTC-25MAR20-T71000",
        "status": "open",
        "close_time": close_time,
    })
    s["scanner"].update_markets(s["client"].get_active_markets())
    await s["maker"].scan_and_execute()
    assert "KXBTC-25MAR20-T71000" in s["maker"]._active_orders
    assert s["scanner"].is_locked("KXBTC-25MAR20-T71000")


@pytest.mark.asyncio
async def test_maker_preorder_ignores_far_market(setup):
    s = setup
    await s["maker"].scan_and_execute()
    assert len(s["maker"]._active_orders) == 0
