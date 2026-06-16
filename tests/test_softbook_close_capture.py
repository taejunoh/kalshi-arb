"""Closing-line capture — true Pinnacle close for alerted lines (2026-06-16).

Fixes the CLV truncation blind spot: the recorder stops logging a line once its EV
leaves the band, so measure used the last in-band snapshot as the "close" (biased
favorable). This captures the Pinnacle fair near commence regardless of band.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_close_capture as cap        # noqa: E402
import softbook_ev_recorder as rec          # noqa: E402

NOW = "2026-06-16T22:40:00+00:00"


def db_():
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    db.execute(rec.ALERT_SCHEMA)
    return db


def add_line(db, eid, market, side, point, player, commence, alerted=True,
             sport="baseball_mlb"):
    db.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               ("2026-06-16T18:00:00+00:00", sport, eid, commence, "H", "A", market,
                side, point, player, 0.5, 2.0, "FD", 2.0, 0.03, 8, 6, 60, "High"))
    if alerted:
        db.execute("INSERT INTO high_alerted(event_id,market,side,point,player,alerted_at)"
                   " VALUES (?,?,?,?,?,?)", (eid, market, side, point, player, "2026-06-16T18:00:00+00:00"))
    db.commit()


def qfn(fairs):
    return lambda sport, eid, market, side, point, player: fairs.get(
        (eid, market, side, point, player), (None, None))


def test_captures_only_pregame_lines_in_window():
    db = db_()
    add_line(db, "A", "spread", "NYM", -1.5, "", "2026-06-16T23:00:00Z")   # +20min -> capture
    add_line(db, "B", "h2h", "NYM", 0, "", "2099-01-01T00:00:00Z")         # far -> skip
    add_line(db, "C", "total", "Over", 8.5, "", "2026-06-16T22:00:00Z")    # started -> skip
    fairs = {("A", "spread", "NYM", -1.5, ""): (0.55, 2.1),
             ("B", "h2h", "NYM", 0, ""): (0.5, 2.0),
             ("C", "total", "Over", 8.5, ""): (0.5, 2.0)}
    n = cap.capture(db, NOW, qfn(fairs), window_min=40)
    assert n == 1
    assert db.execute("SELECT event_id, close_fair, close_soft FROM closing_line").fetchall() \
        == [("A", 0.55, 2.1)]


def test_upsert_keeps_the_capture_closest_to_commence():
    db = db_()
    add_line(db, "A", "spread", "NYM", -1.5, "", "2026-06-16T23:00:00Z")
    cap.capture(db, "2026-06-16T22:30:00+00:00", qfn({("A", "spread", "NYM", -1.5, ""): (0.55, 2.1)}))
    cap.capture(db, "2026-06-16T22:50:00+00:00", qfn({("A", "spread", "NYM", -1.5, ""): (0.58, 2.0)}))
    fair, at = db.execute("SELECT close_fair, captured_at FROM closing_line").fetchone()
    assert fair == 0.58 and "22:50" in at        # latest pre-game capture wins


def test_no_fair_is_not_written():
    db = db_()
    add_line(db, "A", "spread", "NYM", -1.5, "", "2026-06-16T23:00:00Z")
    n = cap.capture(db, NOW, qfn({("A", "spread", "NYM", -1.5, ""): (None, None)}))
    assert n == 0
    assert db.execute("SELECT COUNT(*) FROM closing_line").fetchone()[0] == 0


def test_unalerted_line_is_skipped():
    db = db_()
    add_line(db, "A", "spread", "NYM", -1.5, "", "2026-06-16T23:00:00Z", alerted=False)
    n = cap.capture(db, NOW, qfn({("A", "spread", "NYM", -1.5, ""): (0.55, 2.1)}))
    assert n == 0
