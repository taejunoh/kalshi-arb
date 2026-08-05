"""The watch note must state the real record, not a frozen one.

The card shipped with "페이퍼 4승 21패" written into the JSX — the in-sample
figure as of 2026-07-22. Being a string literal it could never change, so by
08-05 the app was still quoting it while the forward sample had gone the other
way (3-2, +34%). The note now reads a live number.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _paper_db(path, rows):
    """rows: (placed_at, market, ev, odds, stake, pnl, won, completed)"""
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE paper_bet (
        placed_at TEXT, tier TEXT, market TEXT, ev REAL, odds REAL,
        stake REAL, pnl REAL, won INT, completed INT)""")
    c.executemany(
        "INSERT INTO paper_bet (placed_at, tier, market, ev, odds, stake, pnl,"
        " won, completed) VALUES (?,'High',?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    db = tmp_path / "paper.db"
    _paper_db(db, [
        # in-sample era: ML at EV>=5% losing (the cell that got flagged)
        ("2026-07-01", "h2h", 0.07, 2.4, 10.0, -10.0, 0, 1),
        ("2026-07-02", "h2h", 0.06, 2.4, 10.0, -10.0, 0, 1),
        ("2026-07-03", "h2h", 0.08, 2.4, 10.0, 14.0, 1, 1),
        # forward era: same cell winning
        ("2026-08-01", "h2h", 0.06, 2.2, 10.0, 12.0, 1, 1),
        ("2026-08-02", "h2h", 0.09, 2.2, 10.0, 12.0, 1, 1),
        # NOT the cell: ML below the EV bar, and a total at high EV
        ("2026-08-03", "h2h", 0.01, 2.0, 10.0, -10.0, 0, 1),
        ("2026-08-03", "total", 0.09, 2.0, 10.0, -10.0, 0, 1),
        # ungraded — must not count
        ("2026-08-04", "h2h", 0.07, 2.0, 10.0, None, None, 0),
    ])
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(db))
    # _enrich_first_entry opens the EV db; an empty file is enough since the
    # cohort lookup itself is monkeypatched in the test that needs it.
    ev = tmp_path / "ev.db"
    sqlite3.connect(ev).close()
    monkeypatch.setenv("SOFTBOOK_EV_DB", str(ev))
    for m in list(sys.modules):
        if m.startswith("softbook_"):
            del sys.modules[m]
    import softbook_app_api
    softbook_app_api._WATCH_CACHE.update(at=0.0, val=None)
    return softbook_app_api


def test_record_covers_the_whole_period_not_just_the_forward_window(api):
    """User's call 2026-08-05: the card states the TOTAL record. 3 in-sample +
    2 forward = 5 graded, 3 wins."""
    r = api._watch_record()
    assert r["n"] == 5
    assert (r["w"], r["l"]) == (3, 2)


def test_only_the_watch_cell_counts(api):
    """ML below the EV bar and non-ML at high EV are different cells — core
    .is_ev_watch decides, and this must not drift from it."""
    r = api._watch_record()
    assert r["n"] == 5, "a low-EV ML or a high-EV total leaked into the cohort"


def test_ungraded_bets_are_excluded(api):
    r = api._watch_record()
    assert r["n"] == 5, "an unsettled bet was counted as decided"


def test_breakeven_comes_from_the_actual_prices(api):
    """BE is the mean implied probability of the prices taken, not a constant."""
    r = api._watch_record()
    assert 40.0 < r["be"] < 48.0


def test_result_is_cached(api, monkeypatch):
    first = api._watch_record()
    # Break the DB path; a cached call must still answer.
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", "/nonexistent/paper.db")
    assert api._watch_record() == first


def test_missing_db_fails_soft(tmp_path, monkeypatch):
    """The note is decoration — it must never take the feed down."""
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "nope.db"))
    for m in list(sys.modules):
        if m.startswith("softbook_"):
            del sys.modules[m]
    import softbook_app_api as fresh
    fresh._WATCH_CACHE.update(at=0.0, val=None)
    assert fresh._watch_record() is None


def test_attached_only_to_watch_picks(api, monkeypatch):
    picks = [{"event_id": "e", "market": "h2h", "side": "s", "player": None,
              "ev": 0.07},
             {"event_id": "e2", "market": "total", "side": "Over", "player": None,
              "ev": 0.07}]
    monkeypatch.setattr(api.core, "market_first_entry_sides",
                        lambda *a, **k: (True, {"s", "Over"}))
    api._enrich_first_entry(picks)
    assert picks[0]["ev_watch"] is True and picks[0]["watch_record"]["n"] == 5
    assert picks[1]["ev_watch"] is False and "watch_record" not in picks[1]
