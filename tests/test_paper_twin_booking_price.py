"""`OrderManager.market_order(booking_price=...)` reaches ONLY the paper
ledger; the live dispatcher always keeps `price`.

Guards the tennis_live_fade fair-price artifact fix: a LIVE strategy whose
limit rests below the ask must still send `price` to Kalshi while its paper
twin books the honest ask.
"""
import asyncio

import aiosqlite
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from core.config import Config
from core.database import Database
from core.order_manager import (
    LIMIT_TTL_STRATEGIES,
    PAPER_TWIN_STRATEGIES,
    OrderManager,
)

NAME = "sports_tennis_live_fade_v1"


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    await database.initialize()
    yield database
    await database.close()


def _om(db, live=False):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    config.LIVE_STRATEGIES = {NAME} if live else set()
    return OrderManager(config, kalshi_client=None, database=db)


async def _trade_price(db, trade_id):
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT price FROM trades WHERE id = ?", (trade_id,)
        )
        row = await cur.fetchone()
    return None if row is None else row[0]


async def _one_trade(db, strategy):
    """Single trade row for `strategy` — asserts exactly one exists."""
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT price, status FROM trades WHERE strategy = ?", (strategy,)
        )
        rows = await cur.fetchall()
    assert len(rows) == 1, f"expected 1 row for {strategy}, got {rows}"
    return rows[0]


async def _drain_tasks():
    """Await every task the call under test spawned (the paper twin runs via
    asyncio.create_task). Deterministic — no sleep-and-hope."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)


# --------------------------------------------------------------------------
# pure-paper path
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_path_books_booking_price(db):
    om = _om(db)
    r = await om.market_order(
        ticker="KX-T", side="yes", size_usdc=4.0, price=0.74,
        strategy=NAME, market_id="KX-T", booking_price=0.86,
    )
    assert r["status"] == "paper_filled"
    assert await _trade_price(db, r["trade_id"]) == pytest.approx(0.86)


@pytest.mark.asyncio
async def test_omitted_booking_price_is_backward_compatible(db):
    """Every caller that doesn't pass booking_price must be unaffected."""
    om = _om(db)
    r = await om.market_order(
        ticker="KX-T2", side="yes", size_usdc=4.0, price=0.45,
        strategy="sports_pinnacle_edge", market_id="KX-T2",
    )
    assert await _trade_price(db, r["trade_id"]) == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_booking_price_is_clamped(db):
    om = _om(db)
    r = await om.market_order(
        ticker="KX-T3", side="yes", size_usdc=4.0, price=0.74,
        strategy=NAME, market_id="KX-T3", booking_price=1.40,
    )
    assert await _trade_price(db, r["trade_id"]) == pytest.approx(0.99)


# --------------------------------------------------------------------------
# live path + paper twin
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_order_keeps_price_twin_gets_booking_price(db):
    om = _om(db, live=True)
    om._live_limit_ttl_order = AsyncMock(return_value={"status": "skipped"})
    twin = AsyncMock(return_value={"status": "paper_filled"})
    om._paper_market_order = twin

    await om.market_order(
        ticker="KX-T", side="yes", size_usdc=4.0, price=0.74,
        strategy=NAME, market_id="KX-T", booking_price=0.86,
    )
    await _drain_tasks()

    # live dispatcher: positional (ticker, side, size_usdc, price, ...)
    assert om._live_limit_ttl_order.await_args.args[3] == 0.74
    # paper twin: positional (ticker, side, size_usdc, price, ...)
    assert twin.await_args.args[3] == 0.86


@pytest.mark.asyncio
async def test_live_twin_writes_honest_price_to_trades(db):
    """End-to-end: the row the twin actually persists carries the ask."""
    om = _om(db, live=True)
    om._live_limit_ttl_order = AsyncMock(return_value={"status": "skipped"})

    await om.market_order(
        ticker="KX-T", side="yes", size_usdc=4.0, price=0.74,
        strategy=NAME, market_id="KX-T", booking_price=0.86,
    )
    await _drain_tasks()

    price, status = await _one_trade(db, NAME)
    assert status == "paper_filled"
    assert price == pytest.approx(0.86)


@pytest.mark.asyncio
async def test_live_twin_without_booking_price_unchanged(db):
    """A live+twin strategy that supplies no booking price keeps booking at
    the limit — the v31 family must not shift."""
    om = _om(db, live=True)
    om.config.LIVE_STRATEGIES = {"sports_longshot_v31"}
    om._live_limit_ttl_order = AsyncMock(return_value={"status": "skipped"})

    await om.market_order(
        ticker="KX-T4", side="yes", size_usdc=3.0, price=0.30,
        strategy="sports_longshot_v31", market_id="KX-T4",
    )
    await _drain_tasks()

    price, status = await _one_trade(db, "sports_longshot_v31")
    assert status == "paper_filled"
    assert price == pytest.approx(0.30)


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", [
    "sports_tennis_live_fade_v1",
    "sports_tennis_underdog_v1",
    "sports_mlb_pitcher_edge_v1",
    "sports_mlb_pitcher_edge_v2_1",
])
async def test_all_four_fixed_strategies_route_through_twin(db, strategy):
    """Each fixed strategy really does take the twin path, so its honest
    booking price lands in the ledger."""
    assert strategy in PAPER_TWIN_STRATEGIES
    assert strategy in LIMIT_TTL_STRATEGIES

    om = _om(db)
    om.config.LIVE_STRATEGIES = {strategy}
    om._live_limit_ttl_order = AsyncMock(return_value={"status": "skipped"})

    await om.market_order(
        ticker="KX-A", side="yes", size_usdc=4.0, price=0.55,
        strategy=strategy, market_id="KX-A", booking_price=0.62,
    )
    await _drain_tasks()

    price, _status = await _one_trade(db, strategy)
    assert price == pytest.approx(0.62)
    assert om._live_limit_ttl_order.await_args.args[3] == 0.55
