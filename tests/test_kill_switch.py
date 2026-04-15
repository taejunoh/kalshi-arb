"""Kill-switch file at /tmp/kalshi_stop blocks new entries."""
import os
import tempfile
from unittest.mock import MagicMock
import pytest

from core.order_manager import OrderManager


@pytest.fixture
def order_manager():
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.MAX_DAILY_LOSS = 200.0
    config.MAX_DAILY_LOSS_LIVE = 200.0
    config.MAX_CONSECUTIVE_LOSSES = 1000
    config.LIVE_STRATEGIES = set()
    om = OrderManager(config, kalshi_client=MagicMock(), database=None, notifier=None)
    yield om
    # teardown: ensure kill file removed even if test forgot
    try:
        os.remove("/tmp/kalshi_stop")
    except FileNotFoundError:
        pass


def test_kill_switch_absent_returns_false(order_manager):
    # File should not exist at test start (fixture cleans)
    assert order_manager.is_kill_switch_active() is False


def test_kill_switch_present_returns_true(order_manager):
    with open("/tmp/kalshi_stop", "w") as f:
        f.write("")
    try:
        assert order_manager.is_kill_switch_active() is True
    finally:
        os.remove("/tmp/kalshi_stop")


def test_circuit_breaker_trips_when_kill_file_present(order_manager):
    with open("/tmp/kalshi_stop", "w") as f:
        f.write("")
    try:
        assert order_manager.is_circuit_breaker_active() is True
        assert order_manager.is_circuit_breaker_active(strategy="sports_longshot_v31") is True
    finally:
        os.remove("/tmp/kalshi_stop")


def test_removing_kill_file_resumes(order_manager):
    path = "/tmp/kalshi_stop"
    with open(path, "w") as f:
        f.write("")
    assert order_manager.is_circuit_breaker_active() is True
    os.remove(path)
    assert order_manager.is_circuit_breaker_active() is False


def test_custom_kill_file_path(monkeypatch, tmp_path):
    """KILL_SWITCH_FILE env override allows tests / alt deployments."""
    custom_path = str(tmp_path / "stopfile")
    monkeypatch.setenv("KILL_SWITCH_FILE", custom_path)
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.MAX_DAILY_LOSS = 200.0
    config.MAX_DAILY_LOSS_LIVE = 200.0
    config.MAX_CONSECUTIVE_LOSSES = 1000
    config.LIVE_STRATEGIES = set()
    om = OrderManager(config, kalshi_client=MagicMock(), database=None, notifier=None)
    with open(custom_path, "w") as f:
        f.write("")
    assert om.is_kill_switch_active() is True
