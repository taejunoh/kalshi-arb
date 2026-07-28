"""live_fade post-fix cohort gate.

The gate exists because the pre-fix paper twin booked at a price the book never
offered (+22.01% paper vs -10.28% on the real fills). These tests pin the
arithmetic that decides whether the honest cohort earns its LIVE slot back.
"""
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.livefade_postfix_gate import (  # noqa: E402
    POSTFIX_EPOCH, STRATEGY, _two_prop_z, _wilson_ci, _should_alert, evaluate,
)


def _db(rows):
    """rows = (status, ts, price, realized, size, fee, pnl)"""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,
           timestamp TEXT, strategy TEXT, market_id TEXT, direction TEXT,
           price REAL, realized_fill_price REAL, size REAL, fee REAL,
           pnl REAL, status TEXT)"""
    )
    for status, ts, price, realized, size, fee, pnl in rows:
        conn.execute(
            "INSERT INTO trades (timestamp, strategy, market_id, direction, price,"
            " realized_fill_price, size, fee, pnl, status)"
            " VALUES (?,?,?,'yes',?,?,?,?,?,?)",
            (ts, STRATEGY, "MKT", price, realized, size, fee, pnl, status),
        )
    conn.commit()
    return conn


PRE = "2026-07-01T00:00:00+00:00"
POST = "2026-07-28T15:00:00+00:00"


def _legs(status, ts, n, wins, price=0.80, size=4.0, fee=0.03):
    """n legs at `price`; `wins` of them win. Win pays size/price - size - fee."""
    out = []
    for i in range(n):
        pnl = (size / price - size - fee) if i < wins else (-size - fee)
        out.append((status, ts, price, price, size, fee, pnl))
    return out


# ── statistics ────────────────────────────────────────────────────────

def test_wilson_widens_at_small_n():
    lo_small, hi_small = _wilson_ci(8, 10)
    lo_big, hi_big = _wilson_ci(80, 100)
    assert (hi_small - lo_small) > (hi_big - lo_big)


def test_two_prop_z_zero_for_identical_rates():
    assert _two_prop_z(40, 50, 80, 100) == pytest.approx(0.0, abs=1e-9)


def test_two_prop_z_none_when_an_arm_is_empty():
    assert _two_prop_z(0, 0, 10, 20) is None


# ── cohort split + breakeven ──────────────────────────────────────────

def test_epoch_splits_pre_from_post():
    conn = _db(_legs("settled", PRE, 4, 3) + _legs("settled", POST, 6, 5))
    r = evaluate(conn)
    assert r["pre_real"]["n"] == 4
    assert r["post_real"]["n"] == 6


def test_breakeven_is_derived_from_price_and_fees_not_assumed():
    """BE_WR = p * (1 + fee/stake). At p=0.80 with fee 0.03 on size 4.00 that is
    0.80 * 1.0075 = 80.6% — buying favourites needs a high win rate just to
    stand still, which is exactly why WR 83.7% still lost money pre-fix."""
    conn = _db(_legs("settled", POST, 10, 8, price=0.80, size=4.0, fee=0.03))
    be = evaluate(conn)["post_real"]["be_wr"]
    assert be == pytest.approx(0.80 * 1.0075, abs=1e-6)


def test_real_book_prices_at_realized_fill_not_signal_price():
    """The whole point: a real fill is judged at what it actually paid."""
    conn = _db([("settled", POST, 0.73, 0.93, 4.0, 0.03, -4.03)])
    assert evaluate(conn)["post_real"]["avg_px"] == pytest.approx(0.93)


# ── verdicts ──────────────────────────────────────────────────────────

def test_below_target_reports_progress_only():
    conn = _db(_legs("settled", POST, 12, 11))
    r = evaluate(conn)
    assert r["verdict"] == "COLLECTING"
    assert "12/50" in r["detail"]


def test_pre_fix_real_fills_count_toward_the_target():
    """The booking fix never touched live execution, so a pre-fix real fill is
    as honest as a post-fix one. Counting only the post-fix slice would throw
    away 45 real fills and pay real money to re-collect them."""
    conn = _db(_legs("settled", PRE, 40, 34) + _legs("settled", POST, 5, 4))
    assert evaluate(conn)["n"] == 45


def test_early_kill_when_even_a_perfect_remainder_cannot_reach_breakeven():
    """n>=20 and Wilson UPPER below BE → stop paying to reach the milestone."""
    conn = _db(_legs("settled", POST, 25, 5, price=0.80))
    r = evaluate(conn)
    assert r["verdict"] == "EARLY-KILL"
    assert "Demote now" in r["detail"]


def test_early_kill_does_not_fire_while_still_plausible():
    conn = _db(_legs("settled", POST, 25, 21, price=0.80))
    assert evaluate(conn)["verdict"] == "COLLECTING"


def test_pass_requires_wilson_lower_bound_above_breakeven():
    conn = _db(_legs("settled", POST, 60, 59, price=0.80)
               + _legs("settled", PRE, 60, 59, price=0.80))
    r = evaluate(conn)
    assert r["verdict"] == "PASS"
    assert r["basis"] == "all real fills"


def test_fail_when_lower_bound_sits_below_breakeven():
    """WR 85% at BE 80.6% — point estimate clears, Wilson LB does not."""
    conn = _db(_legs("settled", POST, 50, 42, price=0.80)
               + _legs("settled", PRE, 50, 42, price=0.80))
    r = evaluate(conn)
    assert r["verdict"] == "FAIL"


def test_regime_shift_drops_the_pre_fix_arm_instead_of_pooling():
    """Pre-fix WR 70% vs post-fix 96% → |z| >= 1.96. Live execution was never
    touched by the booking fix, so this should not happen — if it does,
    something else changed and the stale arm must not be pooled in.

    Pooled WR (83%) is deliberately kept plausible enough to clear early-kill,
    so the test actually reaches the z-branch."""
    conn = _db(_legs("settled", POST, 50, 48, price=0.80)
               + _legs("settled", PRE, 50, 35, price=0.80))
    r = evaluate(conn)
    assert r["verdict"] != "EARLY-KILL"
    assert abs(r["z"]) >= 1.96
    assert "UNEXPECTED regime shift" in r["basis"]


def test_untestable_split_still_pools_rather_than_discarding_data():
    """No post-fix fills yet → z is None. The structural argument (live path
    untouched) already says pool, so an untestable split must not shrink n."""
    conn = _db(_legs("settled", PRE, 60, 59, price=0.80))
    r = evaluate(conn)
    assert r["z"] is None
    assert r["basis"] == "all real fills"


# ── booking-honesty monitor ───────────────────────────────────────────

def test_booking_gap_is_reported_so_a_hook_regression_is_visible():
    conn = _db(
        [("settled", POST, 0.73, 0.90, 4.0, 0.03, -4.03)]
        + [("paper_settled", POST, 0.75, None, 4.0, 0.03, -4.03)]
    )
    r = evaluate(conn)
    assert r["price_gap"] == pytest.approx(0.75 - 0.90)


def test_honest_booking_shows_a_near_zero_gap():
    conn = _db(
        [("settled", POST, 0.73, 0.86, 4.0, 0.03, -4.03)]
        + [("paper_settled", POST, 0.86, None, 4.0, 0.03, -4.03)]
    )
    assert evaluate(conn)["price_gap"] == pytest.approx(0.0)


# ── alert throttle ────────────────────────────────────────────────────

def test_alerts_on_verdict_change():
    assert _should_alert({"verdict": "EARLY-KILL", "n": 22},
                         {"verdict": "COLLECTING", "n": 22}) is True


def test_quiet_between_progress_milestones():
    assert _should_alert({"verdict": "COLLECTING", "n": 13},
                         {"verdict": "COLLECTING", "n": 11}) is False


def test_alerts_every_ten_fills_of_progress():
    assert _should_alert({"verdict": "COLLECTING", "n": 20},
                         {"verdict": "COLLECTING", "n": 19}) is True


def test_epoch_is_the_ec2_deploy_of_the_booking_fix():
    assert POSTFIX_EPOCH.startswith("2026-07-22T23:07")
