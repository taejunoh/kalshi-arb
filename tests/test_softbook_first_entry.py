"""FIRST-ENTRY GAME ONLY BET/SKIP alert tag — 2026-06-16.

A pick is FIRST-ENTRY if its (event, market, player) was already High at its
earliest recorded snapshot (a standing mispricing); a LATE-CROSSER drifted into
High after the line moved. The tag BETs iff (game [ML/spread/total] AND first-entry)
and SKIPs everything else (all props, all late-crossers).

This is a CONTRACTION of the first-entry-only discipline, NOT an edge claim: the
6/15 re-measure found no confirmed pick edge (every cut ≈ breakeven, Pikkit −4.6%
governs, the seductive cells were artifacts). Props are dropped (first-entry props
ran negative; the late-crosser-prop 'edge' was a single 6/14 slate). Real +EV is in
signup bonuses; these ≈BE game bets are the low-variance vehicle to clear them.
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


def test_prop_late_crosser_is_skip():
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Low")      # prop, drifted to High later
    snap(db, b, "2026-06-13T18:40:00+00:00", "High")
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "prop" in note             # all props skip now
    assert "bet small" not in note


def test_prop_first_entry_is_skip():
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "High")     # prop, High from first sight
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "prop" in note             # props skip regardless of entry
    assert "bet small" not in note


# ---------- keying + defensive ----------

def test_keyed_on_event_market_independent():
    # a game pick's verdict depends on ITS OWN earliest snapshot, not another market's
    db = db_()
    sp = game_bet(market="spread", eid="G1")
    snap(db, sp, "2026-06-13T14:00:00+00:00", "High")    # spread: first-entry -> BET
    tot = game_bet(market="total", side="Over", point=8.5, eid="G1")
    snap(db, tot, "2026-06-13T14:00:00+00:00", "Med")    # total: late-crosser -> SKIP
    snap(db, tot, "2026-06-13T18:00:00+00:00", "High")
    assert "BET" in rec._first_entry_note(db, sp)
    assert "SKIP" in rec._first_entry_note(db, tot)


def test_opposite_side_crosser_is_skip_not_inherited_bet():
    # THE BUG (fixed 2026-06-17): a total is High from the open on ONE side; the total
    # then steams and the OPPOSITE side crosses into High. Side-blind keying made the
    # opposite side inherit the open side's first_entry=True -> mislabeled BET. It is a
    # LATE-CROSSER: the first-entry side still BETs, the opposite side must SKIP.
    db = db_()
    under = game_bet(market="total", side="Under", point=163.5, eid="T1")
    snap(db, under, "2026-06-17T01:40:00+00:00", "High")     # Under High from the open
    over = game_bet(market="total", side="Over", point=164.5, eid="T1")
    snap(db, over, "2026-06-17T07:00:00+00:00", "Med")       # Over appears only after the move
    snap(db, over, "2026-06-17T07:40:00+00:00", "High")      # ...then crosses to High
    assert "BET" in rec._first_entry_note(db, under)         # true first-entry side -> BET
    note = rec._first_entry_note(db, over)                   # opposite-side crosser -> SKIP
    assert "SKIP" in note and "late-crosser game" in note
    assert "bet small" not in note


def test_same_side_line_move_stays_first_entry():
    # A same-direction re-quote (line moved, SAME side) keeps the first-entry verdict —
    # it is the same directional view, not a new opposite-side crosser.
    db = db_()
    o1 = game_bet(market="total", side="Over", point=164.5, eid="T2")
    snap(db, o1, "2026-06-17T01:40:00+00:00", "High")        # Over High from the open
    o2 = game_bet(market="total", side="Over", point=165.5, eid="T2")
    snap(db, o2, "2026-06-17T07:40:00+00:00", "High")        # Over re-quoted at a new line
    assert "BET" in rec._first_entry_note(db, o2)


def test_opposite_side_dup_note_recommends_skip_not_higher_ev():
    # The opposite-direction dup warning must NOT tell the user to take the higher-EV
    # side (data-refuted); it points back to the first-entry side and forbids hedging.
    db = db_()
    under = game_bet(market="total", side="Under", point=163.5, eid="T3")
    over = game_bet(market="total", side="Over", point=164.5, eid="T3")
    snap(db, under, "2026-06-17T01:40:00+00:00", "High")
    snap(db, over, "2026-06-17T07:40:00+00:00", "High")
    db.execute("INSERT INTO high_alerted (event_id, market, side, point, player,"
               " alerted_at) VALUES (?,?,?,?,?,?)",
               ("T3", "total", "Under", 163.5, "", "2026-06-17T01:40:00+00:00"))
    note = rec._dup_note(db, over)
    assert "late-crosser" in note and "first-entry" in note
    assert "EV 높은 쪽만" not in note


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


def test_alert_prop_is_skip_no_bet_small(monkeypatch):
    # any prop must read SKIP, and the whole message must carry NO 'bet small'.
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T20:00:00+00:00", "High")
    cap = _capture(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T21:00:00+00:00")
    assert "SKIP" in cap["t"] and "prop" in cap["t"]
    assert "bet small" not in cap["t"]


def test_alert_prop_late_crosser_also_skips(monkeypatch):
    db = db_()
    b = prop_bet()
    snap(db, b, "2026-06-13T14:00:00+00:00", "Med")
    cap = _capture(monkeypatch)
    rec.alert_new_high(db, [b], "2026-06-13T20:00:00+00:00")
    assert "SKIP" in cap["t"] and "prop" in cap["t"]
    assert "bet small" not in cap["t"]
