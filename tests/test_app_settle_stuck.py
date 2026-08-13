"""An ungradeable bet must stop being retried in silence.

settle_app_bets skips what it cannot grade and tries again next run, every 15
minutes, forever. That is right for a few hours and wrong after a few days,
because both result sources have a horizon: the Odds API /scores call asks for
daysFrom=3, and the ESPN/Naver graders stop returning old fixtures too. Past it
the bet is not "not settled yet", it is unsettleable.

Found 2026-08-05: a KBO bet placed 07-21 had been retried every 15 minutes for
15 days, next to a boxing bet and two WNBA bets from 07-17/18. Nothing said so.
They also sat in open-bet queries the whole time, holding slots in the
top-15-by-stake list the price-move pricer paid API credits for.

Not auto-resolved: grading from a guess writes a wrong number into a ledger of
real money, and ledger rows are records, not estimates.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_app_settle as settle  # noqa: E402


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_bet(id INTEGER PRIMARY KEY, sport TEXT, label TEXT,"
              " book TEXT, stake REAL, commence_time TEXT, settled INT,"
              " not_placed INT DEFAULT 0)")
    return c


def _bet(db, bid, *, hours_ago, settled=0, sport="baseball_kbo", label="KT Wiz ML"):
    db.execute(
        "INSERT INTO app_bet VALUES (?,?,?,'BetRivers',25.0,"
        " strftime('%Y-%m-%dT%H:%M:%SZ', datetime('now', ?)), ?, 0)",
        (bid, sport, label, f"-{hours_ago} hours", settled))
    db.commit()


# ── what counts as stuck ──────────────────────────────────────────────

def test_a_bet_past_the_horizon_is_stuck(db):
    _bet(db, 1, hours_ago=15 * 24)                 # the observed KBO bet
    assert [r["id"] for r in settle.stuck_bets(db)] == [1]


def test_a_recent_unsettled_bet_is_not_stuck(db):
    """Still inside every source's window — retrying is exactly right."""
    _bet(db, 1, hours_ago=5)
    assert settle.stuck_bets(db) == []


def test_the_boundary_is_the_source_horizon(db):
    _bet(db, 1, hours_ago=settle.STUCK_AFTER_H - 1)
    _bet(db, 2, hours_ago=settle.STUCK_AFTER_H + 1)
    assert [r["id"] for r in settle.stuck_bets(db)] == [2]


def test_settled_bets_are_never_stuck(db):
    _bet(db, 1, hours_ago=15 * 24, settled=1)
    assert settle.stuck_bets(db) == []


def test_rows_without_a_sport_or_start_are_skipped(db):
    """Legacy real_fill rows: ungradeable by design, not a new failure."""
    db.execute("INSERT INTO app_bet VALUES (1,NULL,'x','b',1,'2020-01-01T00:00:00Z',0,0)")
    db.execute("INSERT INTO app_bet VALUES (2,'baseball_kbo','y','b',1,NULL,0,0)")
    db.commit()
    assert settle.stuck_bets(db) == []


# ── reported once, not every 15 minutes ───────────────────────────────

def test_it_reports_what_is_stuck(db, tmp_path):
    _bet(db, 1, hours_ago=15 * 24)
    sent = []
    settle.report_stuck(db, notify=sent.append, state_path=str(tmp_path / "s"))
    assert len(sent) == 1
    assert "KT Wiz ML" in sent[0] and "baseball_kbo" in sent[0]


def test_a_stuck_bet_is_reported_once_not_every_run(db, tmp_path):
    """The condition never clears on its own and the timer runs every 15
    minutes — repeating would be a permanent alarm, which is the same as none."""
    _bet(db, 1, hours_ago=15 * 24)
    state = str(tmp_path / "s")
    sent = []
    settle.report_stuck(db, notify=sent.append, state_path=state)
    settle.report_stuck(db, notify=sent.append, state_path=state)
    settle.report_stuck(db, notify=sent.append, state_path=state)
    assert len(sent) == 1


def test_a_newly_stuck_bet_still_reports(db, tmp_path):
    """Reported-once must not become reported-never for the next one."""
    _bet(db, 1, hours_ago=15 * 24)
    state = str(tmp_path / "s")
    sent = []
    settle.report_stuck(db, notify=sent.append, state_path=state)
    _bet(db, 2, hours_ago=8 * 24, sport="boxing_boxing", label="Fighter ML")
    settle.report_stuck(db, notify=sent.append, state_path=state)
    assert len(sent) == 2
    assert "Fighter ML" in sent[1] and "KT Wiz" not in sent[1]


def test_nothing_stuck_sends_nothing(db, tmp_path):
    _bet(db, 1, hours_ago=2)
    sent = []
    assert settle.report_stuck(db, notify=sent.append, state_path=str(tmp_path / "s")) == []
    assert sent == []


def test_an_unwritable_state_file_still_reports(db, tmp_path):
    """A lost state file costs one repeat; failing to report costs the alert."""
    _bet(db, 1, hours_ago=15 * 24)
    sent = []
    settle.report_stuck(db, notify=sent.append, state_path=str(tmp_path / "no" / "such" / "s"))
    assert len(sent) == 1


def test_the_message_bounds_how_many_it_lists(db, tmp_path):
    for i in range(1, 26):
        _bet(db, i, hours_ago=15 * 24, label=f"Bet {i}")
    sent = []
    settle.report_stuck(db, notify=sent.append, state_path=str(tmp_path / "s"))
    assert "25" in sent[0]                       # the count is honest…
    assert sent[0].count("   • ") == 10          # …the list is bounded


def test_not_placed_bets_never_go_stuck(db):
    """An unsettleable bet the user already disowned is not an alarm."""
    db.execute("INSERT INTO app_bet(sport, label, book, stake, commence_time,"
               " settled, not_placed) VALUES ('baseball_kbo','x','FanDuel',20,"
               " datetime('now','-15 days'), 0, 1)")
    db.commit()
    assert settle.stuck_bets(db) == []
