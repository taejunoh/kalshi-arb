import pytest
from unittest.mock import MagicMock, AsyncMock
from core.settlement import SettlementChecker


def _make_checker():
    client = MagicMock()
    db = MagicMock()
    db.get_unsettled_trades = AsyncMock(return_value=[])
    db.settle_trade = AsyncMock()
    order_mgr = MagicMock()
    order_mgr.bankroll = 1000.0
    return SettlementChecker(client, db, order_mgr), client, db, order_mgr


def test_pnl_win_yes():
    checker, _, _, _ = _make_checker()
    # Bought YES at $0.30, market settles YES → win
    pnl = checker.calculate_pnl("yes", 0.30, 30.0, 2.0, "yes")
    # pnl = 30 * (0.70 / 0.30) - 2 = 70 - 2 = 68
    assert pnl == pytest.approx(68.0, abs=0.01)


def test_pnl_lose_yes():
    checker, _, _, _ = _make_checker()
    # Bought YES at $0.30, market settles NO → lose
    pnl = checker.calculate_pnl("yes", 0.30, 30.0, 2.0, "no")
    assert pnl == -30.0


def test_pnl_win_no():
    checker, _, _, _ = _make_checker()
    # Bought NO at $0.40, market settles NO → win
    pnl = checker.calculate_pnl("no", 0.40, 40.0, 3.0, "no")
    # pnl = 40 * (0.60 / 0.40) - 3 = 60 - 3 = 57
    assert pnl == pytest.approx(57.0, abs=0.01)


def test_pnl_lose_no():
    checker, _, _, _ = _make_checker()
    # Bought NO at $0.40, market settles YES → lose
    pnl = checker.calculate_pnl("no", 0.40, 40.0, 3.0, "yes")
    assert pnl == -40.0


def test_settle_updates_bankroll():
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "spread_scanner",
         "direction": "yes", "price": 0.30, "size": 30.0, "fee": 2.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "yes"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.settle_trade.assert_called_once()
    # Bankroll should increase by PnL (68.0)
    assert order_mgr.bankroll == pytest.approx(1068.0, abs=0.01)


def test_skip_non_finalized():
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "spread_scanner",
         "direction": "yes", "price": 0.30, "size": 30.0, "fee": 2.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "active", "result": ""}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.settle_trade.assert_not_called()
    assert order_mgr.bankroll == 1000.0
