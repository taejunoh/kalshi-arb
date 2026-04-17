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


@pytest.mark.asyncio
async def test_middle_band_does_not_clear_halt_flag():
    """If previously halted and quota bounces into middle band, flag stays set
    until full recovery above WARN threshold."""
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    await strat._check_quota(3000)  # middle band
    assert om._quota_exhausted is True  # still halted


@pytest.mark.asyncio
async def test_critical_2pct_threshold_sends_extra_alert():
    """<2% (400) triggers a second one-time alert on top of halt."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(300)  # 1.5%, below both halt and critical
    assert om._quota_exhausted is True
    assert om._quota_critical_alerted is True
    msg_types = [c.kwargs.get("msg_type") for c in notifier.send.call_args_list]
    assert "quota_halt" in msg_types
    assert "quota_critical" in msg_types


@pytest.mark.asyncio
async def test_critical_alert_fires_once_per_crossing():
    """Repeated <2% scans don't re-spam the critical alert."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(300)
    await strat._check_quota(200)
    await strat._check_quota(100)
    msg_types = [c.kwargs.get("msg_type") for c in notifier.send.call_args_list]
    assert msg_types.count("quota_critical") == 1
    assert msg_types.count("quota_halt") == 1


@pytest.mark.asyncio
async def test_halt_without_critical_when_between_5pct_and_2pct():
    """500 remaining (2.5%) halts but does NOT trip the 2% critical alert."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(500)
    assert om._quota_exhausted is True
    assert om._quota_critical_alerted is False
    msg_types = [c.kwargs.get("msg_type") for c in notifier.send.call_args_list]
    assert "quota_halt" in msg_types
    assert "quota_critical" not in msg_types


@pytest.mark.asyncio
async def test_recovery_clears_critical_flag():
    """Full recovery clears both halt and critical flags."""
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    om._quota_critical_alerted = True
    await strat._check_quota(12000)
    assert om._quota_exhausted is False
    assert om._quota_critical_alerted is False


@pytest.mark.asyncio
async def test_middle_band_warns_once_per_day():
    """Repeated middle-band scans within the same day must not re-alert.

    Notifier has a 60s rate-limit per msg_type, so dedup must happen on our
    side via _quota_warn_date. Multiple strategies + fetches would otherwise
    spam the user every ~60s once quota drops below WARN."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(3000)
    await strat._check_quota(2500)
    await strat._check_quota(2000)
    assert notifier.send.call_count == 1


@pytest.mark.asyncio
async def test_full_recovery_sends_recovery_alert():
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    await strat._check_quota(12000)
    assert om._quota_exhausted is False
    # Recovery alert fired
    calls = notifier.send.call_args_list
    msg_types = [c.kwargs.get("msg_type") for c in calls]
    assert "quota_recovery" in msg_types
