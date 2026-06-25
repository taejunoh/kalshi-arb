"""Tests for the daily auto-settler of stuck past-dated +EV paper bets.
Pure grading + JSON extraction run offline; main() runs with an injected fake Claude
client (no network / no ANTHROPIC_API_KEY needed)."""
import sqlite3

import softbook_auto_settle_stuck as a


# ---- _extract_json ----

def test_extract_json_bare_and_fenced_and_prose():
    assert a._extract_json('{"status":"final","winner":"X"}')["winner"] == "X"
    assert a._extract_json('```json\n{"a":1}\n```')["a"] == 1
    assert a._extract_json('Here is the result:\n{"status":"final"}\nDone.')["status"] == "final"
    assert a._extract_json("no json here") is None
    assert a._extract_json("") is None


# ---- grade_bet ----

def test_h2h_winner_based():
    fin = {"status": "final", "winner": "Devin Cushing"}
    assert a.grade_bet("h2h", "Devin Cushing", None, "JHon Ingram", "Devin Cushing", fin) == "win"
    assert a.grade_bet("h2h", "JHon Ingram", None, "JHon Ingram", "Devin Cushing", fin) == "lose"


def test_h2h_draw_and_notplayed_void():
    assert a.grade_bet("h2h", "Indonesia", None, "Indonesia", "Thailand",
                       {"status": "final", "winner": None}) == "void"
    assert a.grade_bet("h2h", "Chicago Cubs", None, "Chicago Cubs", "Toronto Blue Jays",
                       {"status": "not_played"}) == "void"


def test_h2h_winner_not_in_either_side_is_ungradeable():
    assert a.grade_bet("h2h", "Indonesia", None, "Indonesia", "Thailand",
                       {"status": "final", "winner": "Malaysia"}) is None


def test_h2h_name_match_tolerates_accents_punct():
    # stored side "Iva Jović" vs winner "Iva Jovic"
    fin = {"status": "final", "winner": "Iva Jovic"}
    assert a.grade_bet("h2h", "Iva Jović", None, "Iva Jović", "Xinyu Wang", fin) == "win"


def test_spread_total_via_core_grade():
    fin = {"status": "final", "home_score": 6, "away_score": 2}  # home won by 4
    # home -1.5 covers
    assert a.grade_bet("spread", "HOME", -1.5, "HOME", "AWAY",
                       {"status": "final", "home_score": 6, "away_score": 2, }) == "win"
    # total over 8.5 with sum 8 -> lose; sum 9 -> win; ==9 push
    assert a.grade_bet("total", "Over", 8.5, "HOME", "AWAY",
                       {"status": "final", "home_score": 5, "away_score": 3}) == "lose"
    assert a.grade_bet("total", "Over", 8.5, "HOME", "AWAY",
                       {"status": "final", "home_score": 6, "away_score": 3}) == "win"
    assert a.grade_bet("total", "Over", 9.0, "HOME", "AWAY",
                       {"status": "final", "home_score": 6, "away_score": 3}) == "push"


def test_tennis_retired_voids_total():
    assert a.grade_bet("total", "Under", 21.5, "Jovic", "Wang",
                       {"status": "final", "retired": True,
                        "home_score": 4, "away_score": 6}) == "void"


def test_spread_missing_scores_left_pending():
    assert a.grade_bet("spread", "HOME", -1.5, "HOME", "AWAY",
                       {"status": "final", "home_score": None, "away_score": None}) is None


# ---- main() flow with a fake client ----

class _Txt:
    type = "text"
    def __init__(self, t): self.text = t


class _Resp:
    stop_reason = "end_turn"
    def __init__(self, t): self.content = [_Txt(t)]


class _Msgs:
    def __init__(self, payload): self._p = payload
    def create(self, **kw): return _Resp(self._p)


class _FakeClient:
    def __init__(self, payload): self.messages = _Msgs(payload)


_SCHEMA = ("event_id TEXT, sport TEXT, home TEXT, away TEXT, market TEXT, side TEXT, "
           "point REAL, player TEXT, label TEXT, commence_time TEXT, odds REAL, stake REAL, "
           "completed INTEGER DEFAULT 0, won INTEGER, result TEXT, pnl REAL, settled_at TEXT")


def _mkdb(tmp_path):
    db = sqlite3.connect(str(tmp_path / "p.db"))
    for t in ("paper_bet", "paper_bet_high"):
        db.execute(f"CREATE TABLE {t}({_SCHEMA})")
    return db


def _ins(db, t, **k):
    cols = ",".join(k); ph = ",".join("?" * len(k))
    db.execute(f"INSERT INTO {t}({cols}) VALUES ({ph})", tuple(k.values()))


def test_main_settles_only_stuck_past_high_conf(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "PAPER_DB", str(tmp_path / "p.db"))
    db = _mkdb(tmp_path)
    # stuck boxing h2h (started 2 days ago) — SHOULD settle
    _ins(db, "paper_bet", event_id="E1", sport="boxing_boxing", home="Ingram", away="Cushing",
         market="h2h", side="Cushing", label="Cushing ML", odds=2.75, stake=2.0,
         commence_time="datetime", completed=0)
    db.execute("UPDATE paper_bet SET commence_time=datetime('now','-2 days') WHERE event_id='E1'")
    # future game — must be ignored
    _ins(db, "paper_bet", event_id="E2", sport="baseball_mlb", home="A", away="B",
         market="h2h", side="A", label="A ML", odds=1.9, stake=2.0, commence_time="x", completed=0)
    db.execute("UPDATE paper_bet SET commence_time=datetime('now','+5 hours') WHERE event_id='E2'")
    # already-settled — must be ignored
    _ins(db, "paper_bet", event_id="E3", sport="boxing_boxing", home="C", away="D",
         market="h2h", side="D", label="D ML", odds=2.0, stake=2.0, commence_time="x", completed=1)
    db.execute("UPDATE paper_bet SET commence_time=datetime('now','-3 days') WHERE event_id='E3'")
    db.commit(); db.close()

    client = _FakeClient('{"status":"final","winner":"Cushing","home_score":null,'
                         '"away_score":null,"retired":false,"confidence":"high","sources":["x"]}')
    monkeypatch.setattr("sys.argv", ["prog"])
    monkeypatch.setattr(a, "send_telegram", lambda *x: None)
    a.main(client=client)

    db = sqlite3.connect(str(tmp_path / "p.db"))
    e1 = db.execute("SELECT completed,won,result,pnl FROM paper_bet WHERE event_id='E1'").fetchone()
    assert e1 == (1, 1, "win", 3.5)                              # settled win
    e2 = db.execute("SELECT completed FROM paper_bet WHERE event_id='E2'").fetchone()
    assert e2[0] == 0                                            # future untouched
    e3won = db.execute("SELECT won FROM paper_bet WHERE event_id='E3'").fetchone()
    assert e3won[0] is None                                      # pre-settled untouched


def test_main_low_confidence_left_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "PAPER_DB", str(tmp_path / "p.db"))
    db = _mkdb(tmp_path)
    _ins(db, "paper_bet", event_id="E1", sport="boxing_boxing", home="Ingram", away="Cushing",
         market="h2h", side="Cushing", label="Cushing ML", odds=2.75, stake=2.0,
         commence_time="x", completed=0)
    db.execute("UPDATE paper_bet SET commence_time=datetime('now','-2 days') WHERE event_id='E1'")
    db.commit(); db.close()
    client = _FakeClient('{"status":"final","winner":"Cushing","confidence":"low","sources":[]}')
    monkeypatch.setattr("sys.argv", ["prog"])
    a.main(client=client)
    db = sqlite3.connect(str(tmp_path / "p.db"))
    assert db.execute("SELECT completed FROM paper_bet WHERE event_id='E1'").fetchone()[0] == 0
