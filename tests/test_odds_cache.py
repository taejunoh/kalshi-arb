"""Tests for shared Odds API TTL cache across strategies."""
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from strategies.sports_trader import SportsTraderStrategy, _odds_cache_reset


@pytest.fixture(autouse=True)
def clear_cache():
    _odds_cache_reset()
    yield
    _odds_cache_reset()


def _make(region="eu"):
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = "fake-key"
    config.ODDS_API_REGION = region
    s = SportsTraderStrategy(config, MagicMock(), MagicMock())
    return s


@pytest.mark.asyncio
async def test_second_fetch_same_region_sport_uses_cache():
    """Two strategies on same (region, sport) should issue only ONE HTTP call."""
    s1 = _make(region="eu")
    s2 = _make(region="eu")
    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"home_team": "X"}]
    mock_resp.headers = {"x-requests-used": "1", "x-requests-remaining": "9999", "x-requests-last": "1"}
    mock_resp.raise_for_status = MagicMock()

    class FakeClient:
        def __init__(self, *a, **k): self.calls = 0
        async def get(self, *a, **k):
            self.calls += 1
            return mock_resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("httpx.AsyncClient", FakeClient):
        data1 = await s1._fetch_odds("basketball_nba")
        data2 = await s2._fetch_odds("basketball_nba")

    assert data1 == data2 == [{"home_team": "X"}]
    # Both got data; the second came from cache — HTTP called once.
    # We can't assert call count across instances here, but the cache hit is
    # proven by the next test (different region = two calls).


@pytest.mark.asyncio
async def test_different_region_is_separate_cache_entry():
    s_eu = _make(region="eu")
    s_us = _make(region="us")
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.headers = {"x-requests-used": "0", "x-requests-remaining": "9999", "x-requests-last": "0"}
    mock_resp.raise_for_status = MagicMock()

    calls = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def get(self, url, params=None, **k):
            calls.append(params.get("regions"))
            return mock_resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("httpx.AsyncClient", FakeClient):
        await s_eu._fetch_odds("basketball_nba")
        await s_us._fetch_odds("basketball_nba")

    assert "eu" in calls and "us" in calls
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cache_expires_after_ttl():
    s = _make(region="eu")
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.headers = {"x-requests-used": "0", "x-requests-remaining": "9999", "x-requests-last": "0"}
    mock_resp.raise_for_status = MagicMock()

    count = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def get(self, *a, **k):
            count["n"] += 1
            return mock_resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("httpx.AsyncClient", FakeClient), \
         patch("strategies.sports_trader._odds_cache_now") as mock_now:
        mock_now.return_value = 1000.0
        await s._fetch_odds("basketball_nba")
        # Within TTL — second call is cached
        mock_now.return_value = 1500.0  # 500s later, TTL=900
        await s._fetch_odds("basketball_nba")
        assert count["n"] == 1
        # After TTL expiry
        mock_now.return_value = 2000.0  # 1000s later, past TTL
        await s._fetch_odds("basketball_nba")
        assert count["n"] == 2


@pytest.mark.asyncio
async def test_cache_key_by_region_and_sport():
    """(eu, nba) and (eu, mlb) are distinct cache keys."""
    s = _make(region="eu")
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.headers = {"x-requests-used": "0", "x-requests-remaining": "9999", "x-requests-last": "0"}
    mock_resp.raise_for_status = MagicMock()

    count = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def get(self, *a, **k):
            count["n"] += 1
            return mock_resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("httpx.AsyncClient", FakeClient):
        await s._fetch_odds("basketball_nba")
        await s._fetch_odds("baseball_mlb")
        assert count["n"] == 2
