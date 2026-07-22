"""Forward re-measure of the ML+EV>=5% watch cell — 2026-07-22.

The report fires twice (8/05, 8/12) and then tells the user to turn its own timer
off. Its job is to answer ONE question honestly: did the win rate stay under
breakeven? So the tests pin the verdict boundaries and the two ways this could
mislead — reporting a call on too few bets, and drifting out of sync with
core.is_ev_watch (the cell is selected by the predicate, never by hand-written SQL).

Dates are relative to core.EV_WATCH_SINCE rather than hard-coded, so this file
cannot rot into a date bomb the way a fixed-window test would.
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_core as core                                # noqa: E402
import softbook_ev_watch_eval as ev                            # noqa: E402

SINCE = core.EV_WATCH_SINCE
BEFORE = (date.fromisoformat(SINCE) - timedelta(days=3)).isoformat()
AFTER = (date.fromisoformat(SINCE) + timedelta(days=3)).isoformat()

SCHEMA = """CREATE TABLE paper_bet(
    event_id TEXT, sport TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
    point REAL, player TEXT, label TEXT, commence_time TEXT, book TEXT, odds REAL,
    ev REAL, stake REAL, placed_at TEXT, tier TEXT, conf REAL,
    completed INTEGER DEFAULT 0, won INTEGER, result TEXT, pnl REAL, settled_at TEXT)"""


def db_():
    db = sqlite3.connect(":memory:")
    db.execute(SCHEMA)
    return db


def add(db, i, *, market="h2h", ev_=0.06, placed=None, won=1, odds=2.40,
        completed=1, tier="High"):
    """One graded bet. odds 2.40 -> breakeven 41.7%, i.e. the real cell's BE."""
    pnl = (odds - 1) * 2.0 if won else -2.0
    db.execute("INSERT INTO paper_bet (event_id, market, side, odds, ev, stake,"
               " placed_at, tier, completed, won, pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (f"E{i}", market, "A", odds, ev_, 2.0, placed or AFTER, tier,
                completed, won if completed else None, pnl if completed else None))
    db.commit()


def fill(db, wins, losses, **kw):
    for i in range(wins):
        add(db, f"w{i}", won=1, **kw)
    for i in range(losses):
        add(db, f"l{i}", won=0, **kw)


# ---------- cohort selection ----------

def test_only_counts_bets_placed_on_or_after_the_flag_date():
    db = db_()
    fill(db, 0, 6, placed=BEFORE)          # in-sample: must NOT be re-counted
    fill(db, 2, 3, placed=AFTER)
    graded, _ = ev.cohort(db, SINCE)
    assert len(graded) == 5


def test_selects_the_cell_via_core_not_hand_written_sql():
    """Everything that is not ML-at-EV>=threshold must be excluded — by the same
    predicate that decides the alert, so the two can never disagree."""
    db = db_()
    fill(db, 0, 3)                                     # ML @ 6% -> in
    fill(db, 0, 4, market="total")                     # total @ 6% -> out
    fill(db, 0, 5, market="spread")                    # spread @ 6% -> out
    fill(db, 0, 6, ev_=0.04)                           # ML @ 4% -> out
    graded, _ = ev.cohort(db, SINCE)
    assert len(graded) == 3
    assert all(core.is_ev_watch(r[0], r[1]) for r in graded)


def test_pending_bets_are_reported_but_not_graded():
    db = db_()
    fill(db, 1, 2)
    add(db, "p1", completed=0)
    graded, pending = ev.cohort(db, SINCE)
    assert (len(graded), pending) == (3, 1)


# ---------- the verdict boundaries ----------

def test_thin_sample_never_yields_a_call():
    """The failure mode that matters: 3 losses in a row 'confirming' the cut."""
    db = db_()
    fill(db, 0, 3)
    graded, _ = ev.cohort(db, SINCE)
    head, _ = ev.verdict(ev.stats(graded))
    assert "보류" in head and "표본 부족" in head


def test_zero_settled_is_handled():
    head, _ = ev.verdict(ev.stats([]))
    assert "보류" in head


def test_win_rate_far_under_breakeven_calls_for_the_cut():
    db = db_()
    fill(db, 2, 18)                        # WR 10% vs BE 41.7%
    graded, _ = ev.cohort(db, SINCE)
    s = ev.stats(graded)
    assert s["n"] >= ev.MIN_N
    head, _ = ev.verdict(s)
    assert "하드 컷" in head


def test_win_rate_above_breakeven_calls_for_release():
    db = db_()
    fill(db, 12, 6)                        # WR 66.7% vs BE 41.7%
    graded, _ = ev.cohort(db, SINCE)
    head, _ = ev.verdict(ev.stats(graded))
    assert "재현 안 됨" in head


def test_marginal_shortfall_extends_observation_instead_of_cutting():
    db = db_()
    fill(db, 8, 12)                        # WR 40.0% vs BE 41.7% -> 1.7pp short
    graded, _ = ev.cohort(db, SINCE)
    head, _ = ev.verdict(ev.stats(graded))
    assert "관찰 연장" in head and "하드 컷" not in head


# ---------- the report ----------

def test_report_quotes_the_in_sample_baseline_so_it_stands_alone():
    db = db_()
    fill(db, 1, 14)
    text = ev.block(db, AFTER)
    assert "기준선" in text and "4-21" in text


def test_closing_report_tells_you_to_switch_the_timer_off():
    db = db_()
    fill(db, 1, 14)
    assert "systemctl disable" in ev.block(db, ev.LAST_REPORT)
    assert "systemctl disable" not in ev.block(db, ev.FIRST_REPORT)


def test_report_states_what_holding_saved_or_cost():
    db = db_()
    fill(db, 0, 10)                        # all losses -> holding saved money
    assert "아낀 금액" in ev.block(db, AFTER)
    db2 = db_()
    fill(db2, 10, 0)                       # all wins -> holding cost money
    assert "놓친 금액" in ev.block(db2, AFTER)
