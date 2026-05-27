"""Tests for backtest_postproc wrapper."""
from __future__ import annotations

import pytest

from core.backtest_postproc import postproc_report, quick_tier


def _consistent_entries(n=50):
    entries = []
    for d in range(min(n, 30)):
        for _ in range(max(1, n // 30)):
            entries.append({
                "ts": f"2026-05-{(d % 30) + 1:02d}T12:00:00",
                "pnl": 0.06, "price": 0.40,
                "sport": "MLB" if d % 2 == 0 else "WNBA",
                "direction": "yes",
            })
    return entries


class TestPostprocReport:
    def test_empty_returns_message(self):
        report = postproc_report([], name="empty_test")
        assert "No entries" in report

    def test_includes_tier_section(self):
        entries = _consistent_entries(50)
        report = postproc_report(entries, name="test_strat")
        assert "Sim reliability tier" in report
        assert "test_strat" in report

    def test_includes_cohort_decomp(self):
        entries = _consistent_entries(50)
        report = postproc_report(entries, name="test_strat")
        assert "Cohort decomposition" in report
        assert "sport" in report

    def test_includes_train_test(self):
        entries = _consistent_entries(50)
        report = postproc_report(entries, name="test_strat")
        assert "Train/test" in report

    def test_quick_tier_returns_dict(self):
        entries = _consistent_entries(50)
        r = quick_tier(entries, deploy_age_days=21)
        assert "tier" in r
        assert "overall_verdict" in r
        assert isinstance(r["tier"], int)
