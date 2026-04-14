"""cancel_order must credit size+fee back to bankroll for orders that never
filled. Without this, market_maker/mentions_trader's requote churn drifts
bankroll downward indefinitely (16K+ historical cancels).
"""
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
async def test_cancel_paper_open_restores_bankroll(paper_manager, db):
    """Cancelling a paper limit order that never filled must give back size+fee."""
    before = paper_manager.bankroll  # 1000.0
    result = await paper_manager.limit_order(
        ticker="T-1", side="yes", price=0.40, size_usdc=10.0,
        strategy="mentions_trader", market_id="T-1",
    )
    order_id = result["order_id"]
    # Deducted 10 + fee; exact fee depends on price/count but bankroll decreased
    assert paper_manager.bankroll < before

    await paper_manager.cancel_order(order_id)

    # After cancel, bankroll should be restored to the original.
    assert paper_manager.bankroll == pytest.approx(before, abs=0.01)


@pytest.mark.asyncio
async def test_cancel_filled_order_does_NOT_restore(paper_manager, db):
    """If an order was already filled (converted to paper_filled), cancel
    should not double-credit — the capital is already committed to a position."""
    result = await paper_manager.limit_order(
        ticker="T-1", side="yes", price=0.40, size_usdc=10.0,
        strategy="mentions_trader", market_id="T-1",
    )
    order_id = result["order_id"]
    after_place = paper_manager.bankroll

    # Simulate that the order filled (status flipped by settlement simulator)
    await db.update_order_status(order_id, "paper_filled")

    await paper_manager.cancel_order(order_id)

    # Bankroll unchanged — filled orders don't refund on cancel
    assert paper_manager.bankroll == pytest.approx(after_place, abs=0.01)


@pytest.mark.asyncio
async def test_cancel_persists_restored_bankroll(paper_manager, db):
    """Restored bankroll must also write through to bot_state."""
    before = paper_manager.bankroll
    result = await paper_manager.limit_order(
        ticker="T-1", side="yes", price=0.40, size_usdc=10.0,
        strategy="mentions_trader", market_id="T-1",
    )
    await paper_manager.cancel_order(result["order_id"])

    persisted = await db.get_bot_state("bankroll")
    assert persisted == pytest.approx(before, abs=0.01)


@pytest.mark.asyncio
async def test_cancel_unknown_order_is_noop(paper_manager, db):
    """Cancelling an order_id that doesn't exist in DB should not explode
    and should not touch bankroll."""
    before = paper_manager.bankroll
    await paper_manager.cancel_order("paper_nonexistent")
    assert paper_manager.bankroll == before
