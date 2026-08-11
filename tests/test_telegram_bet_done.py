"""Marking a pick bet stamps its original Telegram message.

2026-08-06, observed: the user bet a pick at 1:30 PM (Caesars −115, marked bet
in the app) and bet it AGAIN at 2:20 PM at DraftKings, at −119. Nothing was
sent at 2:20. The second bet came from the 1:30 Telegram message, which sits in
chat history reading "🟣 BET · 베팅" forever — the app hides a bet-marked pick,
but the message that pitched it never changes.

Now marking a pick bet edits the original message to lead with "✅ 베팅 완료".
That requires the sent text (editMessageText replaces the whole body), so
alerts store alert_text; rows from before the column existed fall back to a
reply anchored under the message — still a visible stamp, never silence.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_recorder as rec  # noqa: E402

PK = ("E1", "h2h", "Seattle Mariners", 0.0, "")


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(c)
    return c


def _seed(db, message_id=15309, alert_text="🟣 Med +EV 즉시 알림\n…원문…"):
    db.execute("INSERT INTO high_alerted(event_id,market,side,point,player,"
               "alerted_at,book,odds,message_id,alert_text)"
               " VALUES (?,?,?,?,?,'t','Caesars',1.87,?,?)",
               (*PK, message_id, alert_text))
    db.commit()


@pytest.fixture
def tg(monkeypatch):
    calls = []
    monkeypatch.setattr(rec, "_telegram_call",
                        lambda method, payload: calls.append((method, payload)) or True)
    return calls


# ── the stamp ─────────────────────────────────────────────────────────

def test_it_edits_the_original_message(db, tg):
    _seed(db)
    assert rec.mark_bet_done(db, *PK, note="Caesars -115") is True
    method, payload = tg[0]
    assert method == "editMessageText"
    assert payload["message_id"] == 15309
    assert payload["text"].startswith("✅ 베팅 완료 — Caesars -115")


def test_the_original_body_survives_the_edit(db, tg):
    """editMessageText replaces the whole text — the stamp must carry the
    original along, reply-hint included (a fill-odds reply is still valid)."""
    _seed(db, alert_text="ORIGINAL BODY")
    rec.mark_bet_done(db, *PK, note="x")
    assert "ORIGINAL BODY" in tg[0][1]["text"]


def test_a_pre_column_row_gets_a_reply_instead(db, tg):
    """Alerts sent before alert_text existed: no body to edit with, so anchor a
    reply under the message — a visible stamp either way."""
    _seed(db, alert_text=None)
    assert rec.mark_bet_done(db, *PK, note="Caesars -115") is True
    method, payload = tg[0]
    assert method == "sendMessage"
    assert payload["reply_to_message_id"] == 15309
    assert "✅ 베팅 완료" in payload["text"]


# ── when there is nothing to stamp ────────────────────────────────────

def test_no_alert_row_is_a_quiet_false(db, tg):
    """Picks bet from the app that never alerted (e.g. found via the feed
    before any Telegram send) have no message to stamp."""
    assert rec.mark_bet_done(db, *PK, note="x") is False
    assert tg == []


def test_a_failed_send_row_is_skipped(db, tg):
    """message_id NULL = the original send failed; nothing exists to edit."""
    _seed(db, message_id=None)
    assert rec.mark_bet_done(db, *PK, note="x") is False
    assert tg == []


def test_it_never_raises_even_on_a_broken_db(tg):
    """Runs inside POST /bets — a hiccup here must not fail the user's bet."""
    empty = sqlite3.connect(":memory:")            # no high_alerted table at all
    assert rec.mark_bet_done(empty, *PK, note="x") is False


# ── the column migration ──────────────────────────────────────────────

def test_alert_text_column_is_migrated_idempotently():
    c = sqlite3.connect(":memory:")
    c.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(c)
    rec._ensure_alert_cols(c)                      # second run must not raise
    cols = {r[1] for r in c.execute("PRAGMA table_info(high_alerted)")}
    assert "alert_text" in cols


def test_new_alerts_store_their_sent_text(monkeypatch):
    """The stamp can only edit what was kept — alert_new_high must persist the
    body it sent."""
    ev = sqlite3.connect(":memory:")
    ev.execute(rec.SCHEMA)
    ev.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(ev)
    b = {"event_id": "E1", "market": "h2h", "side": "Seattle Mariners",
         "point": None, "player": None, "home": "Detroit Tigers",
         "away": "Seattle Mariners", "label": "Seattle Mariners ML",
         "book": "Caesars", "odds": 1.87, "ev": 0.02, "n_ev": 3, "n_books": 8,
         "tier": "Med", "sport": "baseball_mlb",
         "commence_time": "2099-01-01T00:00:00Z"}
    ev.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               ("2026-08-06T17:30:00+00:00", b["sport"], b["event_id"],
                b["commence_time"], b["home"], b["away"], b["market"], b["side"],
                b["point"], "", 0.5, 2.0, b["book"], b["odds"], b["ev"],
                b["n_books"], b["n_ev"], 47.0, b["tier"]))
    ev.commit()
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or 777)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    monkeypatch.setattr(rec, "actioned_picks", frozenset)
    rec.alert_new_high(ev, [b], "2026-08-06T17:30:00+00:00")
    stored = ev.execute("SELECT message_id, alert_text FROM high_alerted").fetchone()
    assert stored[0] == 777
    assert stored[1] == sent[0]
