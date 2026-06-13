"""First-entry vs late-crosser alert tag — added 2026-06-13.

A High pick is FIRST-ENTRY if its (event, market, player) opportunity was ALREADY
High at its earliest recorded snapshot — a standing soft-book mispricing you can
grab immediately. The dashboard's First-entry High ledger (paper_bet, tier=High)
settled +16.6% ROI on that subset.

A LATE-CROSSER drifted into High only after the line moved (earliest snapshot was
Med/Low). That subset — every High-trigger pick that is NOT first-entry — settled
~breakeven (−0.9%), pure dilution. So the alert flags it 권장 스킵.

Keyed on (event, market, player) to match the dashboard's First-entry ledger
(side/point movement is the _dup_note's job, not this tag's).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_recorder as rec                             # noqa: E402

HOME, AWAY = "Detroit Tigers", "Seattle Mariners"
CT = "2026-06-13T23:10:00Z"


def bet(market="pitcher_strikeouts", side="Over", point=5.5, player="Tarik Skubal",
        ev=0.03, book="FanDuel", odds=2.05, eid="E1"):
    return {"event_id": eid, "sport": "baseball_mlb", "commence_time": CT,
            "home": HOME, "away": AWAY, "market": market, "side": side,
            "point": point, "player": player, "book": book, "odds": odds,
            "ev": ev, "n_books": 8, "n_ev": 6, "tier": "High",
            "label": f"{player} {side} {point}"}


def db_():
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    db.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(db)
    return db


def snap(db, b, rec_at, tier):
    db.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               (rec_at, b["sport"], b["event_id"], b["commence_time"], b["home"],
                b["away"], b["market"], b["side"], b["point"], b.get("player") or "",
                0.5, 2.0, b["book"], b["odds"], b["ev"], 8, 6, 61.0, tier))


# ---------- core: _first_entry_note logic ----------

def test_first_entry_when_earliest_snapshot_is_high():
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "High")   # High from first sight
    note = rec._first_entry_note(db, b)
    assert "first-entry" in note and "BET" in note
    assert "late-crosser" not in note


def test_late_crosser_when_earliest_snapshot_is_med():
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")    # only ever Med so far
    note = rec._first_entry_note(db, b)
    assert "late-crosser" in note and "SKIP" in note


def test_late_crosser_even_if_a_later_snapshot_is_high():
    # the real dilution case: Med/Low first, drifts to High after the line moves
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Low")    # earliest = Low
    snap(db, b, "2026-06-13T16:20:00+00:00", "Med")
    snap(db, b, "2026-06-13T18:40:00+00:00", "High")   # crossed later
    note = rec._first_entry_note(db, b)
    assert "late-crosser" in note and "SKIP" in note


def test_keyed_on_event_market_player_independent():
    # a different player's earliest snapshot must not bleed into this pick's tag
    db = db_()
    other = bet(player="Other Pitcher")
    snap(db, other, "2026-06-13T14:00:00+00:00", "Med")
    b = bet(player="Tarik Skubal")
    snap(db, b, "2026-06-13T15:00:00+00:00", "High")
    assert "first-entry" in rec._first_entry_note(db, b)


def test_brand_new_pick_high_on_first_scan_is_first_entry():
    # record() writes this scan's snapshot before alert_new_high runs, so a pick
    # that is High on its very first appearance has exactly one (High) snapshot
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    assert "first-entry" in rec._first_entry_note(db, b)


# ---------- wiring: tag actually lands in the alert message ----------

def test_tag_is_wired_into_send_bet_alert(monkeypatch):
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    captured = {}
    monkeypatch.setattr(rec, "send_telegram", lambda text: captured.setdefault("t", text) or 42)
    monkeypatch.setattr(rec.time, "sleep", lambda *a: None)
    rec._send_bet_alert(db, b, "🟣 High +EV 즉시 알림 (pre-game · bet small)")
    assert "first-entry" in captured["t"] and "BET" in captured["t"]


def test_late_crosser_tag_wired_into_message(monkeypatch):
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")
    captured = {}
    monkeypatch.setattr(rec, "send_telegram", lambda text: captured.setdefault("t", text) or 42)
    monkeypatch.setattr(rec.time, "sleep", lambda *a: None)
    rec._send_bet_alert(db, b, "🟣 High +EV 즉시 알림")
    assert "late-crosser" in captured["t"] and "SKIP" in captured["t"]


# ---------- no-contradiction: 'bet small' lives ONLY in the first-entry verdict ----------

def test_first_entry_verdict_carries_bet_action():
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    assert "bet small" in rec._first_entry_note(db, b)


def test_late_crosser_verdict_has_no_bet_action():
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "bet small" not in note


def _capture_alert(monkeypatch):
    cap = {}
    monkeypatch.setattr(rec, "send_telegram", lambda text: cap.setdefault("t", text) or 1)
    monkeypatch.setattr(rec.time, "sleep", lambda *a: None)
    return cap


def test_alert_late_crosser_has_no_bet_small_contradiction(monkeypatch):
    # the real confusion: header said 'bet small' while the verdict said SKIP.
    # On a late-crosser the whole message must contain NO 'bet small' instruction.
    db = db_()
    b = bet()                                            # tier High -> alerts
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")      # but late-crosser
    cap = _capture_alert(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T20:00:00+00:00")
    assert "SKIP" in cap["t"]
    assert "bet small" not in cap["t"]                   # no 'bet' instruction on a skip
    assert "(pre-game)" in cap["t"]                      # neutral header


def test_alert_first_entry_keeps_bet_small_in_verdict(monkeypatch):
    db = db_()
    b = bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")     # first-entry
    cap = _capture_alert(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T21:00:00+00:00")
    assert "🟣 BET" in cap["t"] and "bet small" in cap["t"]
    assert "pre-game · bet small" not in cap["t"]        # header no longer carries it
