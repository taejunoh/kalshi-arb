import pytest
from strategies.sum_arb import SumArbStrategy
from core.config import Config


def test_detect_opportunity():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.45, no_price=0.48, yes_depth=100.0, no_depth=100.0)
    assert signal is not None
    assert signal["gap"] == pytest.approx(0.07, abs=0.001)
    assert signal["net_edge"] > 0


def test_no_opportunity_when_sum_too_high():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.50, no_price=0.49, yes_depth=100.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_low_liquidity():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.45, no_price=0.48, yes_depth=30.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_fees_eat_edge():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.49, no_price=0.48, yes_depth=100.0, no_depth=100.0)
    assert signal is None
