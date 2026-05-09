import math
import pytest

from core.macro_consensus import bucket_prob_from_normal, normal_cdf


def test_normal_cdf_zero_mean_unit_variance():
    """N(0, 1) CDF: P(Z <= 0) = 0.5, P(Z <= 1.96) ≈ 0.975"""
    assert abs(normal_cdf(0.0, mu=0.0, sigma=1.0) - 0.5) < 1e-6
    assert abs(normal_cdf(1.96, mu=0.0, sigma=1.0) - 0.9750) < 1e-3
    assert abs(normal_cdf(-1.96, mu=0.0, sigma=1.0) - 0.0250) < 1e-3


def test_bucket_prob_centered_bucket_around_mean():
    """N(0.3, 0.1): P(0.2 <= X <= 0.4) ≈ 0.6827 (1-sigma window)"""
    p = bucket_prob_from_normal(low=0.2, high=0.4, mu=0.3, sigma=0.1)
    assert abs(p - 0.6827) < 1e-3


def test_bucket_prob_far_tail():
    """N(0.3, 0.1): P(0.6 <= X <= 0.7) ≈ 0.001 (3+ sigma)"""
    p = bucket_prob_from_normal(low=0.6, high=0.7, mu=0.3, sigma=0.1)
    assert p < 0.01


def test_bucket_prob_open_lower_bound():
    """low=None → P(X <= high), no lower clamp"""
    p = bucket_prob_from_normal(low=None, high=0.3, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_open_upper_bound():
    """high=None → P(X >= low), no upper clamp"""
    p = bucket_prob_from_normal(low=0.3, high=None, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_zero_sigma_returns_indicator():
    """Degenerate sigma=0 case: deterministic point mass at mu"""
    # mu inside bucket → 1.0
    assert bucket_prob_from_normal(0.2, 0.4, 0.3, 0.0) == 1.0
    # mu outside bucket → 0.0
    assert bucket_prob_from_normal(0.5, 0.6, 0.3, 0.0) == 0.0
