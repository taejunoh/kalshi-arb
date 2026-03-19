import pytest
from strategies.maker_preorder import MakerPreorderStrategy
from core.config import Config


@pytest.fixture
def config():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    cfg.MAX_DIRECTIONAL_LOSS = 50.0
    return cfg


def test_calculate_order_prices():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    up_price, down_price = strategy.calculate_order_prices()
    assert up_price + down_price == pytest.approx(0.96)
    assert up_price == pytest.approx(0.48)
    assert down_price == pytest.approx(0.48)


def test_expected_profit():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    # Pay $0.96 total, receive $1.00 at resolution = $0.04 profit
    assert profit == pytest.approx(0.04)


def test_is_5min_market():
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    assert strategy._is_5min_market({"question": "Will BTC go up in the next 5 minutes?"}) is True
    assert strategy._is_5min_market({"question": "Will BTC go up in the next 15 minutes?"}) is False
    assert strategy._is_5min_market({"question": "Will ETH go up in the next 5-minute window?"}) is True


def test_should_not_trade_if_profit_negative():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 1.01  # sum > 1.0, no profit possible
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    assert profit < 0
