import os
import sqlite3


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    db = adb.connect()
    adb.init_schema(db)          # idempotent: also runs the column ALTERs
    adb.init_schema(db)          # second call must not raise (columns already present)
    return adb, db


def test_init_schema_adds_price_columns_and_dedup_table(tmp_path, monkeypatch):
    adb, db = _fresh_db(tmp_path, monkeypatch)
    cols = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    assert {"current_best_odds", "last_odds_check_at"} <= cols
    db.execute("SELECT 1 FROM pushed_price_move LIMIT 1")


def test_record_current_odds_and_pricing_helpers(tmp_path, monkeypatch):
    adb, db = _fresh_db(tmp_path, monkeypatch)
    bid = adb.insert_app_bet(db, {
        "event_id": "EVT1", "market": "h2h", "side": "COL", "book": "FanDuel",
        "fill_odds": 2.0, "stake": 5.0, "placed_at": "2026-06-13T00:00:00Z", "source": "one_tap",
    })
    bets = adb.open_bets_for_pricing(db, limit=15)
    assert any(b["id"] == bid and b["fill_odds"] == 2.0 for b in bets)
    adb.record_current_odds(db, bid, 1.8, "2026-06-13T00:05:00Z")
    row = db.execute("SELECT current_best_odds, last_odds_check_at FROM app_bet WHERE id=?", (bid,)).fetchone()
    assert row[0] == 1.8 and row[1] == "2026-06-13T00:05:00Z"
    assert adb.price_move_pushed(db, bid, "cashout") is False
    adb.mark_price_move_pushed(db, bid, "cashout", "2026-06-13T00:05:00Z")
    assert adb.price_move_pushed(db, bid, "cashout") is True
    assert adb.price_move_pushed(db, bid, "move") is False


def test_project_price_move_classifies_by_direction():
    import softbook_push_worker as w
    assert w.project_price_move(2.0, 1.8)["kind"] == "cashout"
    assert w.project_price_move(2.0, 2.2)["kind"] == "move"
    assert w.project_price_move(2.0, 2.05)["kind"] is None
    assert w.project_price_move(None, 1.8)["kind"] is None
    assert w.project_price_move(2.0, None)["kind"] is None


def test_price_move_messages_pushes_once_per_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    import softbook_push_worker as w
    db = adb.connect(); adb.init_schema(db)
    bid = adb.insert_app_bet(db, {
        "event_id": "EVT1", "sport": "baseball_mlb", "market": "h2h", "side": "COL",
        "book": "FanDuel", "fill_odds": 2.0, "stake": 5.0,
        "placed_at": "2026-06-13T00:00:00Z", "source": "one_tap", "label": "COL ML",
    })
    db.execute("INSERT INTO push_token(token, updated_at, active) VALUES('TOK', 't', 1)")
    db.commit()
    fetch = lambda *a, **k: 1.8
    msgs = w._price_move_messages(db, ["TOK"], "2026-06-13T00:05:00Z", fetch_odds=fetch)
    assert len(msgs) == 1
    assert msgs[0]["categoryId"] == w.CATEGORY_PRICE_MOVE
    assert msgs[0]["data"] == {"app_bet_id": bid}
    assert db.execute("SELECT current_best_odds FROM app_bet WHERE id=?", (bid,)).fetchone()[0] == 1.8
    msgs2 = w._price_move_messages(db, ["TOK"], "2026-06-13T00:10:00Z", fetch_odds=fetch)
    assert msgs2 == []
