import pytest
import pytest_asyncio
from core.config import Config
from core.database import Database
from core.order_manager import OrderManager
from core.market_scanner import MarketScanner
from strategies.sum_arb import SumArbStrategy
from strategies.maker_preorder import MakerPreorderStrategy


class FakePolymarketClient:
    def __init__(self):
        self.markets = [
            {
                "conditionId": "test_market_1",
                "question": "Will BTC go up in the next 15 minutes?",
                "tokens": [
                    {"outcome": "Yes", "token_id": "yes_1"},
                    {"outcome": "No", "token_id": "no_1"},
                ],
            }
        ]
        self._prices = {"yes_1": 0.45, "no_1": 0.48}
        self._depths = {"yes_1": 200.0, "no_1": 150.0}

    def get_active_markets(self):
        return self.markets

    def get_token_ids(self, market):
        tokens = market.get("tokens", [])
        yes_id = no_id = None
        for t in tokens:
            if t["outcome"].lower() in ("yes", "up"):
                yes_id = t["token_id"]
            elif t["outcome"].lower() in ("no", "down"):
                no_id = t["token_id"]
        return yes_id, no_id

    def get_last_trade_price(self, token_id):
        return self._prices.get(token_id)

    def get_order_book_depth(self, token_id):
        return self._depths.get(token_id, 0)


@pytest_asyncio.fixture
async def setup(tmp_path):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    config.SUM_ARB_MIN_GAP = 0.03

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    client = FakePolymarketClient()
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
    assert s["scanner"].is_locked("test_market_1")
    assert s["order_mgr"].bankroll < 1000.0


@pytest.mark.asyncio
async def test_no_trade_when_no_opportunity(setup):
    s = setup
    s["client"]._prices = {"yes_1": 0.50, "no_1": 0.49}
    await s["sum_arb"].scan_and_execute()
    trades = await s["db"].get_trades()
    assert len(trades) == 0
    assert s["order_mgr"].bankroll == 1000.0


@pytest.mark.asyncio
async def test_maker_preorder_places_orders_on_5min_market(setup):
    """Maker Pre-Order should place limit orders on 5-min markets."""
    s = setup
    # Add a 5-minute market
    s["client"].markets.append({
        "conditionId": "test_5min_1",
        "question": "Will BTC go up in the next 5 minutes?",
        "tokens": [
            {"outcome": "Yes", "token_id": "yes_5m"},
            {"outcome": "No", "token_id": "no_5m"},
        ],
    })
    s["scanner"].update_markets(s["client"].get_active_markets())

    await s["maker"].scan_and_execute()

    # Should have active orders tracked
    assert "test_5min_1" in s["maker"]._active_orders
    # Market should be locked
    assert s["scanner"].is_locked("test_5min_1")


@pytest.mark.asyncio
async def test_maker_preorder_ignores_15min_market(setup):
    """Maker Pre-Order should NOT place orders on 15-min markets."""
    s = setup
    # Only 15-min market exists in default setup
    await s["maker"].scan_and_execute()

    assert len(s["maker"]._active_orders) == 0
