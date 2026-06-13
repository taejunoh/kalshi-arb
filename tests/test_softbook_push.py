# tests/test_softbook_push.py
"""Expo Push API sender. Builds the message payload, posts to exp.host, prunes
tokens that come back DeviceNotRegistered."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_push as push  # noqa: E402


def test_build_message_has_required_fields():
    msg = push.build_message("ExponentPushToken[x]", "New High",
                             "Nimmo TB Over 1.5 · EV 7.4%",
                             data={"pick_id": "E1|..."}, category="high_pick")
    assert msg["to"] == "ExponentPushToken[x]"
    assert msg["title"] == "New High"
    assert msg["categoryId"] == "high_pick"
    assert msg["data"]["pick_id"] == "E1|..."


def test_send_posts_chunked_and_returns_tickets():
    posted = {}

    def fake_post(url, json):
        posted["url"] = url
        posted["json"] = json
        return {"data": [{"status": "ok", "id": "T1"}]}

    tickets = push.send([push.build_message("ExponentPushToken[x]", "t", "b")],
                        post=fake_post)
    assert posted["url"] == "https://exp.host/--/api/v2/push/send"
    assert tickets == [{"status": "ok", "id": "T1"}]


def test_prune_marks_device_not_registered_inactive():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE push_token(token TEXT PRIMARY KEY, updated_at TEXT,"
               " active INTEGER DEFAULT 1)")
    db.execute("INSERT INTO push_token VALUES ('ExponentPushToken[dead]','t',1)")
    receipts = {"R1": {"status": "error",
                       "details": {"error": "DeviceNotRegistered"}}}
    push.prune_tokens(db, {"R1": "ExponentPushToken[dead]"}, receipts)
    assert db.execute("SELECT active FROM push_token").fetchone() == (0,)


def test_active_tokens_reads_only_active():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE push_token(token TEXT PRIMARY KEY, updated_at TEXT,"
               " active INTEGER DEFAULT 1)")
    db.execute("INSERT INTO push_token VALUES ('A','t',1)")
    db.execute("INSERT INTO push_token VALUES ('B','t',0)")
    assert push.active_tokens(db) == ["A"]


import softbook_push_worker as worker  # noqa: E402


def _worker_dbs():
    ev = sqlite3.connect(":memory:")
    ev.execute("""CREATE TABLE softbook_ev(
        recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT, home TEXT,
        away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        pinnacle_fair_prob REAL, pinnacle_price REAL, best_soft_book TEXT,
        best_soft_price REAL, snapshot_ev REAL, n_books INTEGER, n_ev INTEGER,
        conf REAL, tier TEXT)""")
    # datetime('now') so the worker's '-1 day' recency filter always includes it.
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'batter_total_bases','Over',1.5,'Brandon Nimmo',0.45,2.0,"
               "'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit()
    pdb = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(pdb)
    pdb.execute("INSERT INTO push_token VALUES ('ExponentPushToken[x]','t',1)")
    pdb.commit()
    return ev, pdb


def test_worker_pushes_new_high_once():
    ev, pdb = _worker_dbs()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    assert len(sent) == 1
    assert "Nimmo" in sent[0]["title"] + sent[0]["body"]
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])  # dedup
    assert len(sent) == 1


def test_worker_alerts_first_entry_only():
    """Only first-entry picks push; late-crossers (earliest snapshot was not High) are
    deduped but never alerted."""
    ev, pdb = _worker_dbs()
    ev.execute("DELETE FROM softbook_ev")
    latest = ev.execute("SELECT datetime('now')").fetchone()[0]
    earlier = ev.execute("SELECT datetime('now','-40 minutes')").fetchone()[0]
    cols = ("(recorded_at,sport,event_id,commence_time,home,away,market,side,point,"
            "player,pinnacle_fair_prob,pinnacle_price,best_soft_book,best_soft_price,"
            "snapshot_ev,n_books,n_ev,conf,tier)")
    q = "INSERT INTO softbook_ev " + cols + " VALUES (" + ",".join("?" * 19) + ")"
    # E1: single High snapshot -> first-entry -> SHOULD alert.
    ev.execute(q, (latest, "baseball_mlb", "E1", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "batter_total_bases", "Over", 1.5, "Brandon Nimmo", 0.45, 2.0,
                   "theScore", 2.2, 0.074, 9, 5, 60, "High"))
    # E2: earliest snapshot was Med (late-crosser) -> should NOT alert.
    ev.execute(q, (earlier, "baseball_mlb", "E2", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "h2h", "NYM", 0, "", 0.4, 2.0, "FanDuel", 2.1, 0.03, 8, 5, 55, "Med"))
    ev.execute(q, (latest, "baseball_mlb", "E2", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "h2h", "NYM", 0, "", 0.4, 2.0, "FanDuel", 2.1, 0.03, 8, 5, 55, "High"))
    ev.commit()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    assert len(sent) == 1                 # only the first-entry pick
    assert "Nimmo" in sent[0]["body"]     # E1 (first-entry), not E2 (late-crosser)


def test_worker_pushes_settlement_result_once():
    ev, pdb = _worker_dbs()
    pdb.execute("INSERT INTO app_bet(event_id,market,side,point,player,label,book,"
                "fill_odds,stake,settled,won,pnl,settled_at) VALUES "
                "('E1','batter_total_bases','Over',1.5,'Brandon Nimmo','x','FanDuel',"
                "2.2,2.0,1,1,2.4,'2026-06-12T22:00:00+00:00')")
    pdb.commit()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    settle = [m for m in sent if m.get("categoryId") == "settle"]
    assert len(settle) == 1 and "win" in settle[0]["body"].lower() or "적중" in settle[0]["body"]
