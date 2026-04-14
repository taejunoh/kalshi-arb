"""Unit tests for sports entry CLV snapshot helper."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.sports_trader import SportsTraderStrategy


def _make_strategy() -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = 100.0
    order_mgr._db = MagicMock()
    order_mgr._db.record_clv_snapshot = AsyncMock()
    client = MagicMock()
    return SportsTraderStrategy(config, order_mgr, client)


@pytest.mark.asyncio
async def test_record_entry_clv_calls_db_with_trade_id():
    strat = _make_strategy()
    order_result = {"status": "paper_filled", "trade_id": 42, "size_usdc": 3.0}
    await strat._record_entry_clv(order_result, yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50)
    strat._order_manager._db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=42, snapshot_at="entry", yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50,
    )


@pytest.mark.asyncio
async def test_record_entry_clv_skips_when_order_failed():
    strat = _make_strategy()
    order_result = {"status": "skipped", "reason": "insufficient_bankroll"}
    await strat._record_entry_clv(order_result, yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50)
    strat._order_manager._db.record_clv_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_entry_clv_skips_when_trade_id_missing():
    """Live orders that return status=failed lack trade_id — skip silently."""
    strat = _make_strategy()
    order_result = {"status": "paper_filled"}  # no trade_id
    await strat._record_entry_clv(order_result, yes_ask=0.45, no_ask=0.57, pinnacle_prob=None)
    strat._order_manager._db.record_clv_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_entry_clv_allows_null_pinnacle():
    """If Pinnacle absent for this game, snapshot still records with pinnacle_prob=None."""
    strat = _make_strategy()
    order_result = {"status": "paper_filled", "trade_id": 7, "size_usdc": 3.0}
    await strat._record_entry_clv(order_result, yes_ask=0.30, no_ask=0.72, pinnacle_prob=None)
    strat._order_manager._db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=7, snapshot_at="entry", yes_ask=0.30, no_ask=0.72, pinnacle_prob=None,
    )
