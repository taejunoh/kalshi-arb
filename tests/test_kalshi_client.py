import pytest
from unittest.mock import MagicMock, patch, mock_open
from core.kalshi_client import KalshiClient, CRYPTO_SERIES_PREFIXES


def _make_client():
    """Create a KalshiClient with mocked HTTP."""
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test_key"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    client._http = MagicMock()
    client._private_key = None  # skip auth in tests
    return client


def test_filter_crypto_markets():
    client = _make_client()
    markets = [
        {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
        {"ticker": "TRUMP-APPROVAL", "status": "open"},
        {"ticker": "KXETH-25MAR20-T4000", "status": "open"},
    ]
    filtered = client._filter_crypto_markets(markets)
    assert len(filtered) == 2
    assert filtered[0]["ticker"] == "KXBTC-25MAR20-T70000"
    assert filtered[1]["ticker"] == "KXETH-25MAR20-T4000"


def test_get_market_prices():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"market": {"yes_ask": 45, "no_ask": 48}},
        raise_for_status=lambda: None,
    )
    yes_price, no_price = client.get_market_prices("KXBTC-25MAR20-T70000")
    assert yes_price == pytest.approx(0.45)
    assert no_price == pytest.approx(0.48)


def test_get_order_book_depth():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"orderbook": {
            "yes": [[45, 100], [44, 200]],
            "no": [[48, 150], [47, 50]],
        }},
        raise_for_status=lambda: None,
    )
    yes_depth, no_depth = client.get_order_book_depth("KXBTC-25MAR20-T70000")
    assert yes_depth == pytest.approx(133.0, abs=1.0)
    assert no_depth == pytest.approx(95.5, abs=1.0)


def test_preflight_check_success():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 50000},
        raise_for_status=lambda: None,
    )
    assert client.preflight_check() is True


def test_preflight_check_failure():
    client = _make_client()
    client._http.get.side_effect = Exception("Connection error")
    assert client.preflight_check() is False


def test_place_market_order():
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order": {"id": "ord_123", "status": "executed"}},
        raise_for_status=lambda: None,
    )
    resp = client.place_market_order("KXBTC-25MAR20-T70000", "yes", 10)
    assert resp["id"] == "ord_123"


def test_place_limit_order():
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order": {"id": "ord_456", "status": "resting"}},
        raise_for_status=lambda: None,
    )
    resp = client.place_limit_order("KXBTC-25MAR20-T70000", "yes", 48, 5)
    assert resp["id"] == "ord_456"


def test_cancel_order():
    client = _make_client()
    client._http.delete.return_value = MagicMock(
        status_code=200,
        json=lambda: {},
        raise_for_status=lambda: None,
    )
    assert client.cancel_order("ord_123") is True


def test_cancel_order_failure():
    client = _make_client()
    client._http.delete.side_effect = Exception("Not found")
    assert client.cancel_order("ord_123") is False


def test_get_balance():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 50000},
        raise_for_status=lambda: None,
    )
    balance = client.get_balance()
    assert balance == pytest.approx(500.0)
