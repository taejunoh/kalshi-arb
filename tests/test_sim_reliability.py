"""Tests for sim_reliability tier assessment — the integration layer
that wires Phase A + B utilities into a single verdict."""
from __future__ import annotations

import pytest

from core.sim_reliability import assess, format_result


def _consistent_paper(n=50, avg_pnl=0.08, n_days=21, start="2026-05-06"):
    """Generate clean +EV entries over n_days starting at start."""
    from datetime import datetime, timedelta
    base = datetime.fromisoformat(start)
    entries = []
    per_day = max(1, n // n_days)
    for d in range(n_days):
        for _ in range(per_day):
            entries.append({
                "ts": (base + timedelta(days=d)).isoformat(),
                "pnl": avg_pnl,
                "price": 0.45,
            })
    return entries


class TestTierAssignment:
    def test_tiny_sample_tier1(self):
        # n=10 → too small
        entries = _consistent_paper(n=10, n_days=5)
        r = assess(entries)
        assert r["tier"] == 1
        assert r["overall_verdict"] == "SKIP"

    def test_medium_sample_no_live_tier3(self):
        # n=50, 21d, no LIVE data
        entries = _consistent_paper(n=50, n_days=21)
        r = assess(entries, deploy_age_days=21)
        # Should be tier 4 (deploy_age=21, n>=50, day_skew PASS, walk_forward PASS)
        assert r["tier"] >= 3

    def test_tier5_with_live_twin_tight(self):
        # n=150, 60d, tight LIVE-paper gap
        entries = _consistent_paper(n=150, n_days=60)
        r = assess(entries, has_paper_twin_gap_pp=1.5, deploy_age_days=60)
        assert r["tier"] == 5
        assert r["overall_verdict"] == "PROMOTE"

    def test_wipe_zone_drops_to_tier1(self):
        # Even with big n, if avg price in wipe zone → tier 1
        entries = _consistent_paper(n=100, n_days=30)
        # Override price to wipe zone
        for e in entries:
            e["price"] = 0.92
        r = assess(entries, deploy_age_days=30)
        assert r["tier"] == 1
        assert any(c["name"] == "wipe_zone" and c["verdict"] == "FAIL"
                   for c in r["checks"])

    def test_regime_fragile_drops_to_tier1(self):
        # Single dominant day → day_skew FAIL
        entries = []
        # Day 1: 50 huge winners
        for _ in range(50):
            entries.append({"ts": "2026-05-20T12:00:00", "pnl": 0.5,
                            "price": 0.45})
        # Other days: small + mixed
        for d in range(2, 22):
            for _ in range(3):
                entries.append({"ts": f"2026-05-{d:02d}T12:00:00",
                                "pnl": 0.01, "price": 0.45})
        r = assess(entries, deploy_age_days=21)
        # Concentration too high → tier 1
        assert r["tier"] == 1

    def test_paper_twin_diverges_drops_tier(self):
        entries = _consistent_paper(n=80, n_days=30)
        r = assess(entries, has_paper_twin_gap_pp=15.0, deploy_age_days=30)
        # Large gap → tier 1
        assert r["tier"] == 1

    def test_format_result_includes_checks(self):
        entries = _consistent_paper(n=50, n_days=21)
        r = assess(entries, deploy_age_days=21)
        out = format_result(r)
        assert "tier" in out.lower()
        assert "Checks" in out
        assert "day_skew" in out


class TestSurvivorshipBias:
    def test_high_stale_ratio_fails(self):
        entries = _consistent_paper(n=80, n_days=30)
        # 35% stale → FAIL
        r = assess(entries, deploy_age_days=30, stale_ratio=0.35)
        assert r["tier"] == 1
        survivorship = [c for c in r["checks"] if c["name"] == "survivorship"][0]
        assert survivorship["verdict"] == "FAIL"

    def test_moderate_stale_warns(self):
        entries = _consistent_paper(n=80, n_days=30)
        # 15% stale → WARN
        r = assess(entries, deploy_age_days=30, stale_ratio=0.15)
        survivorship = [c for c in r["checks"] if c["name"] == "survivorship"][0]
        assert survivorship["verdict"] == "WARN"
        # Should NOT auto-FAIL (just WARN)
        assert r["tier"] >= 3

    def test_low_stale_passes(self):
        entries = _consistent_paper(n=80, n_days=30)
        r = assess(entries, deploy_age_days=30, stale_ratio=0.03)
        survivorship = [c for c in r["checks"] if c["name"] == "survivorship"][0]
        assert survivorship["verdict"] == "PASS"


class TestCmeBasisFlipped:
    """Apply assess() to the real cme_basis_fade FLIPPED 30d data —
    should output tier 1 (regime-fragile)."""

    def test_real_pattern_fails(self):
        data = [
            ('2026-05-19', 63, 47, 16.63),
            ('2026-05-20', 455, 349, 117.14),
            ('2026-05-21', 319, 258, 98.01),
            ('2026-05-22', 141, 25, -47.59),
            ('2026-05-23', 104, 0, -52.64),
            ('2026-05-24', 103, 9, -39.02),
            ('2026-05-25', 62, 29, 0.44),
            ('2026-05-26', 391, 313, 129.01),
        ]
        entries = []
        for day, n, wins, pnl_total in data:
            win_pnl = (1.0 - 0.40 - 0.07)
            loss_pnl = (-0.40 - 0.07)
            for i in range(n):
                entries.append({
                    "ts": day + "T12:00:00",
                    "pnl": win_pnl if i < wins else loss_pnl,
                    "price": 0.40,
                })
        r = assess(entries, deploy_age_days=8)
        assert r["tier"] == 1, f"Expected tier 1 but got {r['tier']} - {format_result(r)}"
        # Specifically should fail on day_skew
        day_skew_check = [c for c in r["checks"] if c["name"] == "day_skew"][0]
        assert day_skew_check["verdict"] == "FAIL"
