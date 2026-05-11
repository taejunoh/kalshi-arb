import os
from unittest.mock import patch

from core.config import Config


XVENUE_KEYS = (
    "XVENUE_MIN_DEVIATION", "XVENUE_MIN_DEPTH_USDC",
    "XVENUE_BASE_SIZE_USDC", "XVENUE_MAX_POSITION_PER_TICKER",
    "XVENUE_MAX_TICKER_TOTAL_EXPOSURE", "XVENUE_QUOTE_STALE_MINS",
    "XVENUE_MIN_POLYMARKET_VOLUME", "XVENUE_RECENTLY_FIRED_SECS",
)


def test_xvenue_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in XVENUE_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.XVENUE_MIN_DEVIATION == 0.10
        assert cfg.XVENUE_MIN_DEPTH_USDC == 30.0
        assert cfg.XVENUE_BASE_SIZE_USDC == 20.0
        assert cfg.XVENUE_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.XVENUE_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.XVENUE_QUOTE_STALE_MINS == 15.0
        assert cfg.XVENUE_MIN_POLYMARKET_VOLUME == 5000.0
        assert cfg.XVENUE_RECENTLY_FIRED_SECS == 300.0


def test_xvenue_config_env_override():
    with patch.dict(os.environ, {
        "XVENUE_MIN_DEVIATION": "0.15",
        "XVENUE_MIN_POLYMARKET_VOLUME": "10000",
    }):
        cfg = Config()
        assert cfg.XVENUE_MIN_DEVIATION == 0.15
        assert cfg.XVENUE_MIN_POLYMARKET_VOLUME == 10000.0
