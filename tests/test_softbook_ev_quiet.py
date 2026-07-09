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
    return db


def run_wake(db, monkeypatch, now="2026-06-11T08:30:12+00:00", pushed=None):
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or len(sent))
    cap = pushed if pushed is not None else []
    # inject app_push so tests never touch the real Expo/paper.db send path
    n = rec.wakeup_requote(db, "key", now, app_push=lambda bs: cap.extend(bs) or len(bs))
    return n, sent


def test_wakeup_noop_before_wake_time(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    snap(db, "2026-06-11T08:00:22+00:00")
    n, sent = run_wake(db, monkeypatch, now="2026-06-11T08:00:12+00:00")  # before 08:30 WAKE_UTC
    assert n == 0 and sent == []


def test_wakeup_live_bet_survivor_pushed(monkeypatch):
    """Overnight-first-High pick STILL High in the latest scan and pre-game ->
    Telegram '지금 베팅' summary + app push at the FRESH (latest-scan) odds."""
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")                 # first High overnight
    snap(db, "2026-06-11T08:20:22+00:00", odds=1.95)      # latest scan: still High, fresh odds
    pushed = []
    n, sent = run_wake(db, monkeypatch, pushed=pushed)
    assert n == 1 and len(sent) == 1
    assert "지금 베팅" in sent[0] and "Dallas Wings" in sent[0]
    assert len(pushed) == 1
    assert (pushed[0]["side"], pushed[0]["book"], pushed[0]["odds"]) == \
        ("Dallas Wings", "BetRivers", 1.95)               # fresh latest-scan price, not the 04:20 one
    n2, sent2 = run_wake(db, monkeypatch)                 # once-per-day guard
    assert n2 == 0 and sent2 == []


def test_wakeup_started_game_skipped(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00", eid="Estart", ct="2026-06-11T08:00:00Z")
    snap(db, "2026-06-11T08:20:22+00:00", eid="Estart", ct="2026-06-11T08:00:00Z")
    n, sent = run_wake(db, monkeypatch)                   # wake 08:30 > commence 08:00
    assert n == 0 and sent == []
    assert db.execute("SELECT 1 FROM wakeup_sent").fetchone()   # day still marked (once/day)


def test_wakeup_cooled_out_of_high_skipped(monkeypatch):
    """First High overnight but the latest scan is Med -> no longer live -> skipped."""
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")                 # overnight High
    snap(db, "2026-06-11T08:20:22+00:00", tier="Med")     # cooled at wake
    n, sent = run_wake(db, monkeypatch)
    assert n == 0 and sent == []
    assert db.execute("SELECT 1 FROM wakeup_sent").fetchone()


def test_wakeup_daytime_first_high_not_overnight_skipped(monkeypatch):
    """A pick whose FIRST High was during the day (user awake) is not resurfaced,
    even if it is still High at wake."""
    db = wake_db()
    snap(db, "2026-06-10T14:00:22+00:00", eid="Eday")     # first High yesterday afternoon
    snap(db, "2026-06-11T08:20:22+00:00", eid="Eday")     # still High now
    n, sent = run_wake(db, monkeypatch)
    assert n == 0 and sent == []


# ---------- ③ should_bet split: only bettable are staked + pushed ----------

def test_wakeup_non_bet_survivor_context_only_not_pushed(monkeypatch):
    """A still-live survivor that is NOT should_bet (prop / late-crosser) is listed
    as 참고 context only — NOT under '지금 베팅' and NOT handed to the app push."""
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    snap(db, "2026-06-11T08:20:22+00:00")
    monkeypatch.setattr(rec, "_is_bet", lambda *a: False)
    pushed = []
    n, sent = run_wake(db, monkeypatch, pushed=pushed)
    assert n == 1 and len(sent) == 1
    assert "참고" in sent[0] and "지금 베팅" not in sent[0]
    assert pushed == []


def test_wakeup_bet_survivor_handed_to_app_push(monkeypatch):
    db = wake_db()
    snap(db, "2026-06-11T04:20:22+00:00")
    snap(db, "2026-06-11T08:20:22+00:00")
    pushed = []
    n, sent = run_wake(db, monkeypatch, pushed=pushed)
    assert n == 1 and len(pushed) == 1
    assert (pushed[0]["market"], pushed[0]["side"]) == ("spread", "Dallas Wings")


def test_wake_app_push_builds_deeplink_message():
    """_wake_app_push builds ONE Expo message per token, deep-linking to the top
    survivor via the app's EXISTING high_pick pick_id (matches feed._encode_id)."""
    survivors = [{"event_id": "E1", "market": "spread", "side": "Dallas Wings",
                  "point": -5.5, "player": "", "label": "Dallas Wings -5.5",
                  "book": "FanDuel", "odds": 2.10, "ev": 0.05}]
    sent = []
    n = rec._wake_app_push(survivors, tokens=["ExpoTok1", "ExpoTok2"],
                           send=lambda msgs: sent.append(msgs) or [])
    assert n == 2
    msgs = sent[0]
    assert [m["to"] for m in msgs] == ["ExpoTok1", "ExpoTok2"]
    assert msgs[0]["data"]["pick_id"] == "E1|spread|Dallas Wings|-5.5|"
    assert msgs[0]["categoryId"] == "high_pick"
    assert "아직 유효 1건" in msgs[0]["title"]


def test_wake_app_push_empty_or_no_tokens_is_noop():
    assert rec._wake_app_push([]) == 0
    one = [{"event_id": "E1", "market": "h2h", "side": "X", "point": 0,
            "player": "", "label": "X", "book": "FD", "odds": 2.0, "ev": 0.03}]
    assert rec._wake_app_push(one, tokens=[], send=lambda m: []) == 0
