"""Portfolio performance metric.

Each test pins one of the three ways the old pooled-ROI read misled on
2026-07-28: nested windows, stake concentration, and a shifting strategy set.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.portfolio_performance import (  # noqa: E402
    CONCENTRATION_FLAG, WEIGHTING_DIVERGENCE_PP, analyze, load_rows,
)


def _rows(*specs):
    """(week, strategy, size, pnl)"""
    return list(specs)


# ── weighting ─────────────────────────────────────────────────────────

def _legs(wk, strategy, n, size, roi_pct):
    """n trades for one strategy, together hitting `roi_pct`."""
    return [(wk, strategy, size, size * roi_pct / 100.0) for _ in range(n)]


def test_equal_weighting_denies_a_big_stake_strategy_the_headline():
    """The 2026-07-28 case: three ~$24 World Cup outrights swamped hundreds of
    $2-4 sports bets. Stake-weighted says the book is bleeding; equal-weighted
    says one strategy is."""
    rows = (_legs(0, "xvenue_polymarket", 3, 25.0, -60.0)
            + _legs(0, "sports_a", 3, 1.67, 20.0)
            + _legs(0, "sports_b", 3, 1.67, 20.0)
            + _legs(0, "sports_c", 3, 1.67, 20.0))
    w = analyze(rows, weeks=1)["weeks"][0]
    assert w["voters"] == 4
    assert w["equal_roi"] == pytest.approx((-60.0 + 20 + 20 + 20) / 4)  # 0.0
    assert w["stake_roi"] < -40  # stake-weighted still says catastrophe


def test_singleton_strategies_do_not_get_an_equal_weight_vote():
    """Equal-weighting is supposed to remove bias, not add it. A strategy with
    one -105% longshot loss must not outvote one with 20 near-breakeven
    trades — that is what MIN_STRATEGY_N is for."""
    rows = _legs(0, "steady", 20, 2.0, 1.0) + _legs(0, "oneoff", 1, 2.0, -105.0)
    w = analyze(rows, weeks=1)["weeks"][0]
    assert w["strats"] == 2 and w["voters"] == 1
    assert w["equal_roi"] == pytest.approx(1.0)


def test_below_threshold_strategies_still_count_in_stake_weighted():
    """They are excluded from the vote, not from reality."""
    rows = _legs(0, "steady", 20, 2.0, 0.0) + _legs(0, "oneoff", 1, 40.0, -100.0)
    w = analyze(rows, weeks=1)["weeks"][0]
    assert w["stake_roi"] == pytest.approx(100 * -40.0 / 80.0)


def test_no_qualifying_strategies_reports_none_not_zero():
    w = analyze(_legs(0, "a", 1, 5.0, 50.0), weeks=1)["weeks"][0]
    assert w["equal_roi"] is None and w["voters"] == 0


def test_weighting_divergence_is_flagged():
    rows = _legs(0, "big", 3, 33.0, -50.0) + _legs(0, "small", 3, 0.5, 50.0)
    assert any("stake concentration" in f
               for f in analyze(rows, weeks=1)["flags"])


def test_no_divergence_flag_when_the_two_agree():
    rows = _legs(0, "a", 3, 10.0, 10.0) + _legs(0, "b", 3, 10.0, 10.0)
    assert not [f for f in analyze(rows, weeks=1)["flags"]
                if "stake concentration" in f]


def test_equal_weighting_skips_zero_stake_instead_of_scoring_it_zero():
    rows = _legs(0, "a", 3, 10.0, 20.0) + [(0, "ghost", 0.0, 0.0)] * 3
    w = analyze(rows, weeks=1)["weeks"][0]
    assert w["equal_roi"] == pytest.approx(20.0)


# ── concentration ─────────────────────────────────────────────────────

def test_single_strategy_dominance_is_flagged_as_anecdote():
    rows = _rows((0, "whale", 10.0, -9.0), (0, "a", 10.0, 0.5),
                 (0, "b", 10.0, 0.5))
    w = analyze(rows, weeks=1)["weeks"][0]
    assert w["top_share"] >= CONCENTRATION_FLAG
    assert any("anecdote" in f for f in analyze(rows, weeks=1)["flags"])


def test_even_spread_is_not_flagged():
    rows = _rows(*[(0, f"s{i}", 10.0, -1.0) for i in range(10)])
    assert not [f for f in analyze(rows, weeks=1)["flags"] if "anecdote" in f]


# ── fixed basket ──────────────────────────────────────────────────────

def test_fixed_basket_keeps_only_strategies_present_every_week():
    rows = _rows(
        (0, "always", 10.0, 1.0), (1, "always", 10.0, 1.0),
        (0, "newcomer", 10.0, -5.0),
    )
    r = analyze(rows, weeks=2)
    assert r["common"] == ["always"]
    wk0 = next(b for b in r["basket"] if b["wk"] == 0)
    assert wk0["pnl"] == pytest.approx(1.0)  # newcomer excluded


def test_fixed_basket_removes_a_trend_that_mix_shift_invented():
    """wk1 has a loser that wk0 lacks. Pooled, wk1 looks far worse; the shared
    strategy performed identically in both."""
    rows = _rows(
        (0, "shared", 10.0, 1.0),
        (1, "shared", 10.0, 1.0),
        (1, "gone_now", 90.0, -45.0),
    )
    r = analyze(rows, weeks=2)
    pooled = {w["wk"]: w["stake_roi"] for w in r["weeks"]}
    basket = {b["wk"]: b["roi"] for b in r["basket"]}
    assert pooled[1] < pooled[0]              # pooled invents a decline
    assert basket[1] == pytest.approx(basket[0])  # like-for-like: flat


def test_empty_basket_is_reported_not_silently_skipped():
    rows = _rows((0, "only_now", 10.0, 1.0), (1, "only_then", 10.0, 1.0))
    r = analyze(rows, weeks=2)
    assert r["common"] == []
    assert "NOT like-for-like" in __import__(
        "scripts.portfolio_performance", fromlist=["render"]
    ).render("x", r)


# ── windowing ─────────────────────────────────────────────────────────

def test_weeks_are_disjoint_buckets():
    rows = _rows((0, "a", 10.0, 1.0), (1, "a", 10.0, -1.0), (2, "a", 10.0, 5.0))
    r = analyze(rows, weeks=3)
    assert [w["wk"] for w in r["weeks"]] == [0, 1, 2]
    assert [w["n"] for w in r["weeks"]] == [1, 1, 1]


def test_rows_beyond_the_horizon_are_dropped():
    rows = _rows((0, "a", 10.0, 1.0), (9, "a", 10.0, -99.0))
    r = analyze(rows, weeks=3)
    assert [w["wk"] for w in r["weeks"]] == [0]


def test_no_rows_returns_empty_not_a_crash():
    r = analyze([], weeks=4)
    assert r["weeks"] == [] and r["common"] == []
    assert "no settled trades" in __import__(
        "scripts.portfolio_performance", fromlist=["render"]
    ).render("x", r)


# ── SQL layer ─────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, strategy TEXT, size REAL, pnl REAL, status TEXT)""")
    return conn


def test_both_ledger_timestamp_formats_are_matched():
    """Ledger rows appear ISO-T-with-offset ('...T12:00:00+00:00') and
    space-separated. Both must be found, and both sides of the comparison go
    through datetime() so the boundary does not misorder
    (feedback_sqlite_timestamp_t_space_compare)."""
    conn = _db()
    conn.execute(
        "INSERT INTO trades (timestamp, strategy, size, pnl, status) VALUES "
        "(strftime('%Y-%m-%d','now','-3 days') || 'T12:00:00+00:00', 'iso_t',"
        " 5, 1, 'paper_settled')"
    )
    conn.execute(
        "INSERT INTO trades (timestamp, strategy, size, pnl, status) VALUES "
        "(datetime('now','-3 days'), 'spaced', 5, 1, 'paper_settled')"
    )
    conn.commit()
    rows = load_rows(conn, status="paper_settled", weeks=6)
    assert {r[1] for r in rows} == {"iso_t", "spaced"}


def test_rows_older_than_the_horizon_are_excluded_by_the_sql():
    conn = _db()
    conn.execute(
        "INSERT INTO trades (timestamp, strategy, size, pnl, status) VALUES "
        "(strftime('%Y-%m-%d','now','-400 days') || 'T12:00:00+00:00', 'ancient',"
        " 5, 1, 'paper_settled')"
    )
    conn.commit()
    assert load_rows(conn, status="paper_settled", weeks=6) == []


def test_load_rows_filters_by_book_and_skips_zero_stake():
    conn = _db()
    for strat, size, status in (("a", 5, "paper_settled"), ("b", 5, "settled"),
                                ("c", 0, "paper_settled")):
        conn.execute(
            "INSERT INTO trades (timestamp, strategy, size, pnl, status) "
            "VALUES (strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-1 days'),?,?,1,?)",
            (strat, size, status),
        )
    conn.commit()
    assert {r[1] for r in load_rows(conn, status="paper_settled", weeks=6)} == {"a"}
    assert {r[1] for r in load_rows(conn, status="settled", weeks=6)} == {"b"}
