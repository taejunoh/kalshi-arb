"""Tests for execution_realism module — verify slip + TTL + wipe-zone modeling."""
from __future__ import annotations

import sqlite3

import pytest

from core.execution_realism import (
    KALSHI_FEE,
    breakeven_wr,
    is_wipe_zone,
    realistic_pnl,
    simulate_ttl_fill,
    slip_adjusted_pnl,
)


class TestRealisticPnl:
    def test_yes_win(self):
        # Buy YES at 0.40, outcome yes -> +0.60 - 0.07 = +0.53
        assert realistic_pnl(0.40, "yes", "yes") == pytest.approx(0.53)

    def test_yes_loss(self):
        # Buy YES at 0.40, outcome no -> -0.40 - 0.07 = -0.47
        assert realistic_pnl(0.40, "yes", "no") == pytest.approx(-0.47)

    def test_no_win(self):
        # Buy NO at 0.60 (no_ask), outcome no -> +0.40 - 0.07 = +0.33
        assert realistic_pnl(0.60, "no", "no") == pytest.approx(0.33)

    def test_degenerate_returns_none(self):
        assert realistic_pnl(0.0, "yes", "yes") is None
        assert realistic_pnl(1.0, "yes", "yes") is None
        assert realistic_pnl(None, "yes", "yes") is None

    def test_fee_override(self):
        # Custom fee 0.05 instead of default 0.07
        assert realistic_pnl(0.40, "yes", "yes", fee=0.05) == pytest.approx(0.55)


class TestWipeZone:
    def test_wipe_zone_high(self):
        assert is_wipe_zone(0.90)
        assert is_wipe_zone(0.85)
        assert is_wipe_zone(0.95)

    def test_wipe_zone_low(self):
        assert is_wipe_zone(0.10)
        assert is_wipe_zone(0.15)
        assert is_wipe_zone(0.05)

    def test_mid_price_not_wipe(self):
        assert not is_wipe_zone(0.40)
        assert not is_wipe_zone(0.50)
        assert not is_wipe_zone(0.65)
        assert not is_wipe_zone(0.20)
        assert not is_wipe_zone(0.80)


class TestBreakevenWr:
    def test_be_at_mid_price(self):
        # ya 0.50, fee 0.07:
        # win = 0.43, loss = -0.57. BE WR = 0.57 / (0.57+0.43) = 0.57
        assert breakeven_wr(0.50) == pytest.approx(0.57, abs=0.01)

    def test_be_at_underdog(self):
        # ya 0.30: win=0.63, loss=-0.37. BE = 0.37 / 1.0 = 0.37
        assert breakeven_wr(0.30) == pytest.approx(0.37, abs=0.01)

    def test_be_at_wipe_zone(self):
        # ya 0.90: win=0.03, loss=-0.97. BE = 0.97 / 1.0 = 0.97
        assert breakeven_wr(0.90) == pytest.approx(0.97, abs=0.01)

    def test_be_above_one_returns_none(self):
        # At extreme prices, fee exceeds win-payoff -> impossible to BE.
        # E.g. price 0.95: win = -0.02 (negative!). Impossible.
        assert breakeven_wr(0.95) is None


class TestSimulateTtlFill:
    def _make_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE odds_observations (
            kalshi_ticker TEXT, timestamp TEXT,
            kalshi_yes_ask REAL, kalshi_no_ask REAL
        )""")
        return conn

    def test_immediate_fill(self):
        conn = self._make_db()
        conn.executemany("INSERT INTO odds_observations VALUES (?, ?, ?, ?)", [
            ("KXTEST-A", "2026-05-27T10:00:00", 0.40, 0.62),
            ("KXTEST-A", "2026-05-27T10:00:30", 0.39, 0.63),
        ])
        # Limit BUY yes at 0.40 — should fill (best ask 0.39 <= 0.40)
        r = simulate_ttl_fill(conn, "KXTEST-A", "2026-05-27T10:00:00",
                              limit_price=0.40, side="yes", ttl_secs=180)
        assert r["filled"]
        assert r["fill_price"] == 0.40

    def test_miss_price_too_high(self):
        conn = self._make_db()
        conn.executemany("INSERT INTO odds_observations VALUES (?, ?, ?, ?)", [
            ("KXTEST-B", "2026-05-27T10:00:00", 0.45, 0.57),
            ("KXTEST-B", "2026-05-27T10:01:00", 0.46, 0.56),
        ])
        # Limit BUY yes at 0.40 — best ask 0.45, never fills
        r = simulate_ttl_fill(conn, "KXTEST-B", "2026-05-27T10:00:00",
                              limit_price=0.40, side="yes", ttl_secs=180)
        assert not r["filled"]
        assert "0.450" in r["miss_reason"]

    def test_no_observations(self):
        conn = self._make_db()
        r = simulate_ttl_fill(conn, "KXTEST-C", "2026-05-27T10:00:00",
                              limit_price=0.40, side="yes", ttl_secs=180)
        assert not r["filled"]
        assert "no observations" in r["miss_reason"]

    def test_outside_ttl_window_ignored(self):
        conn = self._make_db()
        conn.executemany("INSERT INTO odds_observations VALUES (?, ?, ?, ?)", [
            ("KXTEST-D", "2026-05-27T10:00:00", 0.45, 0.57),
            # 300s later — outside 180s TTL
            ("KXTEST-D", "2026-05-27T10:05:00", 0.39, 0.63),
        ])
        r = simulate_ttl_fill(conn, "KXTEST-D", "2026-05-27T10:00:00",
                              limit_price=0.40, side="yes", ttl_secs=180)
        assert not r["filled"]


class TestSlipAdjustedPnl:
    """Legacy heuristic path — use_calibration=False explicitly to keep
    these tests pinned to the original 2pp-in-wipe-zone behavior even
    after calibration data is loaded."""

    def test_mid_price_unchanged_legacy(self):
        # Mid-price (not wipe), heuristic path = same as realistic_pnl
        expected = realistic_pnl(0.40, "yes", "yes")
        actual = slip_adjusted_pnl(0.40, "yes", "yes", use_calibration=False)
        assert actual == pytest.approx(expected)

    def test_wipe_zone_extra_slip_legacy(self):
        # ya 0.90 (wipe zone) → heuristic adds 0.02 slip → effective 0.92
        # win: 1 - 0.92 - 0.07 = 0.01 (was 0.03 without slip)
        result = slip_adjusted_pnl(0.90, "yes", "yes", use_calibration=False)
        assert result == pytest.approx(0.01, abs=0.001)

    def test_wipe_zone_loss_penalty_legacy(self):
        # ya 0.90 + 0.02 slip = 0.92, loss → -0.92 - 0.07 = -0.99
        result = slip_adjusted_pnl(0.90, "yes", "no", use_calibration=False)
        assert result == pytest.approx(-0.99, abs=0.001)

    def test_wipe_zone_extreme_returns_none(self):
        # ya 0.99 + 0.02 slip > 1.0 → can't model
        result = slip_adjusted_pnl(0.99, "yes", "yes", use_calibration=False)
        assert result is None


class TestContinuousWipeRisk:
    def test_safe_band_zero(self):
        from core.execution_realism import continuous_wipe_risk
        assert continuous_wipe_risk(0.40) == 0.0
        assert continuous_wipe_risk(0.50) == 0.0
        assert continuous_wipe_risk(0.65) == 0.0

    def test_ramp_high_side(self):
        from core.execution_realism import continuous_wipe_risk
        # At 0.85: (0.85-0.80)/0.15 = 0.333
        assert continuous_wipe_risk(0.85) == pytest.approx(0.333, abs=0.01)
        # At 0.95: ramp saturated to 1.0
        assert continuous_wipe_risk(0.95) == pytest.approx(1.0)

    def test_ramp_low_side(self):
        from core.execution_realism import continuous_wipe_risk
        # At 0.15: (0.20-0.15)/0.15 = 0.333
        assert continuous_wipe_risk(0.15) == pytest.approx(0.333, abs=0.01)
        # At 0.05: ramp saturated
        assert continuous_wipe_risk(0.05) == pytest.approx(1.0)

    def test_extreme_capped(self):
        from core.execution_realism import continuous_wipe_risk
        assert continuous_wipe_risk(0.99) == 1.0
        assert continuous_wipe_risk(0.01) == 1.0


class TestCalibrationAwarePnl:
    """Calibration-aware path — depends on data/slip_calibration.json
    if present, otherwise falls back to legacy behavior."""

    def test_uses_calibration_when_present(self):
        """If calibration file exists, mid-price gets non-zero slip
        (the data shows ~1pp slip in band C). Otherwise = realistic_pnl."""
        from core.execution_realism import calibrated_slip
        slip = calibrated_slip(0.40)
        # Whatever it is, applying it should give the same pnl as
        # manually adding that slip to price.
        expected = realistic_pnl(0.40 + slip, "yes", "yes")
        actual = slip_adjusted_pnl(0.40, "yes", "yes", use_calibration=True)
        # If calibration loaded, slip > 0; if not, slip = 0 → both paths
        # produce equivalent result.
        assert actual == pytest.approx(expected)
