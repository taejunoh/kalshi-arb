import pytest
from datetime import datetime, timezone, timedelta
from strategies.maker_preorder import MakerPreorderStrategy
from core.config import Config


def test_calculate_order_prices():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    yes_price, no_price = strategy.calculate_order_prices()
    assert yes_price + no_price == pytest.approx(0.96)
    assert yes_price == pytest.approx(0.48)
    assert no_price == pytest.approx(0.48)


def test_expected_profit():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    assert profit == pytest.approx(0.04)


def test_is_short_duration_market():
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    close_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time}) is True

    close_time_far = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time_far}) is False

    close_time_past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time_past}) is False


def test_should_not_trade_if_profit_negative():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 1.01
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    assert profit < 0
