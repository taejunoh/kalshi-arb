"""Paper / Live daily loss caps are independent."""
from unittest.mock import MagicMock
from core.order_manager import OrderManager


def _make(live_strategies=None):
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.MAX_DAILY_LOSS = 200.0  # paper
    config.MAX_DAILY_LOSS_LIVE = 200.0
    config.MAX_CONSECUTIVE_LOSSES = 1000  # effectively off for these tests
    config.LIVE_STRATEGIES = live_strategies or set()
    om = OrderManager(config, kalshi_client=MagicMock(), database=None, notifier=None)
    return om


def test_paper_loss_does_not_trip_live_cap():
    om = _make(live_strategies={"sports_longshot_v31"})
    om.record_loss(amount=250.0, strategy="sports_longshot")  # paper
    assert om.is_circuit_breaker_active(strategy="sports_longshot") is True
    assert om.is_circuit_breaker_active(strategy="sports_longshot_v31") is False


def test_live_loss_does_not_trip_paper_cap():
    om = _make(live_strategies={"sports_longshot_v31"})
    om.record_loss(amount=250.0, strategy="sports_longshot_v31")  # live
    assert om.is_circuit_breaker_active(strategy="sports_longshot_v31") is True
    assert om.is_circuit_breaker_active(strategy="sports_longshot") is False


def test_no_strategy_arg_returns_true_if_either_tripped():
    om = _make(live_strategies={"sports_longshot_v31"})
    om.record_loss(amount=250.0, strategy="sports_longshot_v31")  # live
    assert om.is_circuit_breaker_active() is True  # live cap breached


def test_reset_daily_clears_both_buckets():
    om = _make(live_strategies={"sports_longshot_v31"})
    om.record_loss(amount=250.0, strategy="sports_longshot")
    om.record_loss(amount=250.0, strategy="sports_longshot_v31")
    om.reset_daily()
    assert om.is_circuit_breaker_active() is False


def test_legacy_record_loss_without_strategy_goes_to_paper():
    """Backward compat: old callers passing no strategy hit paper bucket."""
    om = _make(live_strategies={"sports_longshot_v31"})
    om.record_loss(amount=250.0)  # no strategy arg
    # paper cap tripped, live untouched
    assert om.is_circuit_breaker_active(strategy="sports_longshot") is True
    assert om.is_circuit_breaker_active(strategy="sports_longshot_v31") is False
