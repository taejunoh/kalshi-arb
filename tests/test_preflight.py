import pytest
from unittest.mock import MagicMock, patch
from core.polymarket_client import PolymarketClient
from core.config import Config


def test_preflight_passes_with_valid_connection():
    config = Config()
    client = PolymarketClient(config)
    mock_clob = MagicMock()
    mock_clob.get_markets.return_value = [{"conditionId": "1"}]
    client._clob = mock_clob
    assert client.preflight_check() is True


def test_preflight_fails_without_clob():
    config = Config()
    client = PolymarketClient(config)
    client._clob = None
    # Override _init_clob to do nothing (simulate failed init)
    client._init_clob = lambda: None
    assert client.preflight_check() is False


def test_preflight_fails_on_api_error():
    config = Config()
    client = PolymarketClient(config)
    mock_clob = MagicMock()
    mock_clob.get_markets.side_effect = Exception("API error")
    client._clob = mock_clob
    assert client.preflight_check() is False
