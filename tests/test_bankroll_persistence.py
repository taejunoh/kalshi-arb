"""Tests for OrderManager bankroll persistence + hydration via bot_state."""
import pytest
import pytest_asyncio

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database


@pytest.fixture
def paper_manager(db):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    return OrderManager(config, kalshi_client=None, database=db)


@pytest.mark.asyncio
async def test_hydrate_bankroll_uses_initial_when_db_empty(paper_manager, db):
    await paper_manager.hydrate_bankroll()
    assert paper_manager.bankroll == 1000.0


@pytest.mark.asyncio
async def test_hydrate_bankroll_restores_from_db(paper_manager, db):
    await db.set_bot_state("bankroll", 742.33)
    await paper_manager.hydrate_bankroll()
    assert paper_manager.bankroll == 742.33


@pytest.mark.asyncio
async def test_paper_market_order_persists_bankroll(paper_manager, db):
    await paper_manager.market_order(
        ticker="T-1", side="yes", size_usdc=3.0, price=0.40,
        strategy="sports_pinnacle_edge", market_id="T-1",
    )
    persisted = await db.get_bot_state("bankroll")
    # placement: size 3 + fee (~$0.07 * 7 contracts = $0.49) → roughly $996.51
    assert persisted is not None
    assert abs(persisted - paper_manager.bankroll) < 0.01


@pytest.mark.asyncio
async def test_paper_limit_order_persists_bankroll(paper_manager, db):
    await paper_manager.limit_order(
        ticker="T-1", side="yes", price=0.40, size_usdc=3.0,
        strategy="sports_pinnacle_edge", market_id="T-1",
    )
    persisted = await db.get_bot_state("bankroll")
    assert persisted is not None
    assert abs(persisted - paper_manager.bankroll) < 0.01


@pytest.mark.asyncio
async def test_bankroll_survives_simulated_restart(paper_manager, db):
    """Place a trade → make a new OrderManager → hydrate → matches."""
    await paper_manager.market_order(
        ticker="T-1", side="yes", size_usdc=3.0, price=0.40,
        strategy="sports_pinnacle_edge", market_id="T-1",
    )
    pre_restart = paper_manager.bankroll

    # Simulate restart
    config2 = Config()
    config2.TRADING_MODE = "paper"
    config2.INITIAL_BANKROLL = 1000.0
    fresh_mgr = OrderManager(config2, kalshi_client=None, database=db)
    await fresh_mgr.hydrate_bankroll()
    assert abs(fresh_mgr.bankroll - pre_restart) < 0.01
