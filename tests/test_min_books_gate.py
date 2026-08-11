"""No surface shows a pick only one book quotes.

2026-08-11: two amateur-MMA picks quoted by FanDuel ALONE (1/1bk) alerted as Med
BETs, and the markets did not exist in the FanDuel app. Single-book fringe
markets are exactly the ones that appear in the API without being purchasable —
the NY gate checks book keys, not whether a book sells the market.

This was impossible under High-only ML: one book caps the confidence score at 54
and High needs 55, an accidental floor the ML->Med move removed. So the gate the
tier system silently provided is now explicit: core.is_confirmable, applied at
every exposure surface, with recording untouched — the 1-book cohort stays in
the paper ledger, where it papers +21%/bet precisely because paper assumes fills
at quotes the user demonstrably cannot take.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_core as core  # noqa: E402
import softbook_ev_recorder as rec  # noqa: E402


# ── the gate ──────────────────────────────────────────────────────────

def test_one_book_is_not_confirmable():
    assert core.is_confirmable(1) is False


def test_two_books_are():
    assert core.is_confirmable(2) is True
    assert core.is_confirmable(8) is True


def test_unknown_coverage_is_not_confirmable():
    """Never surface a pick whose coverage is unknown — same policy as
    _is_bet's own error path."""
    assert core.is_confirmable(None) is False


# ── telegram ──────────────────────────────────────────────────────────

BET = {"event_id": "E1", "market": "h2h", "side": "Tom Pagliarulo",
       "point": None, "player": None, "home": "Tom Pagliarulo",
       "away": "Ananias Mulumba", "label": "Tom Pagliarulo ML",
       "book": "FanDuel", "odds": 2.34, "ev": 0.010, "n_ev": 1, "n_books": 1,
       "tier": "Med", "sport": "mma_mixed_martial_arts",
       "commence_time": "2099-01-01T00:00:00Z"}


def _alert(monkeypatch, n_books):
    ev = sqlite3.connect(":memory:")
    ev.execute(rec.SCHEMA)
    ev.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(ev)
    b = {**BET, "n_books": n_books}
    ev.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               ("2026-08-11T22:00:00+00:00", b["sport"], b["event_id"],
                b["commence_time"], b["home"], b["away"], b["market"], b["side"],
                b["point"], b["player"] or "", 0.42, 2.4, b["book"], b["odds"],
                b["ev"], b["n_books"], b["n_ev"], 47.0, b["tier"]))
    ev.commit()
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or 1)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    monkeypatch.setattr(rec, "actioned_picks", frozenset)
    rec.alert_new_high(ev, [b], "2026-08-11T22:00:00+00:00")
    return sent


def test_the_observed_alert_no_longer_sends(monkeypatch):
    """The exact pick: 1/1bk amateur MMA, Med, first-entry."""
    assert _alert(monkeypatch, n_books=1) == []


def test_two_books_still_send(monkeypatch):
    """The gate must not silence normal picks."""
    sent = _alert(monkeypatch, n_books=2)
    assert len(sent) == 1 and "BET" in sent[0]


def test_a_gated_pick_is_still_recorded_for_dedup_and_measurement(monkeypatch):
    """Exposure gate, not an erasure: high_alerted still gets the row, so if the
    market later gains books it does not re-fire as 'new', and the snapshot
    trail keeps the 1-book cohort measurable."""
    ev = sqlite3.connect(":memory:")
    ev.execute(rec.SCHEMA)
    ev.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(ev)
    monkeypatch.setattr(rec, "send_telegram", lambda t: 1)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    monkeypatch.setattr(rec, "actioned_picks", frozenset)
    rec.alert_new_high(ev, [BET], "2026-08-11T22:00:00+00:00")
    assert ev.execute("SELECT COUNT(*) FROM high_alerted").fetchone()[0] == 1


# ── every surface reads the same gate ─────────────────────────────────

def test_every_exposure_surface_calls_the_core_gate():
    """One copy of the policy. A surface with its own n_books comparison is the
    +EV-tab failure mode again: a private copy that stops moving with the rule."""
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts")
    for name in ("softbook_ev_recorder.py", "softbook_push_worker.py",
                 "softbook_app_api.py", "softbook_ev_today.py"):
        with open(os.path.join(root, name)) as f:
            src = f.read()
        assert "is_confirmable" in src, f"{name} does not gate on coverage"
        assert "n_books >= 2" not in src.replace(
            "MIN_ALERT_BOOKS", ""), f"{name} hardcodes the threshold"
