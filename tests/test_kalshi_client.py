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


def test_get_active_markets_queries_per_prefix():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"markets": [
            {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
        ]},
        raise_for_status=lambda: None,
    )
    markets = client.get_active_markets()
    # Should have called _get once per crypto prefix (KXBTC, KXETH, KXSOL, KXXRP)
    assert client._http.get.call_count == 4
    # Each call returns 1 market, so total = 4
    assert len(markets) == 4


def test_get_market_prices():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"market": {"yes_ask_dollars": "0.4500", "no_ask_dollars": "0.4800"}},
        raise_for_status=lambda: None,
    )
    yes_price, no_price = client.get_market_prices("KXBTC-25MAR20-T70000")
    assert yes_price == pytest.approx(0.45)
    assert no_price == pytest.approx(0.48)


def test_get_order_book_depth():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"orderbook_fp": {
            "yes_dollars": [["0.45", "100"], ["0.44", "200"]],
            "no_dollars": [["0.48", "150"], ["0.47", "50"]],
        }},
        raise_for_status=lambda: None,
    )
    yes_depth, no_depth = client.get_order_book_depth("KXBTC-25MAR20-T70000")
    # yes: 0.45*100 + 0.44*200 = 45 + 88 = 133
    # no: 0.48*150 + 0.47*50 = 72 + 23.5 = 95.5
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
    resp = client.place_market_order("KXBTC-25MAR20-T70000", "yes", 10,
                                      yes_price_cents=35, buy_max_cost_cents=35)
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
