"""Odds API quota alert behavior (bucket-based: every 2000 used)."""
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


def _msg_types(notifier):
    return [c.kwargs.get("msg_type") for c in notifier.send.call_args_list]


@pytest.mark.asyncio
async def test_first_fetch_seeds_bucket_without_alerting():
    """First fetch after boot seeds the tracker to current bucket — no alert.
    This prevents restart spam: the last known bucket was already alerted by
    the previous process (or not, but either way the user doesn't need a
    'you're here now' message on every restart)."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(3935)  # used=16065, bucket 8
    assert om._quota_last_alert_bucket == 8
    notifier.send.assert_not_called()


@pytest.mark.asyncio
async def test_bucket_crossing_fires_alert_after_seed():
    """After seed, crossing to the next bucket fires one alert."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(3935)  # seed at bucket 8
    await strat._check_quota(1900)  # used=18100, bucket 9
    assert om._quota_last_alert_bucket == 9
    types = _msg_types(notifier)
    assert "quota_bucket_9" in types


@pytest.mark.asyncio
async def test_same_bucket_no_duplicate():
    """Repeated calls within the same bucket don't re-alert."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(19500)  # seed at bucket 0
    await strat._check_quota(18000)  # bucket 1 — alert
    await strat._check_quota(17500)  # still bucket 1
    await strat._check_quota(16500)  # still bucket 1
    assert sum(1 for t in _msg_types(notifier) if t and t.startswith("quota_bucket_")) == 1


@pytest.mark.asyncio
async def test_each_new_bucket_alerts_once():
    """Every 2000 used triggers exactly one alert (after seed)."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(19500)  # seed at bucket 0
    for remaining in [18000, 16000, 14000, 12000, 10000]:  # buckets 1..5
        await strat._check_quota(remaining)
    bucket_alerts = [t for t in _msg_types(notifier) if t and t.startswith("quota_bucket_")]
    assert bucket_alerts == [f"quota_bucket_{i}" for i in range(1, 6)]


@pytest.mark.asyncio
async def test_monthly_reset_resets_bucket_tracker():
    """If `used` drops below the last-alerted bucket floor (quota reset),
    re-seed to current bucket without alerting."""
    strat, om, notifier = _make_strategy()
    await strat._check_quota(19500)  # seed at bucket 0
    await strat._check_quota(4000)  # used=16000, bucket 8 — alert
    assert om._quota_last_alert_bucket == 8
    # Month rolls over → provider resets quota to full
    await strat._check_quota(19500)  # used=500, below bucket 8 floor
    assert om._quota_last_alert_bucket == 0  # re-seeded (silent)
    # From here, crossing bucket 1 should alert again
    await strat._check_quota(18000)
    assert om._quota_last_alert_bucket == 1


@pytest.mark.asyncio
async def test_halt_fires_once_below_5pct():
    strat, om, notifier = _make_strategy()
    await strat._check_quota(500)
    assert om._quota_exhausted is True
    assert "quota_halt" in _msg_types(notifier)
    # Repeated below-halt calls don't re-fire halt
    await strat._check_quota(300)
    assert _msg_types(notifier).count("quota_halt") == 1


@pytest.mark.asyncio
async def test_recovery_clears_halt_and_alerts():
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    await strat._check_quota(12000)  # above WARN (5000)
    assert om._quota_exhausted is False
    assert "quota_recovery" in _msg_types(notifier)


@pytest.mark.asyncio
async def test_middle_band_does_not_clear_halt_flag():
    """Halt flag only clears when remaining >= WARN threshold."""
    strat, om, notifier = _make_strategy()
    om._quota_exhausted = True
    await strat._check_quota(3000)
    assert om._quota_exhausted is True


def test_circuit_breaker_blocks_sports_when_quota_exhausted():
    strat, om, _ = _make_strategy()
    om._quota_exhausted = True
    assert om.is_circuit_breaker_active(strategy="sports_longshot_v31") is True
    assert om.is_circuit_breaker_active(strategy="mentions_trader") is False


def test_no_strategy_arg_unaffected_by_quota_flag():
    strat, om, _ = _make_strategy()
    om._quota_exhausted = True
    assert om.is_circuit_breaker_active() is False
