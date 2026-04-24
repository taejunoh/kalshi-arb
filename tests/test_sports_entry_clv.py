"""Unit tests for sports entry CLV snapshot helper."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategies.sports_trader import SportsTraderStrategy


_GAME = {"id": "g1", "home_team": "H", "away_team": "A", "bookmakers": []}


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
    client.get_market_prices = MagicMock(return_value=(0.45, 0.57))
    return SportsTraderStrategy(config, order_mgr, client)


def _patch_dual(strat, us_prob=0.50, eu_prob=0.50, pin_prob=0.48, age_s=120):
    """Patch _dual_region_consensus to avoid real network calls."""
    async def _fake(sport, game):
        return us_prob, eu_prob, pin_prob, age_s
    strat._dual_region_consensus = _fake


@pytest.mark.asyncio
async def test_record_entry_clv_calls_db_with_trade_id():
    strat = _make_strategy()
    _patch_dual(strat)
    strat._client.get_order_book_depth = MagicMock(return_value=(12.0, 34.0))
    await strat._record_entry_clv(
        trade_id=42, ticker="KXNBAGAME-26APR01DENMIA-DEN",
        game=_GAME, sport="basketball_nba", pinnacle_prob=0.50,
    )
    strat._order_manager._db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=42, snapshot_at="entry",
        yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50,
        us_consensus_prob=0.50, eu_consensus_prob=0.50,
        pinnacle_age_s=120,
        kalshi_yes_bid=None, kalshi_yes_ask=0.45,
        kalshi_yes_depth=12.0, kalshi_no_depth=34.0,
    )


@pytest.mark.asyncio
async def test_record_entry_clv_allows_null_pinnacle():
    """If Pinnacle absent for this game, snapshot still records with pinnacle_prob=None."""
    strat = _make_strategy()
    _patch_dual(strat, pin_prob=None)
    strat._client.get_order_book_depth = MagicMock(return_value=(5.0, 5.0))
    await strat._record_entry_clv(
        trade_id=7, ticker="KXNBAGAME-26APR01DENMIA-DEN",
        game=_GAME, sport="basketball_nba", pinnacle_prob=None,
    )
    strat._order_manager._db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=7, snapshot_at="entry",
        yes_ask=0.45, no_ask=0.57, pinnacle_prob=None,
        us_consensus_prob=0.50, eu_consensus_prob=0.50,
        pinnacle_age_s=120,
        kalshi_yes_bid=None, kalshi_yes_ask=0.45,
        kalshi_yes_depth=5.0, kalshi_no_depth=5.0,
    )


@pytest.mark.asyncio
async def test_record_entry_clv_uses_get_top_of_book_when_available():
    """If client has get_top_of_book, kalshi_yes_bid/ask are populated from it."""
    strat = _make_strategy()
    strat._client.get_top_of_book = MagicMock(return_value=(0.44, 0.46))
    strat._client.get_order_book_depth = MagicMock(return_value=(0.0, 100.0))
    _patch_dual(strat)
    await strat._record_entry_clv(
        trade_id=99, ticker="KXNBAGAME-26APR01DENMIA-DEN",
        game=_GAME, sport="basketball_nba", pinnacle_prob=0.50,
    )
    strat._order_manager._db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=99, snapshot_at="entry",
        yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50,
        us_consensus_prob=0.50, eu_consensus_prob=0.50,
        pinnacle_age_s=120,
        kalshi_yes_bid=0.44, kalshi_yes_ask=0.46,
        kalshi_yes_depth=0.0, kalshi_no_depth=100.0,
    )


@pytest.mark.asyncio
async def test_record_entry_clv_depth_exception_falls_back_to_none():
    """If get_order_book_depth raises, snapshot still succeeds with depth=None."""
    strat = _make_strategy()
    _patch_dual(strat)
    strat._client.get_order_book_depth = MagicMock(side_effect=Exception("book unavailable"))
    await strat._record_entry_clv(
        trade_id=1, ticker="KXNBAGAME-26APR01DENMIA-DEN",
        game=_GAME, sport="basketball_nba", pinnacle_prob=0.50,
    )
    call_kwargs = strat._order_manager._db.record_clv_snapshot.call_args.kwargs
    assert call_kwargs["kalshi_yes_depth"] is None
    assert call_kwargs["kalshi_no_depth"] is None
