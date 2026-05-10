import os
from unittest.mock import patch
from core.config import Config

MACRO_KEYS = (
    "MACRO_EVENT_MIN_DEVIATION", "MACRO_EVENT_MIN_DEPTH_USDC",
    "MACRO_EVENT_BASE_SIZE_USDC", "MACRO_EVENT_MAX_POSITION_PER_TICKER",
    "MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE", "MACRO_EVENT_WINDOW_MAX_HOURS",
    "MACRO_EVENT_WINDOW_MIN_MINUTES", "MACRO_CONSENSUS_STALE_HOURS",
)

def test_macro_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in MACRO_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.MACRO_EVENT_MIN_DEVIATION == 0.10
        assert cfg.MACRO_EVENT_MIN_DEPTH_USDC == 20.0
        assert cfg.MACRO_EVENT_BASE_SIZE_USDC == 20.0
        assert cfg.MACRO_EVENT_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.MACRO_EVENT_WINDOW_MAX_HOURS == 336.0
        assert cfg.MACRO_EVENT_WINDOW_MIN_MINUTES == 30.0
        assert cfg.MACRO_CONSENSUS_STALE_HOURS == 24.0

def test_macro_config_env_override():
    with patch.dict(os.environ, {
        "MACRO_EVENT_MIN_DEVIATION": "0.15",
        "MACRO_EVENT_BASE_SIZE_USDC": "25",
    }):
        cfg = Config()
        assert cfg.MACRO_EVENT_MIN_DEVIATION == 0.15
        assert cfg.MACRO_EVENT_BASE_SIZE_USDC == 25.0
