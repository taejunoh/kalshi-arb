"""Telegram and push must not re-pitch a pick the user has already acted on.

2026-08-05, observed live: a Med ML line (Washington Nationals) was marked bet in
the app at 20:37 UTC, and at 21:50 Telegram alerted it — "🟣 BET · 베팅 (bet
small)", 73 minutes after the user said they had bet it.

Not a dedup failure. `high_alerted` held exactly one row for that line, at 21:50:
it was the FIRST alert. Med only started qualifying at ~19:00 that day, so the
13:20-15:10 Med snapshots were recorded and never alerted — nothing earlier
existed to dedup against. Each channel was correct on its own rule. The gap is
that the alert path knows what the bet RULE says and nothing about what the USER
did; only the app reads pick_state.

Moving ML to Med quadrupled alert volume, which is why it surfaced now. The gap
predates it and would have kept producing double-stake invitations.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_recorder as rec  # noqa: E402

PICK = ("E1", "h2h", "Washington Nationals", 0.0, "")


@pytest.fixture
def paper(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE pick_state(event_id TEXT, market TEXT, side TEXT,"
               " point REAL, player TEXT, state TEXT, updated_at TEXT)")
    db.commit()
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(path))
    yield db
    db.close()


def _mark(db, state, pick=PICK):
    db.execute("INSERT INTO pick_state VALUES (?,?,?,?,?,?,'now')", (*pick, state))
    db.commit()


# ── what counts as "already acted on" ─────────────────────────────────

BET = {"event_id": "E1", "market": "h2h", "side": "Washington Nationals",
       "point": None, "player": None, "home": "Philadelphia Phillies",
       "away": "Washington Nationals", "label": "Washington Nationals ML",
       "book": "FanDuel", "odds": 2.82, "ev": 0.013, "n_ev": 1, "n_books": 8,
       "tier": "Med", "sport": "baseball_mlb",
       "commence_time": "2099-01-01T00:00:00Z"}


def _alert(monkeypatch, marked):
    """Drive alert_new_high over a first-entry Med ML bet; return what was sent."""
    ev = sqlite3.connect(":memory:")
    ev.execute(rec.SCHEMA)
    ev.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(ev)
    # Its only snapshot, written directly: record() needs pinnacle fields this
    # fixture has no reason to carry, and swallows the KeyError if they're absent
    # — which would leave no snapshot and silently make every case look "unseen".
    ev.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               ("2026-08-05T21:50:00+00:00", BET["sport"], BET["event_id"],
                BET["commence_time"], BET["home"], BET["away"], BET["market"],
                BET["side"], BET["point"], BET["player"] or "", 0.35, 2.9,
                BET["book"], BET["odds"], BET["ev"], BET["n_books"], BET["n_ev"],
                40.0, BET["tier"]))
    ev.commit()
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or 1)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    monkeypatch.setattr(rec, "actioned_picks", lambda: frozenset({PICK} if marked else ()))
    rec.alert_new_high(ev, [BET], "2026-08-05T21:50:00+00:00")
    return sent


def test_an_unmarked_pick_still_alerts(monkeypatch):
    """The guard must not silence the normal case."""
    sent = _alert(monkeypatch, marked=False)
    assert len(sent) == 1 and "BET" in sent[0]


def test_a_pick_marked_bet_is_not_alerted(monkeypatch):
    """The observed case: telling the user to bet what they already bet."""
    assert _alert(monkeypatch, marked=True) == []


def test_bet_and_dismissed_are_both_suppressed(paper):
    _mark(paper, "bet")
    _mark(paper, "dismissed", ("E2", "h2h", "Toronto Blue Jays", 0.0, ""))
    done = rec.actioned_picks()
    assert PICK in done
    assert ("E2", "h2h", "Toronto Blue Jays", 0.0, "") in done


def test_an_untouched_pick_is_not_suppressed(paper):
    assert rec.actioned_picks() == frozenset()


def test_other_states_do_not_suppress(paper):
    """Only bet/dismissed are the user saying "handled". Anything else must
    still be alertable — silence is the expensive failure here."""
    _mark(paper, "seen")
    assert PICK not in rec.actioned_picks()


def test_the_key_normalises_point_and_player(paper):
    """pick_state stores NULL point/player for moneyline; the alert path builds
    the key from the bet dict. They have to meet."""
    paper.execute("INSERT INTO pick_state VALUES ('E9','h2h','Side',NULL,NULL,'bet','now')")
    paper.commit()
    assert ("E9", "h2h", "Side", 0.0, "") in rec.actioned_picks()


# ── failure policy: never swallow an alert ────────────────────────────

def test_a_missing_app_db_suppresses_nothing(tmp_path, monkeypatch):
    """An alert that should have been suppressed is a nuisance; a swallowed
    alert is a missed bet. The failure direction is not symmetric."""
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "nope.db"))
    assert rec.actioned_picks() == frozenset()


def test_a_db_without_the_table_suppresses_nothing(tmp_path, monkeypatch):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(path))
    assert rec.actioned_picks() == frozenset()


# ── both channels read the same source ────────────────────────────────

def test_push_worker_uses_the_recorders_set_not_its_own_query():
    """Two copies of "has the user handled this" would drift, which is the
    whole shape of the bug this fixes."""
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "softbook_push_worker.py")) as f:
        src = f.read()
    assert "rec.actioned_picks()" in src
    assert "FROM pick_state" not in src, "push worker must not re-query pick_state"
