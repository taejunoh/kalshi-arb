"""Unit tests for crypto cross-asset observer suite."""
from __future__ import annotations

import pytest

from strategies.crypto_cross_asset_observer import (
    _pearson_correlation,
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
