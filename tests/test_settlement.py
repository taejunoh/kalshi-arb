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
    order_mgr._persist_bankroll = AsyncMock()
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


def test_settle_win_credits_full_payout():
    """Bankroll on win should += payout (size/price), NOT just pnl.
    Reason: placement already deducted size+fee. Settlement must refund full
    payout (capital + profit) for the bankroll math to close.
    Bug history: previously bankroll += pnl, which under-credited wins by size.
    """
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "spread_scanner",
         "direction": "yes", "price": 0.30, "size": 30.0, "fee": 2.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "yes"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.settle_trade.assert_called_once()
    # Payout = size / price = 30 / 0.30 = 100
    assert order_mgr.bankroll == pytest.approx(1100.0, abs=0.01)


def test_settle_loss_does_not_drain_bankroll():
    """Bankroll on loss should += 0, NOT += pnl (which is -size).
    Reason: placement already deducted size. Settlement must not deduct again.
    Bug history: previously bankroll += pnl = bankroll -= size, causing
    phantom double-deduction after restart-sync (where placement was reset
    but pending trades remained in DB).
    """
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "spread_scanner",
         "direction": "yes", "price": 0.30, "size": 30.0, "fee": 2.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "no"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.settle_trade.assert_called_once()
    # Loss: bankroll unchanged (capital already deducted at placement)
    assert order_mgr.bankroll == pytest.approx(1000.0, abs=0.01)


def test_settle_win_no_side_credits_payout():
    """NO-side win also credits full payout."""
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "shutdown_fade",
         "direction": "no", "price": 0.40, "size": 40.0, "fee": 3.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "no"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    # Payout = 40 / 0.40 = 100
    assert order_mgr.bankroll == pytest.approx(1100.0, abs=0.01)


def test_settle_pnl_record_unchanged():
    """settle_trade should still record the ECONOMIC PnL (not the bankroll delta).
    settle_trade(id, pnl) — pnl is the net gain/loss for performance tracking.
    """
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 42, "market_id": "MKT-1", "strategy": "spread_scanner",
         "direction": "yes", "price": 0.30, "size": 30.0, "fee": 2.0, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "yes"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    # settle_trade was called with the economic PnL (68.0), not the payout (100)
    args, _ = db.settle_trade.call_args
    assert args[0] == 42
    assert args[1] == pytest.approx(68.0, abs=0.01)


def test_settled_cache_evicts_oldest_when_over_cap():
    """Cache must not grow unbounded; capped at MAX_SETTLED_CACHE entries."""
    from core.settlement import SettlementChecker, MAX_SETTLED_CACHE
    checker, _, _, _ = _make_checker()
    # Fill just past the cap — oldest entries should get evicted
    for i in range(MAX_SETTLED_CACHE + 5):
        checker._remember_settled(f"T-{i}", "yes", 1.0, 0.0)
    assert len(checker._settled_cache) == MAX_SETTLED_CACHE
    # The earliest 5 should be gone; the newest should remain
    assert "T-0" not in checker._settled_cache
    assert "T-4" not in checker._settled_cache
    assert f"T-{MAX_SETTLED_CACHE + 4}" in checker._settled_cache


def test_settle_cache_hit_still_writes_non_null_closing_snapshot():
    """Across two check_and_settle calls on the same ticker, the second
    call hits _settled_cache and historically left closing yes_ask/no_ask
    as NULL. Regression test ensures the cache carries the asks too."""
    checker, client, db, order_mgr = _make_checker()
    db.record_clv_snapshot = AsyncMock()
    client.get_market.return_value = {"status": "finalized", "result": "yes",
                                       "yes_ask_dollars": "1.00", "no_ask_dollars": "0.00"}

    import asyncio
    # Call A: trade 1 settles, market fetched, cache populated
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "sports_pinnacle_edge",
         "direction": "yes", "price": 0.30, "size": 3.0, "fee": 0.07, "status": "paper_filled"},
    ])
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())

    # Call B: trade 2 settles from a later batch — cache HIT (result only)
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 2, "market_id": "MKT-1", "strategy": "sports_pinnacle_edge",
         "direction": "no", "price": 0.70, "size": 3.0, "fee": 0.07, "status": "paper_filled"},
    ])
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())

    assert db.record_clv_snapshot.await_count == 2
    for call in db.record_clv_snapshot.await_args_list:
        assert call.kwargs["yes_ask"] is not None, "closing yes_ask must not be NULL even on cache hit"
        assert call.kwargs["no_ask"] is not None, "closing no_ask must not be NULL even on cache hit"


def test_settle_persists_bankroll_after_win_credit():
    """Win credit must also write through to bot_state so a restart doesn't lose it."""
    checker, client, db, order_mgr = _make_checker()
    order_mgr._persist_bankroll = AsyncMock()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 1, "market_id": "MKT-1", "strategy": "sports_pinnacle_edge",
         "direction": "yes", "price": 0.30, "size": 3.0, "fee": 0.07, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "yes",
                                       "yes_ask_dollars": "1.00", "no_ask_dollars": "0.00"}
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    order_mgr._persist_bankroll.assert_awaited()


def test_settle_records_closing_clv_snapshot():
    """On settlement, a 'closing' CLV snapshot is recorded for the trade."""
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 7, "market_id": "NBA-LAL-BOS", "strategy": "sports_pinnacle_edge",
         "direction": "yes", "price": 0.30, "size": 3.0, "fee": 0.07, "status": "paper_filled"},
    ])
    client.get_market.return_value = {
        "status": "finalized", "result": "yes",
        "yes_ask_dollars": "1.00", "no_ask_dollars": "0.00",
    }
    db.record_clv_snapshot = AsyncMock()
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.record_clv_snapshot.assert_awaited_once_with(
        trade_id=7, snapshot_at="closing",
        yes_ask=1.0, no_ask=0.0, pinnacle_prob=None,
    )


def test_settle_closing_snapshot_tolerates_missing_asks():
    """If market lacks yes_ask_dollars/no_ask_dollars, snapshot uses NULL values and still records."""
    checker, client, db, order_mgr = _make_checker()
    db.get_unsettled_trades = AsyncMock(return_value=[
        {"id": 7, "market_id": "NBA-LAL-BOS", "strategy": "sports_pinnacle_edge",
         "direction": "yes", "price": 0.30, "size": 3.0, "fee": 0.07, "status": "paper_filled"},
    ])
    client.get_market.return_value = {"status": "finalized", "result": "yes"}
    db.record_clv_snapshot = AsyncMock()
    import asyncio
    asyncio.get_event_loop().run_until_complete(checker.check_and_settle())
    db.record_clv_snapshot.assert_awaited_once()
    call_kwargs = db.record_clv_snapshot.await_args.kwargs
    assert call_kwargs["trade_id"] == 7
    assert call_kwargs["snapshot_at"] == "closing"


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
