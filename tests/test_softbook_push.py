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
