# tests/test_softbook_live_grade.py
"""Live in-game grading: project an open prop bet's status from the player's
current stat vs the line. Pure math + an injected stat fetcher."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_live_grade as lg  # noqa: E402


def test_project_over_winning_when_stat_reaches_line():
    out = lg.project("Over", 1.5, current=2, game_state="Mid 2nd")
    assert out["live_status"] == "winning"
    assert out["progress"]["current"] == 2 and out["progress"]["needed"] == 1.5


def test_project_over_pending_when_below_line():
    out = lg.project("Over", 1.5, current=0, game_state="Mid 2nd")
    assert out["live_status"] == "losing"          # below line, game ongoing
    assert out["progress"]["remaining"] == 2       # ceil(1.5)-0 to clear


def test_project_under_winning_while_below_line():
    out = lg.project("Under", 1.5, current=1, game_state="Top 5th")
    assert out["live_status"] == "winning"


def test_project_returns_pending_when_stat_unknown():
    out = lg.project("Over", 1.5, current=None, game_state=None)
    assert out["live_status"] == "pending"


def test_grade_open_bets_updates_db():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,book,"
               "fill_odds,stake,settled) VALUES ('E1','baseball_mlb',"
               "'batter_total_bases','Over',1.5,'Brandon Nimmo','FanDuel',2.2,2.0,0)")
    db.commit()

    def fake_stat(sport, event_id, player, market):
        return 2, "Mid 2nd"        # current total bases, game state

    n = lg.grade_open_bets(db, fetch_stat=fake_stat)
    assert n == 1
    row = db.execute("SELECT live_status, live_progress FROM app_bet").fetchone()
    assert row[0] == "winning"
    assert json.loads(row[1])["current"] == 2


def test_grade_skips_settled_bets():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,settled)"
               " VALUES ('E1','baseball_mlb','batter_total_bases','Over',1.5,'X',1)")
    db.commit()
    assert lg.grade_open_bets(db, fetch_stat=lambda *a: (5, "Final")) == 0


def test_grade_skips_not_placed_bets():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,settled,"
               "not_placed) VALUES ('E1','baseball_mlb','batter_total_bases',"
               "'Over',1.5,'Brandon Nimmo',0,1)")
    db.commit()
    assert lg.grade_open_bets(db, fetch_stat=lambda *a: (5, "Final")) == 0
