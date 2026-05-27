"""Tests for cohort_decomp + strategy_correlation utilities."""
from __future__ import annotations

import sqlite3

import pytest

from core.cohort_decomp import auto_decompose, decompose, driver_summary, format_decomp
from core.strategy_correlation import correlation_matrix, daily_pnl_series, pearson


class TestCohortDecompose:
    def test_decompose_by_sport(self):
        entries = [
            {"sport": "MLB", "pnl": 0.1} for _ in range(15)
        ] + [
            {"sport": "WNBA", "pnl": -0.05} for _ in range(12)
        ] + [
            {"sport": "NHL", "pnl": 0.02} for _ in range(10)
        ]
        rows = decompose(entries, "sport")
        # Sorted by pnl desc
        assert rows[0]["key"] == "MLB"
        assert rows[0]["pnl"] > 0
        assert rows[1]["key"] == "NHL"

    def test_below_min_n_filtered(self):
        entries = [{"sport": "MLB", "pnl": 0.1}] * 5
        rows = decompose(entries, "sport", min_n=10)
        assert rows == []

    def test_auto_decompose_multi_dims(self):
        entries = [
            {"sport": "MLB", "direction": "yes", "pnl": 0.1}
            for _ in range(15)
        ] + [
            {"sport": "WNBA", "direction": "no", "pnl": 0.05}
            for _ in range(15)
        ]
        out = auto_decompose(entries, dim_keys=["sport", "direction"])
        assert "sport" in out
        assert "direction" in out
        assert len(out["sport"]) == 2

    def test_driver_summary_flags_dominant_cohort(self):
        # MLB drives 90% of |PnL|
        entries = (
            [{"sport": "MLB", "pnl": 0.10} for _ in range(50)]
            + [{"sport": "WNBA", "pnl": 0.005} for _ in range(15)]
            + [{"sport": "NHL", "pnl": 0.003} for _ in range(15)]
        )
        decomp = auto_decompose(entries, dim_keys=["sport"])
        flags = driver_summary(decomp)
        assert any("dominated" in f for f in flags)

    def test_format_includes_table(self):
        entries = [{"sport": "MLB", "pnl": 0.1} for _ in range(15)]
        decomp = auto_decompose(entries, dim_keys=["sport"])
        out = format_decomp(decomp)
        assert "MLB" in out


class TestCorrelation:
    def test_pearson_perfect_positive(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        assert pearson(xs, ys) == pytest.approx(1.0)

    def test_pearson_perfect_negative(self):
        xs = [1, 2, 3, 4, 5]
        ys = [5, 4, 3, 2, 1]
        assert pearson(xs, ys) == pytest.approx(-1.0)

    def test_pearson_uncorrelated(self):
        xs = [1, 2, 3, 4, 5]
        ys = [3, 1, 4, 1, 5]  # irregular
        r = pearson(xs, ys)
        assert -0.5 < r < 0.5

    def test_pearson_insufficient_data(self):
        assert pearson([1, 2], [3, 4]) is None
        assert pearson([], []) is None

    def _make_db_with_pnl(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE trades (
            strategy TEXT, timestamp TEXT, pnl REAL, status TEXT
        )""")
        return conn

    def test_correlation_matrix_high_corr_flagged(self):
        conn = self._make_db_with_pnl()
        # Two strategies with perfectly correlated daily PnL
        for d in range(10):
            day = f"2026-05-{d+1:02d}T12:00:00"
            conn.execute("INSERT INTO trades VALUES ('s1', ?, ?, 'settled')",
                         (day, d * 1.0))
            conn.execute("INSERT INTO trades VALUES ('s2', ?, ?, 'settled')",
                         (day, d * 2.0))
        report = correlation_matrix(conn, ["s1", "s2"], days_back=30)
        assert len(report["flagged_pairs"]) == 1
        assert abs(report["flagged_pairs"][0][2]) > 0.95

    def test_correlation_matrix_no_flag_for_uncorrelated(self):
        conn = self._make_db_with_pnl()
        # Independent random-looking daily PnL
        pnl_a = [0.1, -0.2, 0.3, -0.1, 0.2, -0.3, 0.05, -0.05, 0.15, -0.15]
        pnl_b = [-0.3, 0.1, -0.15, 0.25, -0.2, 0.05, 0.3, -0.05, -0.1, 0.2]
        for d in range(10):
            day = f"2026-05-{d+1:02d}T12:00:00"
            conn.execute("INSERT INTO trades VALUES ('s1', ?, ?, 'settled')",
                         (day, pnl_a[d]))
            conn.execute("INSERT INTO trades VALUES ('s2', ?, ?, 'settled')",
                         (day, pnl_b[d]))
        report = correlation_matrix(conn, ["s1", "s2"], days_back=30)
        # Correlation should be modest (not >0.7)
        assert len(report["flagged_pairs"]) == 0
