import os
import pytest


def test_config_loads_defaults():
    for key in ["TRADING_MODE", "INITIAL_BANKROLL", "SUM_ARB_MIN_GAP"]:
        os.environ.pop(key, None)

    from core.config import Config
    cfg = Config()

    assert cfg.TRADING_MODE == "paper"
    assert cfg.INITIAL_BANKROLL == 100.0
    assert cfg.SUM_ARB_MIN_GAP == 0.03
    assert cfg.MAKER_TARGET_SUM == 0.96
    assert cfg.KELLY_FRACTION == 0.25
    assert cfg.MAX_SINGLE_TRADE_PCT == 0.10
    assert cfg.MAX_DIRECTIONAL_LOSS == 50.0
    assert cfg.MAX_DAILY_LOSS == 200.0
    assert cfg.MAX_CONSECUTIVE_LOSSES == 5


def test_config_is_paper_mode():
    from core.config import Config
    cfg = Config()
    assert cfg.is_paper_mode is True


def test_config_env_override():
    os.environ["TRADING_MODE"] = "live"
    os.environ["INITIAL_BANKROLL"] = "500"
    from core.config import Config
    cfg = Config()
    assert cfg.TRADING_MODE == "live"
    assert cfg.INITIAL_BANKROLL == 500.0
    assert cfg.is_paper_mode is False
    os.environ.pop("TRADING_MODE", None)
    os.environ.pop("INITIAL_BANKROLL", None)


def test_kalshi_fee_calculation():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee(10, 50)
    assert fee == pytest.approx(0.70, abs=0.01)

def test_kalshi_fee_capped():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee(1, 5)
    assert fee == pytest.approx(0.05, abs=0.01)

def test_kalshi_fee_per_contract():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee_per_contract(50)
    assert fee == pytest.approx(0.07, abs=0.01)

def test_kalshi_fee_per_contract_capped():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee_per_contract(3)
    assert fee == pytest.approx(0.03, abs=0.01)
