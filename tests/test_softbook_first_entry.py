"""Diagonal BET/SKIP alert tag (market type × entry timing) — 2026-06-13.

A pick is FIRST-ENTRY if its (event, market, player) was already High at its
earliest recorded snapshot (a standing mispricing); a LATE-CROSSER drifted into
High after the line moved. The realized edge is DIAGONAL (paper, settled, n=234):
  • GAME (ML/spread/total): first-entry +29.5% / late-crosser −7.2% → bet first-entry
  • PROPS:                  late-crosser +9.2% / first-entry −11.2% → bet late-crosser
So the tag is BET iff (game & first-entry) OR (prop & late-crosser), else SKIP.
'bet small' lives ONLY in the BET verdict so it never contradicts a SKIP.

Keyed on (event, market, player); prop vs game via core.PROP_SHORT.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_recorder as rec                             # noqa: E402

HOME, AWAY = "Detroit Tigers", "Seattle Mariners"
CT = "2026-06-13T23:10:00Z"


def prop_bet(market="pitcher_strikeouts", side="Over", point=5.5,
             player="Tarik Skubal", ev=0.03, book="FanDuel", odds=2.05, eid="E1"):
    return {"event_id": eid, "sport": "baseball_mlb", "commence_time": CT,
            "home": HOME, "away": AWAY, "market": market, "side": side,
            "point": point, "player": player, "book": book, "odds": odds,
            "ev": ev, "n_books": 8, "n_ev": 6, "tier": "High",
            "label": f"{player} {side} {point}"}


def game_bet(market="spread", side=HOME, point=-1.5, ev=0.03, book="FanDuel",
             odds=2.05, eid="G1"):
    return {"event_id": eid, "sport": "baseball_mlb", "commence_time": CT,
            "home": HOME, "away": AWAY, "market": market, "side": side,
            "point": point, "player": "", "book": book, "odds": odds,
            "ev": ev, "n_books": 8, "n_ev": 6, "tier": "High",
            "label": f"{side} {point}"}


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


# ---------- the 2×2 diagonal ----------

def test_game_first_entry_is_bet():
    db = db_()
    b = game_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "High")     # game, High from first sight
    note = rec._first_entry_note(db, b)
    assert "BET" in note and "first-entry game" in note
    assert "bet small" in note and "SKIP" not in note


def test_game_late_crosser_is_skip():
    db = db_()
    b = game_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")      # game, drifted to High later
    snap(db, b, "2026-06-13T18:40:00+00:00", "High")
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "late-crosser game" in note
    assert "bet small" not in note


def test_prop_late_crosser_is_bet():
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Low")      # prop, drifted to High later
    snap(db, b, "2026-06-13T18:40:00+00:00", "High")
    note = rec._first_entry_note(db, b)
    assert "BET" in note and "late-crosser prop" in note
    assert "bet small" in note


def test_prop_first_entry_is_skip():
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "High")     # prop, High from first sight
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "first-entry prop" in note
    assert "bet small" not in note


# ---------- keying + defensive ----------

def test_keyed_on_event_market_player_independent():
    db = db_()
    other = prop_bet(player="Other Pitcher")
    snap(db, other, "2026-06-13T14:00:00+00:00", "Med")
    b = prop_bet(player="Tarik Skubal")
    snap(db, b, "2026-06-13T14:00:00+00:00", "Low")
    snap(db, b, "2026-06-13T18:00:00+00:00", "High")     # this player: late-crosser prop -> BET
    assert "BET" in rec._first_entry_note(db, b) and "late-crosser prop" in rec._first_entry_note(db, b)


def test_missing_snapshot_returns_empty_tag():
    db = db_()                                           # no softbook_ev rows
    assert rec._first_entry_note(db, prop_bet()) == ""


def test_lookup_error_returns_empty_tag():
    db = sqlite3.connect(":memory:")                     # no softbook_ev table at all
    db.execute(rec.ALERT_SCHEMA)
    assert rec._first_entry_note(db, prop_bet()) == ""


# ---------- wiring into the alert message (no contradiction) ----------

def _capture(monkeypatch):
    cap = {}
    monkeypatch.setattr(rec, "send_telegram", lambda text: cap.setdefault("t", text) or 1)
    monkeypatch.setattr(rec.time, "sleep", lambda *a: None)
    return cap


def test_alert_game_first_entry_bets_with_no_header_bet_small(monkeypatch):
    db = db_()
    b = game_bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    cap = _capture(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T21:00:00+00:00")
    assert "🟣 BET" in cap["t"] and "first-entry game" in cap["t"] and "bet small" in cap["t"]
    assert "(pre-game)" in cap["t"] and "pre-game · bet small" not in cap["t"]


def test_alert_prop_first_entry_is_skip_no_bet_small(monkeypatch):
    # the contradiction case: a prop that's High on first sight must read SKIP, and
    # the whole message must carry NO 'bet small' instruction.
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    cap = _capture(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T21:00:00+00:00")
    assert "SKIP" in cap["t"] and "first-entry prop" in cap["t"]
    assert "bet small" not in cap["t"]


def test_alert_prop_late_crosser_bets(monkeypatch):
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")
    cap = _capture(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T20:00:00+00:00")
    assert "🟣 BET" in cap["t"] and "late-crosser prop" in cap["t"] and "bet small" in cap["t"]
