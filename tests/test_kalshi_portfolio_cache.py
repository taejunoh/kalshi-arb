"""TTL cache for Kalshi portfolio endpoints (positions, settlements).

The dashboard HTML auto-refreshes every 15s, hitting /api/performance which
calls get_positions + get_settlements each time. Multiple browser tabs or
quick reloads generate 429 Too Many Requests from Kalshi. Cache at the
client level so one call serves many dashboard ticks.
"""
from unittest.mock import MagicMock, patch

from core.kalshi_client import KalshiClient, _portfolio_cache_reset


def _make_client() -> KalshiClient:
    config = MagicMock()
    config.KALSHI_API_BASE_URL = "https://fake"
    config.KALSHI_API_KEY_ID = ""
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    _portfolio_cache_reset()
    return KalshiClient(config)


def test_get_positions_caches_within_ttl():
    c = _make_client()
    calls = {"n": 0}
    def fake_get(url, *_a, **_k):
        calls["n"] += 1
        return {"market_positions": [{"ticker": "X"}]}
    with patch.object(c, "_get", side_effect=fake_get), \
         patch("core.kalshi_client._portfolio_cache_now", return_value=1000.0):
        r1 = c.get_positions()
        r2 = c.get_positions()
    assert r1 == r2 == [{"ticker": "X"}]
    assert calls["n"] == 1  # second call hit cache, not HTTP


def test_get_positions_refetches_after_ttl():
    c = _make_client()
    calls = {"n": 0}
    def fake_get(url, *_a, **_k):
        calls["n"] += 1
        return {"market_positions": []}
    with patch.object(c, "_get", side_effect=fake_get), \
         patch("core.kalshi_client._portfolio_cache_now") as now:
        now.return_value = 1000.0
        c.get_positions()
        now.return_value = 1050.0  # TTL is 30s; 50s later → expired
        c.get_positions()
    assert calls["n"] == 2


def test_get_settlements_caches_independently():
    """positions and settlements share the TTL window but are separate keys."""
    c = _make_client()
    calls = {"positions": 0, "settlements": 0}
    def fake_get(url, *_a, **_k):
        if "positions" in url:
            calls["positions"] += 1
            return {"market_positions": []}
        else:
            calls["settlements"] += 1
            return {"settlements": []}  # empty list → while True loop ends after 1 call
    with patch.object(c, "_get", side_effect=fake_get), \
         patch("core.kalshi_client._portfolio_cache_now", return_value=1000.0):
        c.get_positions()
        c.get_positions()
        c.get_settlements()
        c.get_settlements()
    assert calls["positions"] == 1
    assert calls["settlements"] == 1


def test_cache_returns_fresh_copy_not_mutable_reference():
    """If caller mutates the returned list, the cached copy should stay clean."""
    c = _make_client()
    def fake_get(url, *_a, **_k):
        return {"market_positions": [{"ticker": "X"}]}
    with patch.object(c, "_get", side_effect=fake_get), \
         patch("core.kalshi_client._portfolio_cache_now", return_value=1000.0):
        r1 = c.get_positions()
        r1.append({"ticker": "MUTATED"})
        r2 = c.get_positions()
    assert len(r2) == 1
    assert r2[0]["ticker"] == "X"
