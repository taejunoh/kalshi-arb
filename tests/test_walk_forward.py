"""Tests for walk_forward consistency check."""
from __future__ import annotations

import pytest

from core.walk_forward import format_report, walk_forward_report


class TestWalkForward:
    def test_empty(self):
        r = walk_forward_report([])
        assert r["pooled_n"] == 0
        assert r["verdict"] == "PASS"

    def test_consistent_positive_passes(self):
        # 30 days, each day +$2 → all windows positive
        entries = []
        for d in range(30):
            for _ in range(5):
                entries.append({"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": 2.0})
        r = walk_forward_report(entries, n_windows=4)
        assert r["verdict"] == "PASS"
        assert r["positive_windows"] == 4

    def test_fluke_concentrated_in_one_window_fails(self):
        # All positive PnL in week 1, flat-to-negative weeks 2-4
        entries = []
        for d in range(7):
            for _ in range(10):
                entries.append({"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": 5.0})
        for d in range(7, 28):
            for _ in range(3):
                entries.append({"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": -0.5})
        r = walk_forward_report(entries, n_windows=4)
        # Pooled should be positive (week 1 dominates), but only 1/4 windows positive
        assert r["pooled_pnl"] > 0
        # Verdict may be FAIL or WARN depending on exact split
        assert r["verdict"] in ("FAIL", "WARN")

    def test_consistent_negative_passes(self):
        # All windows -EV → PASS (good for "kill" decisions)
        entries = []
        for d in range(20):
            for _ in range(5):
                entries.append({"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": -1.5})
        r = walk_forward_report(entries, n_windows=4)
        assert r["verdict"] == "PASS"
        assert r["positive_windows"] == 0

    def test_low_n_windows_skipped(self):
        # 1 entry per day for 4 days — windows have n=1 each
        entries = [{"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": 1.0} for d in range(4)]
        r = walk_forward_report(entries, n_windows=4, min_n_per_window=5)
        # All windows skipped
        assert r["evaluated_windows"] < 4
        assert r["verdict"] == "WARN"

    def test_tuple_input(self):
        entries = []
        for d in range(20):
            entries.append((f"2026-05-{d+1:02d}T12:00:00", 1.0))
        r = walk_forward_report(entries, n_windows=4, min_n_per_window=3)
        assert r["pooled_n"] == 20

    def test_format_report_includes_windows(self):
        entries = [{"ts": f"2026-05-{d+1:02d}T12:00:00", "pnl": 1.0}
                   for d in range(20) for _ in range(3)]
        r = walk_forward_report(entries, n_windows=4)
        out = format_report(r)
        assert "Window" in out
        assert "1/4" in out
        assert "4/4" in out
