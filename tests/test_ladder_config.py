import os
from unittest.mock import patch

from core.config import Config


def test_ladder_config_defaults():
    with patch.dict(os.environ, {}, clear=False):
        for key in ("LADDER_MIN_ARB_EDGE_USDC", "LADDER_MIN_FADE_INVERSION",
                    "LADDER_MIN_DEPTH_USDC", "LADDER_MAX_POSITION_PER_GROUP",
                    "LADDER_STALE_ORDERBOOK_SEC"):
            os.environ.pop(key, None)
        cfg = Config()
        assert cfg.LADDER_MIN_ARB_EDGE_USDC == 0.02
        assert cfg.LADDER_MIN_FADE_INVERSION == 0.05
        assert cfg.LADDER_MIN_DEPTH_USDC == 30.0
        assert cfg.LADDER_MAX_POSITION_PER_GROUP == 50.0
        assert cfg.LADDER_STALE_ORDERBOOK_SEC == 10.0


def test_ladder_config_env_override():
    with patch.dict(os.environ, {"LADDER_MIN_ARB_EDGE_USDC": "0.05",
                                  "LADDER_MAX_POSITION_PER_GROUP": "75"}):
        cfg = Config()
        assert cfg.LADDER_MIN_ARB_EDGE_USDC == 0.05
        assert cfg.LADDER_MAX_POSITION_PER_GROUP == 75.0
