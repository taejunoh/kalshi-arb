# tests/test_softbook_app_db.py
"""App-side persistence for the +EV companion app: pick_state (bet/dismissed
overrides on the live feed), app_bet (captured bets = calendar/PnL source of
truth), filter_prefs, push_token. Plus a one-time real_fill -> app_bet migration."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_db as adb  # noqa: E402


def mem():
    db = sqlite3.connect(":memory:")
    adb.init_schema(db)
    return db


def test_init_schema_creates_all_tables():
    db = mem()
    names = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pick_state", "app_bet", "filter_prefs", "push_token"} <= names


def test_init_schema_is_idempotent():
    db = mem()
    adb.init_schema(db)  # second call must not raise
    assert db.execute("SELECT count(*) FROM app_bet").fetchone() == (0,)


def test_set_pick_state_upsert():
    db = mem()
    pick = ("E1", "total", "Under", 169.5, "")
    adb.set_pick_state(db, pick, "dismissed", "2026-06-12T00:00:00+00:00")
    adb.set_pick_state(db, pick, "bet", "2026-06-12T00:05:00+00:00")  # overwrite
    rows = db.execute("SELECT state FROM pick_state").fetchall()
    assert rows == [("bet",)]


def test_pick_states_returns_keyed_dict():
    db = mem()
    adb.set_pick_state(db, ("E1", "h2h", "NYM", 0, ""), "bet", "t")
    adb.set_pick_state(db, ("E2", "total", "Over", 8.5, ""), "dismissed", "t")
    states = adb.pick_states(db)
    assert states[("E1", "h2h", "NYM", 0.0, "")] == "bet"
    assert states[("E2", "total", "Over", 8.5, "")] == "dismissed"


def test_insert_app_bet_returns_id_and_persists():
    db = mem()
    bet = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
           "commence_time": "2026-06-12T23:10:00Z", "market": "batter_total_bases",
           "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
           "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
           "rec_odds": 2.2, "fill_odds": 2.2, "stake": 2.0,
           "placed_at": "2026-06-12T18:00:00+00:00", "source": "one_tap"}
    bid = adb.insert_app_bet(db, bet)
    assert isinstance(bid, int)
    row = db.execute("SELECT player, book, fill_odds, stake, source, live_status,"
                     " settled FROM app_bet WHERE id=?", (bid,)).fetchone()
    assert row == ("Brandon Nimmo", "theScore", 2.2, 2.0, "one_tap", "pending", 0)


def test_migrate_real_fill_copies_filled_not_skipped():
    db = mem()
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY, event_id TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL, skipped INTEGER,
        raw_reply TEXT, replied_at TEXT, cashed_out INTEGER, cashout_amount REAL)""")
    db.execute("INSERT INTO real_fill VALUES (1,'E1','total','Under',169.5,'',"
               "'BetMGM',2.06,'FanDuel',1.98,5.0,0,'1.98 fd','t',0,NULL)")
    db.execute("INSERT INTO real_fill VALUES (2,'E2','h2h','NYM',0,'',"
               "'BetMGM',1.9,NULL,NULL,NULL,1,'skip','t',0,NULL)")  # skip -> excluded
    n = adb.migrate_real_fill(db)
    assert n == 1
    row = db.execute("SELECT event_id, book, fill_odds, stake, source, rec_odds"
                     " FROM app_bet").fetchone()
    assert row == ("E1", "FanDuel", 1.98, 5.0, "telegram", 2.06)


def test_migrate_real_fill_is_idempotent():
    db = mem()
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY, event_id TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL, skipped INTEGER,
        raw_reply TEXT, replied_at TEXT, cashed_out INTEGER, cashout_amount REAL)""")
    db.execute("INSERT INTO real_fill VALUES (1,'E1','total','Under',169.5,'',"
               "'BetMGM',2.06,'FanDuel',1.98,5.0,0,'1.98 fd','t',0,NULL)")
    adb.migrate_real_fill(db)
    adb.migrate_real_fill(db)  # second run must not duplicate
    assert db.execute("SELECT count(*) FROM app_bet").fetchone() == (1,)
