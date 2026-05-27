"""Tests for bootstrap CI utility."""
from __future__ import annotations

import pytest

from core.bootstrap import bootstrap_ci, bootstrap_mean_ci, bootstrap_sum_ci


class TestBootstrap:
    def test_empty(self):
        r = bootstrap_ci([])
        assert r["n"] == 0
        assert r["overlap_zero"]

    def test_mean_consistent_positive(self):
        # 50 values, all near +0.10 — CI should NOT cover zero
        values = [0.10 + 0.01 * (i % 3 - 1) for i in range(50)]
        r = bootstrap_mean_ci(values)
        assert r["point"] > 0
        assert r["lower"] > 0
        assert not r["overlap_zero"]

    def test_mean_noisy_overlaps_zero(self):
        # 30 values, alternating wins and losses — mean ~0 noisy
        values = [0.5 if i % 2 == 0 else -0.5 for i in range(30)]
        r = bootstrap_mean_ci(values)
        # Mean is exactly 0, CI brackets zero
        assert r["overlap_zero"]

    def test_sum_ci(self):
        values = [1.0] * 10
        r = bootstrap_sum_ci(values)
        # Sum = 10, CI bounded
        assert r["point"] == 10.0
        # With identical values, lower == upper == 10
        assert r["lower"] == 10.0
        assert r["upper"] == 10.0

    def test_reproducibility(self):
        values = [0.1, -0.05, 0.2, -0.1, 0.15] * 10
        r1 = bootstrap_mean_ci(values, seed=42)
        r2 = bootstrap_mean_ci(values, seed=42)
        assert r1 == r2

    def test_ci_widens_with_smaller_sample(self):
        # Same mean, smaller sample → wider CI
        small = [0.10 + 0.05 * (i % 3 - 1) for i in range(10)]
        large = [0.10 + 0.05 * (i % 3 - 1) for i in range(200)]
        r_small = bootstrap_mean_ci(small)
        r_large = bootstrap_mean_ci(large)
        small_width = r_small["upper"] - r_small["lower"]
        large_width = r_large["upper"] - r_large["lower"]
        assert small_width > large_width
