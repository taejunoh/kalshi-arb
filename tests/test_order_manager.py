import pytest
import pytest_asyncio
from core.order_manager import OrderManager
from core.config import Config
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def paper_manager(db):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    return OrderManager(config, kalshi_client=None, database=db)


def test_initial_bankroll(paper_manager):
    assert paper_manager.bankroll == 1000.0


@pytest.mark.asyncio
async def test_paper_market_order(paper_manager):
    result = await paper_manager.market_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", size_usdc=100.0,
        price=0.50, strategy="sum_arb", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_filled"
    assert result["size_usdc"] == 100.0
    assert paper_manager.bankroll == 900.0


@pytest.mark.asyncio
async def test_paper_limit_order(paper_manager):
    result = await paper_manager.limit_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", price=0.48,
        size_usdc=50.0, strategy="maker_preorder", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_open"
    assert result["price"] == 0.48


@pytest.mark.asyncio
async def test_position_sizing(paper_manager):
    size = paper_manager.calculate_position_size(net_edge=0.05)
    assert size == 12.5


@pytest.mark.asyncio
async def test_circuit_breaker(paper_manager):
    paper_manager._daily_loss = 201.0
    assert paper_manager.is_circuit_breaker_active() is True
    paper_manager._daily_loss = 50.0
    assert paper_manager.is_circuit_breaker_active() is False


@pytest.mark.asyncio
async def test_consecutive_loss_tracking(paper_manager):
    paper_manager.record_loss()
    paper_manager.record_loss()
    assert paper_manager._consecutive_losses == 2
    paper_manager.record_win()
    assert paper_manager._consecutive_losses == 0
