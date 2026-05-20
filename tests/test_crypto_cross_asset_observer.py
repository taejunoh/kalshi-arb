"""Unit tests for crypto cross-asset observer suite."""
from __future__ import annotations

import pytest

from strategies.crypto_cross_asset_observer import (
    _pearson_correlation,
    _BookMidCache,
)


def test_pearson_correlation_perfect_positive():
    # Perfect positive: ys = 2 * xs
    xs = list(range(1, 11))  # [1, 2, 3, ..., 10]
    ys = [2 * x for x in xs]   # [2, 4, 6, ..., 20]
    assert _pearson_correlation(xs, ys) == pytest.approx(1.0, abs=1e-9)


def test_pearson_correlation_perfect_negative():
    # Perfect negative: ys = -xs + constant
    xs = list(range(1, 11))  # [1, 2, 3, ..., 10]
    ys = [11 - x for x in xs]  # [10, 9, 8, ..., 1]
    assert _pearson_correlation(xs, ys) == pytest.approx(-1.0, abs=1e-9)


def test_pearson_correlation_uncorrelated():
    # 16 samples alternating in a pattern that gives near-zero correlation
    xs = [1.0, 2.0, 1.0, 2.0] * 4
    ys = [1.0, 1.0, 2.0, 2.0] * 4
    result = _pearson_correlation(xs, ys)
    assert result is not None
    assert abs(result) < 0.1


def test_pearson_correlation_returns_none_on_short_input():
    # < 10 samples → None per spec
    xs = [1.0, 2.0, 3.0]
    ys = [1.0, 2.0, 3.0]
    assert _pearson_correlation(xs, ys) is None


def test_pearson_correlation_returns_none_on_zero_variance():
    # Flat series → undefined corr
    xs = [1.0] * 15
    ys = list(range(15))
    assert _pearson_correlation(xs, ys) is None


def test_book_mid_cache_update_and_get_now():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.45)
    # Same-ts lookup at 0s lookback returns the inserted value
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1000.0) == 0.45


def test_book_mid_cache_n_seconds_ago():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    cache.update("KXBTC", 90250.0, 1015.0, 0.44)
    cache.update("KXBTC", 90250.0, 1030.0, 0.47)
    # 30s ago from t=1030 → most recent entry with ts <= 1000
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1030.0) == 0.40


def test_book_mid_cache_returns_none_on_data_gap():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    # 30s ago from t=1100 → target ts=1070, nearest sample at t=1000
    # is 70s away → > 2*30 = 60s guard → None
    assert cache.n_seconds_ago("KXBTC", 90250.0, 30.0, 1100.0) is None


def test_book_mid_cache_returns_none_on_missing_key():
    cache = _BookMidCache()
    assert cache.n_seconds_ago("KXETH", 2120.0, 30.0, 1000.0) is None


def test_book_mid_cache_rolling_maxlen():
    cache = _BookMidCache()
    # Insert 70 entries — maxlen=60 should evict oldest 10
    for i in range(70):
        cache.update("KXBTC", 90250.0, float(i), float(i) * 0.01)
    # Oldest survivor should be ts=10
    assert cache.n_seconds_ago("KXBTC", 90250.0, 59.0, 69.0) == pytest.approx(0.10)
    # ts=0 should be gone
    assert cache.n_seconds_ago("KXBTC", 90250.0, 70.0, 69.0) is None


def test_book_mid_cache_prune_stale_keys():
    cache = _BookMidCache()
    cache.update("KXBTC", 90250.0, 1000.0, 0.40)
    cache.update("KXBTC", 90350.0, 1100.0, 0.50)
    # Prune from t=1200 with max_age=120s → 90250 (last_ts=1000, age=200) removed
    removed = cache.prune_stale(now_ts=1200.0, max_age_s=120.0)
    assert removed == 1
    assert cache.size() == 1
    assert cache.n_seconds_ago("KXBTC", 90250.0, 0.0, 1200.0) is None
    assert cache.n_seconds_ago("KXBTC", 90350.0, 0.0, 1200.0) is not None


def test_book_mid_cache_hard_cap_flush():
    cache = _BookMidCache(hard_cap=5)
    for i in range(6):
        cache.update("KXBTC", float(90000 + i * 100), 1000.0 + i, 0.4)
    # At insert 6 we exceed cap → flush returns True
    assert cache.size() == 0  # flushed
