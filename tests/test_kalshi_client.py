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
    # Should have called _get once per crypto prefix
    # (KXBTC, KXBTCD, KXETH, KXETHD, KXSOL, KXXRP)
    assert client._http.get.call_count == 6
    # Each call returns 1 market, so total = 6
    assert len(markets) == 6


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
    # 2026-07-01 v1→v2 migration: response is the v2 body returned as-is
    # (order_id at top level, no 'order' wrapper), sent as a fill_or_kill
    # limit to the v2 single-book endpoint with side=bid/ask.
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order_id": "ord_123", "status": "executed"},
        raise_for_status=lambda: None,
    )
    resp = client.place_market_order("KXBTC-25MAR20-T70000", "yes", 10,
                                      yes_price_cents=35, buy_max_cost_cents=35)
    assert resp["order_id"] == "ord_123"
    args, kwargs = client._http.post.call_args
    assert args[0] == "/trade-api/v2/portfolio/events/orders"
    payload = kwargs["json"]
    assert payload["side"] == "bid"  # yes → bid
    assert payload["price"] == "0.3500"  # cents → fixed-point dollars
    assert payload["count"] == "10"
    assert payload["time_in_force"] == "fill_or_kill"


def test_place_limit_order():
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order_id": "ord_456", "status": "resting"},
        raise_for_status=lambda: None,
    )
    resp = client.place_limit_order("KXBTC-25MAR20-T70000", "yes", 48, 5)
    assert resp["order_id"] == "ord_456"
    args, kwargs = client._http.post.call_args
    assert args[0] == "/trade-api/v2/portfolio/events/orders"
    payload = kwargs["json"]
    assert payload["side"] == "bid"  # yes → bid
    assert payload["price"] == "0.4800"
    assert payload["count"] == "5"
    assert payload["time_in_force"] == "good_till_canceled"


def test_place_limit_order_no_side_maps_to_ask():
    # side='no' → ask on the YES book; price_cents arrives already inverted
    # to yes-terms by the caller, so it passes through unchanged.
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order_id": "ord_789", "status": "resting"},
        raise_for_status=lambda: None,
    )
    resp = client.place_limit_order("KXBTC-25MAR20-T70000", "no", 52, 5)
    assert resp["order_id"] == "ord_789"
    payload = client._http.post.call_args.kwargs["json"]
    assert payload["side"] == "ask"
    assert payload["price"] == "0.5200"


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


def test_rate_limit_serializes_concurrent_threads():
    """Concurrent threads through _rate_limit must NOT bypass the throttle.

    Pre-fix (no lock), N threads could read _last_request_time before any
    write, all sleep the same delta, and fire in the same millisecond
    — bursting past Kalshi's 429 limit. Post-fix the lock serializes
    them so N threads take ≥ (N-1) × _min_request_interval total.
    """
    import threading
    import time

    client = _make_client()
    # Shrink interval to keep test fast but still measurable
    client._min_request_interval = 0.05
    # Force first call to skip sleep
    client._last_request_time = 0.0

    fire_times: list[float] = []
    lock = threading.Lock()

    def worker():
        client._rate_limit()
        with lock:
            fire_times.append(time.time())

    threads = [threading.Thread(target=worker) for _ in range(10)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    # 10 threads × 0.05s interval, minus the first that doesn't sleep
    # → expect ≥ 9 × 0.05 = 0.45s. Allow generous wall-clock slack
    # (CI / Python sleep accuracy) but require materially more than
    # 0.05s (which is what the pre-fix race-condition outcome would
    # produce).
    assert elapsed >= 0.35, (
        f"_rate_limit not serializing threads — 10 calls took {elapsed:.3f}s, "
        f"expected ≥ 0.35s (race condition would yield <0.1s)"
    )

    # Adjacent fire times should respect the interval (within timer skew).
    fire_times.sort()
    deltas = [fire_times[i+1] - fire_times[i] for i in range(len(fire_times) - 1)]
    # At least 80% of adjacent deltas must be ≥ 0.04s (interval minus skew)
    ok = sum(1 for d in deltas if d >= 0.04)
    assert ok >= int(0.8 * len(deltas)), (
        f"adjacent deltas violated interval too often: deltas={deltas}"
    )
