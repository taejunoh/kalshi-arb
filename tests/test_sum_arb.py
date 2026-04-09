import pytest
from unittest.mock import MagicMock
from strategies.sum_arb import SumArbStrategy
from core.config import Config


def _make_strategy():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    cfg.MAX_SINGLE_TRADE_PCT = 0.10
    order_mgr = MagicMock()
    order_mgr.bankroll = 200.0
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    strategy._order_manager = order_mgr
    return strategy


def test_detect_opportunity():
    strategy = _make_strategy()
    signal = strategy.evaluate(yes_price=0.40, no_price=0.40, yes_depth=100.0, no_depth=100.0)
    assert signal is not None
    assert signal["gap"] == pytest.approx(0.20, abs=0.001)
    assert signal["net_edge"] > 0


def test_no_opportunity_when_sum_too_high():
    strategy = _make_strategy()
    signal = strategy.evaluate(yes_price=0.50, no_price=0.49, yes_depth=100.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_low_liquidity():
    strategy = _make_strategy()
    signal = strategy.evaluate(yes_price=0.40, no_price=0.40, yes_depth=30.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_fees_eat_edge():
    strategy = _make_strategy()
    signal = strategy.evaluate(yes_price=0.49, no_price=0.49, yes_depth=100.0, no_depth=100.0)
    assert signal is None
