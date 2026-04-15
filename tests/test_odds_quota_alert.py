"""Odds API quota alert thresholds."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from core.order_manager import OrderManager
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy


def _make_strategy():
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    config.ODDS_API_KEY = "k"
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    client = MagicMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    om = OrderManager(config, kalshi_client=client, database=None, notifier=notifier)
    om._notifier = notifier
    strat = SportsLongshotV31Strategy(config, om, client)
    return strat, om, notifier


@pytest.mark.asyncio
async def test_high_quota_no_halt_no_warn():
    strat, om, notifier = _make_strategy()
    await strat._check_quota(12000)
    assert om._quota_exhausted is False
    notifier.send.assert_not_called()


@pytest.mark.asyncio
async def test_low_quota_warns_but_no_halt():
    strat, om, notifier = _make_strategy()
    await strat._check_quota(3000)
    assert om._quota_exhausted is False
    notifier.send.assert_called_once()
    args, kwargs = notifier.send.call_args
    assert "quota_warn" in kwargs.get("msg_type", args[1] if len(args) > 1 else "")


@pytest.mark.asyncio
async def test_critical_quota_halts_and_alerts():
    strat, om, notifier = _make_strategy()
    await strat._check_quota(500)
    assert om._quota_exhausted is True
    notifier.send.assert_called_once()
    args, kwargs = notifier.send.call_args
    assert kwargs.get("msg_type", args[1] if len(args) > 1 else "") == "quota_halt"


@pytest.mark.asyncio
async def test_recovery_clears_halt_flag():
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    await strat._check_quota(12000)
    assert om._quota_exhausted is False


def test_circuit_breaker_blocks_sports_when_quota_exhausted():
    strat, om, _ = _make_strategy()
    om._quota_exhausted = True
    assert om.is_circuit_breaker_active(strategy="sports_longshot_v31") is True
    # Non-sports strategy unaffected
    assert om.is_circuit_breaker_active(strategy="mentions_trader") is False


def test_no_strategy_arg_unaffected_by_quota_flag():
    """Default (strategy=None) path — quota only gates sports_* callers."""
    strat, om, _ = _make_strategy()
    om._quota_exhausted = True
    # No strategy arg: quota flag doesn't apply (conservative either-breached
    # logic still runs but daily caps untouched → False)
    assert om.is_circuit_breaker_active() is False
