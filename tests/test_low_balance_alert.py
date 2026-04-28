"""Hysteresis low-balance proactive alert.

Fires once when balance crosses BELOW LOW_BALANCE_THRESHOLD. Re-arms
only after balance recovers ABOVE LOW_BALANCE_RECOVERY (preventing
oscillation spam). Pairs with the post-reject alert and the 14:00 +
22:00 ET scheduled checks to close gaps when an evening cluster
drains cash between scheduled checks.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.order_manager import OrderManager


def _make_manager():
    config = MagicMock()
    config.INITIAL_BANKROLL = 1000.0
    config.is_paper_mode = True
    config.LIVE_STRATEGIES = set()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    om = OrderManager(config, MagicMock(), MagicMock(), notifier=notifier)
    return om, notifier


async def _check(om, *balances):
    """Call check_low_balance from inside an event loop so the create_task
    inside fires cleanly and the AsyncMock is awaited (no stray warnings)."""
    for b in balances:
        om.check_low_balance(b)
    # Yield once so any tasks created by check_low_balance get scheduled.
    await asyncio.sleep(0)


def test_alerts_when_balance_first_dips_below_threshold():
    om, notifier = _make_manager()
    asyncio.run(_check(om, 40.0))
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "below" in msg.lower()
    assert "$40.00" in msg
    assert notifier.send.call_args[1]["msg_type"] == "balance_low"


def test_does_not_re_alert_when_still_below_threshold():
    om, notifier = _make_manager()
    asyncio.run(_check(om, 40.0, 30.0, 15.0))
    # Three consecutive low-balance polls should produce ONE alert
    assert notifier.send.call_count == 1


def test_re_arms_after_recovery_above_recovery_line():
    om, notifier = _make_manager()
    asyncio.run(_check(om, 40.0, 110.0, 40.0))
    # alert → recover → alert again
    assert notifier.send.call_count == 2


def test_does_not_re_arm_in_dead_zone():
    """Balance between threshold ($50) and recovery ($100) should not re-arm.
    Prevents oscillation spam when balance hovers around the threshold."""
    om, notifier = _make_manager()
    asyncio.run(_check(om, 40.0, 75.0, 30.0))
    assert notifier.send.call_count == 1


def test_no_alert_when_balance_above_threshold():
    om, notifier = _make_manager()
    asyncio.run(_check(om, 150.0, 200.0))
    notifier.send.assert_not_called()


def test_handles_none_balance_gracefully():
    om, notifier = _make_manager()
    asyncio.run(_check(om, None))
    notifier.send.assert_not_called()
    assert om._low_balance_armed is True


def test_no_alert_when_notifier_is_none():
    config = MagicMock()
    config.INITIAL_BANKROLL = 1000.0
    config.is_paper_mode = True
    config.LIVE_STRATEGIES = set()
    om = OrderManager(config, MagicMock(), MagicMock(), notifier=None)
    asyncio.run(_check(om, 40.0))
    # State updates regardless of notifier presence
    assert om._low_balance_armed is False
