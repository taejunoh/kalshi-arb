import pytest
from unittest.mock import MagicMock
from core.kalshi_client import KalshiClient


def test_preflight_passes_with_valid_connection():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 50000},
        raise_for_status=lambda: None,
    )
    client._http = mock_http
    client._private_key = MagicMock()
    assert client.preflight_check() is True


def test_preflight_fails_without_http():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = ""
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    client._http = None
    client._init_http = lambda: None
    assert client.preflight_check() is False


def test_preflight_fails_on_api_error():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    mock_http = MagicMock()
    mock_http.get.side_effect = Exception("API error")
    client._http = mock_http
    client._private_key = MagicMock()
    assert client.preflight_check() is False
