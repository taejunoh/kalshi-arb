import os
from unittest.mock import patch

from core.config import Config


CRYPTO_KEYS = (
    "CRYPTO_FAIR_MIN_DEVIATION", "CRYPTO_FAIR_MIN_DEPTH_USDC",
    "CRYPTO_FAIR_BASE_SIZE_USDC", "CRYPTO_FAIR_MAX_POSITION_PER_TICKER",
    "CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE", "CRYPTO_FAIR_RECENTLY_FIRED_SECS",
    "CRYPTO_FAIR_ATM_WINDOW_N", "CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS",
)


def test_crypto_fair_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for k in CRYPTO_KEYS:
            os.environ.pop(k, None)
        cfg = Config()
        assert cfg.CRYPTO_FAIR_MIN_DEVIATION == 0.10
        assert cfg.CRYPTO_FAIR_MIN_DEPTH_USDC == 30.0
        assert cfg.CRYPTO_FAIR_BASE_SIZE_USDC == 20.0
        assert cfg.CRYPTO_FAIR_MAX_POSITION_PER_TICKER == 30.0
        assert cfg.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE == 80.0
        assert cfg.CRYPTO_FAIR_RECENTLY_FIRED_SECS == 60.0
        assert cfg.CRYPTO_FAIR_ATM_WINDOW_N == 5
        assert cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS == 60.0


def test_xrp_in_binance_ws_urls():
    cfg = Config()
    assert "XRP" in cfg.BINANCE_WS_URLS
    assert "xrpusdt" in cfg.BINANCE_WS_URLS["XRP"].lower()
