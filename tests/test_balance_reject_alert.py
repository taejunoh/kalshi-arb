"""Telegram alert fires on balance-rejected live entries.

Both rejection sites in OrderManager (`_live_market_order` and
`_live_limit_ttl_order`) must call `_alert_balance_reject` when the
order is blocked before reaching Kalshi's book — otherwise the user
has no signal to top up cash and the auto-retry loop spins quietly.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.order_manager import OrderManager


def _make_manager(balance_value):
    config = MagicMock()
    config.INITIAL_BANKROLL = 1000.0
    config.is_paper_mode = True
    config.LIVE_STRATEGIES = {"sports_underdog_v31"}
    config.estimate_fee = MagicMock(return_value=0.5)
    client = MagicMock()
    client.get_balance = MagicMock(return_value=balance_value)
    db = MagicMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    om = OrderManager(config, client, db, notifier=notifier)
    return om, notifier


def _run(coro):
    return asyncio.run(coro)


def test_market_order_alerts_on_insufficient_balance():
    om, notifier = _make_manager(balance_value=10.79)
    res = _run(om._live_market_order(
        ticker="KX-NHL-1", side="no", size_usdc=20.0, price=0.40,
        strategy="sports_underdog_v31", market_id="KX-NHL-1",
    ))
    assert res["status"] == "failed"
    assert res["reason"] == "insufficient_balance"
    # Drain pending tasks so the asyncio.create_task call resolves
    asyncio.run(asyncio.sleep(0))
    notifier.send.assert_called_once()
    text, kwargs = notifier.send.call_args[0][0], notifier.send.call_args[1]
    assert "insufficient balance" in text
    assert "sports_underdog_v31" in text
    assert "KX-NHL-1" in text
    assert "$10.79" in text
    assert kwargs["msg_type"] == "balance_reject"


def test_market_order_alerts_on_balance_check_failed():
    om, notifier = _make_manager(balance_value=None)
    res = _run(om._live_market_order(
        ticker="KX-NHL-2", side="yes", size_usdc=20.0, price=0.40,
        strategy="sports_underdog_v31", market_id="KX-NHL-2",
    ))
    assert res["status"] == "failed"
    assert res["reason"] == "balance_check_failed"
    asyncio.run(asyncio.sleep(0))
    notifier.send.assert_called_once()
    text = notifier.send.call_args[0][0]
    assert "balance check failed" in text
    assert "KX-NHL-2" in text


def test_limit_ttl_order_alerts_on_insufficient_balance():
    om, notifier = _make_manager(balance_value=10.79)
    res = _run(om._live_limit_ttl_order(
        ticker="KX-NBA-1", side="yes", size_usdc=20.0, price=0.28,
        strategy="sports_underdog_v31", market_id="KX-NBA-1",
    ))
    assert res["status"] == "failed"
    assert res["reason"] == "insufficient_balance"
    asyncio.run(asyncio.sleep(0))
    notifier.send.assert_called_once()
    text = notifier.send.call_args[0][0]
    assert "insufficient balance" in text
    assert "KX-NBA-1" in text


def test_no_alert_when_notifier_is_none():
    config = MagicMock()
    config.INITIAL_BANKROLL = 1000.0
    config.is_paper_mode = True
    config.LIVE_STRATEGIES = {"sports_underdog_v31"}
    config.estimate_fee = MagicMock(return_value=0.5)
    client = MagicMock()
    client.get_balance = MagicMock(return_value=10.0)
    om = OrderManager(config, client, MagicMock(), notifier=None)
    # Should not raise even though notifier is None
    res = _run(om._live_market_order(
        ticker="KX-X", side="yes", size_usdc=20.0, price=0.4,
        strategy="sports_underdog_v31", market_id="KX-X",
    ))
    assert res["status"] == "failed"
