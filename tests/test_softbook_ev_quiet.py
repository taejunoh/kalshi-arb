"""Quiet-hours alignment for the softbook +EV pipeline — added 2026-06-11.

① ledger B (paper_bet_high) enters at the first NON-quiet pre-game High snapshot
   (alerts are suppressed 02-09 UTC, so a quiet-hour trigger price is unplaceable);
   a line whose only High snapshots are quiet never enters.
② wakeup_requote: one push after the quiet window re-quoting overnight Highs at
   current odds — in-band lines get marked in high_alerted, dead lines stay
   unmarked so a later return to High re-alerts.
③ migration: existing quiet-hour ledger-B rows are repriced to the first
   non-quiet High snapshot or deleted when none exists.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_core as core                                # noqa: E402
import softbook_ev_paper_pnl as pnl                            # noqa: E402
import softbook_ev_recorder as rec                             # noqa: E402
import softbook_ev_quiet_requote_migration as mig              # noqa: E402

CT = "2026-06-12T01:10:00Z"        # WNBA-style evening start


def src_db():
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    return db


def snap(db, rec_at, tier="High", eid="E1", market="spread", side="Dallas Wings",
         point=-5.5, player="", book="BetRivers", odds=1.91, ev=0.045, ct=CT):
    db.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               (rec_at, "basketball_wnba", eid, ct, "Dallas Wings", "Phoenix Mercury",
                market, side, point, player, 0.547, 1.73, book, odds, ev, 7, 6,
                61.0, tier))


# ---------- ① candidates_high quiet filter ----------

def test_candidates_high_skips_quiet_enters_first_awake():
    db = src_db()
    snap(db, "2026-06-11T04:20:22+00:00", book="BetRivers", odds=1.91)   # quiet
    snap(db, "2026-06-11T11:00:22+00:00", book="FanDuel", odds=1.82, ev=0.012)
    out = pnl.candidates_high(db)
    assert len(out) == 1
    c = out[0]
    assert c[10] == "FanDuel" and c[11] == 1.82
    assert c[14].startswith("2026-06-11T11:00")


def test_candidates_high_drops_quiet_only_line():
    db = src_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    snap(db, "2026-06-11T08:40:22+00:00")          # still quiet (08 in range(2,9))
    assert pnl.candidates_high(db) == []


def test_candidates_high_awake_trigger_unchanged():
    db = src_db()
    snap(db, "2026-06-11T14:00:22+00:00", book="BetRivers", odds=1.91)
    snap(db, "2026-06-11T15:00:22+00:00", book="FanDuel", odds=1.99, ev=0.06)
    out = pnl.candidates_high(db)
    assert len(out) == 1 and out[0][10] == "BetRivers"   # first awake High wins


def test_candidates_high_still_requires_pregame():
    db = src_db()
    snap(db, "2026-06-12T02:00:22+00:00")          # after commence (and quiet)
    snap(db, "2026-06-12T11:00:22+00:00")          # after commence, awake
    assert pnl.candidates_high(db) == []


# ---------- ② wakeup_requote ----------

def payload(fd_price=2.10, br_price=None):
    books = [{"key": "pinnacle", "markets": [{
        "key": "spreads", "outcomes": [
            {"name": "Dallas Wings", "point": -5.5, "price": 1.91},
            {"name": "Phoenix Mercury", "point": 5.5, "price": 1.91}]}]}]
    if fd_price:
        books.append({"key": "fanduel", "markets": [{
            "key": "alternate_spreads",
            "outcomes": [{"name": "Dallas Wings", "point": -5.5, "price": fd_price}]}]})
    if br_price:
        books.append({"key": "betrivers", "markets": [{
            "key": "spreads",
            "outcomes": [{"name": "Dallas Wings", "point": -5.5, "price": br_price}]}]})
    return {"home_team": "Dallas Wings", "away_team": "Phoenix Mercury",
            "bookmakers": books}


def test_line_quotes_exact_line_best_per_book():
    fair, quotes = core.line_quotes(payload(fd_price=2.10, br_price=1.78),
                                    "spread", "Dallas Wings", -5.5)
    assert fair == pytest.approx(0.5)
    assert sorted(quotes) == [("BetRivers", 1.78), ("FanDuel", 2.10)]


def wake_db():
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    db.execute(rec.ALERT_SCHEMA)
    return db


def run_wake(db, monkeypatch, data, now="2026-06-11T09:00:12+00:00"):
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t))
    monkeypatch.setattr(core, "event_odds", lambda *a, **k: (data, 0))
    n = rec.wakeup_requote(db, "key", now)
    return n, sent


def test_wakeup_noop_during_quiet(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    n, sent = run_wake(db, monkeypatch, payload(), now="2026-06-11T05:00:12+00:00")
    assert n == 0 and sent == []


def test_wakeup_still_in_band_marks_alerted(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    n, sent = run_wake(db, monkeypatch, payload(fd_price=2.10))   # EV +5% in band
    assert n == 1 and len(sent) == 1
    assert "아직 유효" in sent[0] and "FanDuel 2.10" in sent[0]
    assert db.execute("SELECT COUNT(*) FROM high_alerted").fetchone()[0] == 1
    # second call same day: once-per-day guard
    n2, sent2 = run_wake(db, monkeypatch, payload(fd_price=2.10))
    assert n2 == 0 and sent2 == []


def test_wakeup_dead_line_not_marked(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    n, sent = run_wake(db, monkeypatch, payload(fd_price=1.82))   # EV +0.5% below band
    assert n == 1 and "엣지 소멸" in sent[0]
    assert db.execute("SELECT COUNT(*) FROM high_alerted").fetchone()[0] == 0


def test_wakeup_skips_lines_alerted_or_outside_window(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-10T14:00:22+00:00", eid="E_old")     # first High pre-window
    snap(db, "2026-06-11T04:20:22+00:00", eid="E_old")     # re-High overnight
    snap(db, "2026-06-11T04:40:22+00:00", eid="E_marked")
    db.execute("INSERT INTO high_alerted VALUES ('E_marked','spread','Dallas Wings',"
               "-5.5,'','2026-06-11T04:40:22+00:00')")
    n, sent = run_wake(db, monkeypatch, payload())
    assert n == 0 and sent == []
    assert db.execute("SELECT 1 FROM wakeup_sent").fetchone()


def test_wakeup_started_game_listed_as_missed(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T07:40:22+00:00", eid="E_npb", ct="2026-06-11T09:00:00Z")
    def boom(*a, **k):
        raise AssertionError("must not fetch odds for a started game")
    monkeypatch.setattr(core, "event_odds", boom)
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t))
    n = rec.wakeup_requote(db, "key", "2026-06-11T09:00:12+00:00")
    assert n == 1 and "경기 시작돼 패스" in sent[0]
    assert db.execute("SELECT COUNT(*) FROM high_alerted").fetchone()[0] == 0


# ---------- ③ migration ----------

def paper_db():
    db = sqlite3.connect(":memory:")
    db.execute(pnl.HIGH_SCHEMA)
    return db


def bet_row(db, eid, placed, book="BetRivers", odds=1.91, stake=3.0,
            completed=0, result=None, pnl_val=None, won=None):
    db.execute("""INSERT INTO paper_bet_high (event_id, sport, home, away, market,
        side, point, player, label, commence_time, book, odds, ev, stake, placed_at,
        tier, conf, completed, won, result, pnl)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (eid, "basketball_wnba", "Dallas Wings", "Phoenix Mercury", "spread",
         "Dallas Wings", -5.5, "", "Dallas Wings -5.5", CT, book, odds, 0.045,
         stake, placed, "High", 61.0, completed, won, result, pnl_val))


def test_migration_reprices_and_deletes():
    src = src_db()
    # E_rep: quiet High then awake High -> reprice (settled: pnl recomputed)
    snap(src, "2026-06-11T04:20:22+00:00", eid="E_rep", odds=1.91)
    snap(src, "2026-06-11T11:00:22+00:00", eid="E_rep", book="FanDuel",
         odds=1.82, ev=0.012)
    # E_del: quiet-only High -> delete
    snap(src, "2026-06-11T04:40:22+00:00", eid="E_del")
    # E_keep: awake trigger -> untouched
    snap(src, "2026-06-11T14:00:22+00:00", eid="E_keep")
    paper = paper_db()
    bet_row(paper, "E_rep", "2026-06-11T04:20:22+00:00",
            completed=1, result="win", won=1, pnl_val=2.73, stake=3.0)
    bet_row(paper, "E_del", "2026-06-11T04:40:22+00:00")
    bet_row(paper, "E_keep", "2026-06-11T14:00:22+00:00")
    nrep, ndel = mig.migrate(paper, src, apply=True)
    assert (nrep, ndel) == (1, 1)
    rows = {r[0]: r for r in paper.execute(
        "SELECT event_id, book, odds, stake, pnl, placed_at FROM paper_bet_high")}
    assert set(rows) == {"E_rep", "E_keep"}
    eid, book, odds, stake, pnl_val, placed = rows["E_rep"]
    assert book == "FanDuel" and odds == 1.82
    assert placed.startswith("2026-06-11T11:00")
    assert pnl_val == pytest.approx(round(stake * 0.82, 2))   # win pnl recomputed
    assert rows["E_keep"][1] == "BetRivers"                   # untouched
