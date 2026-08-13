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


def test_app_bet_has_not_placed_columns_defaulting_off():
    db = mem()
    cols = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    assert {"not_placed", "not_placed_at"} <= cols
    adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert db.execute("SELECT not_placed, not_placed_at FROM app_bet"
                      ).fetchone() == (0, None)


def test_ensure_columns_upgrades_a_pre_existing_table():
    """The column lands on an EC2 db that already has rows — the ALTER path,
    not the CREATE path."""
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE app_bet(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
        market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
        book TEXT, rec_odds REAL, fill_odds REAL, stake REAL,
        placed_at TEXT, source TEXT,
        live_status TEXT DEFAULT 'pending', live_progress TEXT,
        settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT)""")
    db.execute("INSERT INTO app_bet(event_id, market, side) VALUES ('E1','h2h','NYM')")
    db.commit()
    adb.init_schema(db)
    assert db.execute("SELECT not_placed FROM app_bet").fetchone() == (0,)


def test_get_app_bet_returns_a_dict_or_none():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM",
                                  "stake": 20.0, "book": "FanDuel"})
    row = adb.get_app_bet(db, bid)
    assert row["id"] == bid and row["book"] == "FanDuel" and row["stake"] == 20.0
    assert row["not_placed"] == 0
    assert adb.get_app_bet(db, bid + 999) is None


def test_update_app_bet_writes_only_the_three_editable_columns():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM",
                                  "book": "FanDuel", "stake": 20.0, "fill_odds": 2.1,
                                  "rec_odds": 2.2})
    n = adb.update_app_bet(db, bid, {"book": "DraftKings", "stake": 25.0,
                                     "fill_odds": 2.05, "rec_odds": 9.9,
                                     "market": "spread"})
    assert n == 1
    row = adb.get_app_bet(db, bid)
    assert (row["book"], row["stake"], row["fill_odds"]) == ("DraftKings", 25.0, 2.05)
    assert row["rec_odds"] == 2.2 and row["market"] == "h2h"   # untouched


def test_update_app_bet_with_no_editable_field_is_a_noop():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert adb.update_app_bet(db, bid, {"market": "spread"}) == 0


def test_set_not_placed_flips_the_flag_and_stamps_the_time():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert adb.set_not_placed(db, bid, True, "2026-08-12T10:00:00+00:00") is True
    row = adb.get_app_bet(db, bid)
    assert row["not_placed"] == 1 and row["not_placed_at"] == "2026-08-12T10:00:00+00:00"
    adb.set_not_placed(db, bid, False, None)
    row = adb.get_app_bet(db, bid)
    assert row["not_placed"] == 0 and row["not_placed_at"] is None
    assert adb.set_not_placed(db, bid + 999, True, "x") is False


def test_live_bets_for_pick_counts_only_owned_rows_on_that_pick():
    """The question unmark asks before dropping a pick's feed override: is any
    OTHER bet on this pick still owned? not_placed rows are disowned and do not
    count; a different pick's rows are a different question."""
    db = mem()
    pick = {"event_id": "E1", "market": "spread", "side": "NYM", "point": -1.5}
    b1 = adb.insert_app_bet(db, dict(pick))
    b2 = adb.insert_app_bet(db, dict(pick))
    adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    key = ("E1", "spread", "NYM", -1.5, "")
    assert adb.live_bets_for_pick(db, key) == 2
    # exclude_id answers "is there ANOTHER one", the form unmark needs.
    assert adb.live_bets_for_pick(db, key, exclude_id=b1) == 1
    adb.set_not_placed(db, b2, True, "t1")
    assert adb.live_bets_for_pick(db, key, exclude_id=b1) == 0
    assert adb.live_bets_for_pick(db, key) == 1            # b1 itself still owned
    adb.set_not_placed(db, b1, True, "t1")
    assert adb.live_bets_for_pick(db, key) == 0
    # A pick with no rows at all.
    assert adb.live_bets_for_pick(db, ("E9", "h2h", "MIA", 0, "")) == 0


def test_clear_bet_state_removes_bet_only_and_leaves_dismissed():
    """A dismissed row is a preference; a bet row is what we are undoing here.
    Clearing one must never sweep up the other."""
    db = mem()
    bet = ("E1", "h2h", "NYM", 0, "")
    other = ("E2", "h2h", "MIA", 0, "")
    adb.set_pick_state(db, bet, "bet", "t1")
    adb.set_pick_state(db, other, "dismissed", "t1")
    assert adb.clear_bet_state(db, bet) == 1
    states = adb.pick_states(db)
    assert ("E1", "h2h", "NYM", 0.0, "") not in states
    assert states[("E2", "h2h", "MIA", 0.0, "")] == "dismissed"
    assert adb.clear_bet_state(db, bet) == 0          # idempotent
