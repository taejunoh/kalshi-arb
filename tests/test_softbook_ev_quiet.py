"""Quiet-hours alignment for the softbook +EV pipeline — added 2026-06-11.

① ledger B (paper_bet_high) enters at the FIRST pre-game High snapshot at ANY
   hour — quiet-hour triggers count (user bets at night too; alerts now fire 24/7,
   2026-06-11 his call).
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


# ---------- ① candidates_high: first High snapshot wins, ANY hour ----------

def test_candidates_high_quiet_trigger_enters_at_trigger():
    db = src_db()
    snap(db, "2026-06-11T04:20:22+00:00", book="BetRivers", odds=1.91)   # overnight
    snap(db, "2026-06-11T11:00:22+00:00", book="FanDuel", odds=1.82, ev=0.012)
    out = pnl.candidates_high(db)
    assert len(out) == 1
    c = out[0]
    assert c[10] == "BetRivers" and c[11] == 1.91     # trigger price, not wake-up price
    assert c[14].startswith("2026-06-11T04:20")


def test_candidates_high_quiet_only_line_enters():
    db = src_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    assert len(pnl.candidates_high(db)) == 1


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
    # must return a message_id: a None send is treated as failed -> row deleted
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or len(sent))
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
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
    # valid line now goes out as its own per-bet alert (fill replies map to it)
    assert "아직 유효" in sent[0] and "FanDuel @ +110" in sent[0]   # 2.10 -> +110 (american)
    assert "✂️" in sent[0] and "답장" in sent[0]
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
