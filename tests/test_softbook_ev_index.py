"""The app API's hot queries must not scan the whole snapshot table.

/picks runs market_first_entry_sides and the alert-vs-now lookup ONCE PER PICK.
The table's only other index is the autoindex behind UNIQUE(recorded_at, ...),
which leads with recorded_at and so cannot serve a lookup keyed on
(event_id, market). Measured on the live db 2026-08-05: 203 picks against 310k
rows took 26s — past the app's HTTP timeout, which surfaced as "fetch failed".
With the index, 0.18s.

This guards the plan, not the wall-clock: a timing assertion would be flaky on
a loaded host, but "does SQLite reach for the index" is deterministic.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_recorder as rec  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "OUT", str(tmp_path / "ev.db"))
    c = rec.init_db()
    yield c
    c.close()


def test_init_db_creates_the_event_market_index(db):
    names = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='softbook_ev'")}
    assert "idx_sbev_event_market" in names


def test_init_db_is_idempotent(tmp_path, monkeypatch):
    """It runs on every recorder start, twice an hour."""
    monkeypatch.setattr(rec, "OUT", str(tmp_path / "ev.db"))
    rec.init_db().close()
    rec.init_db().close()          # must not raise "index already exists"


def _plan(db, sql, n):
    return " ".join(r[3] for r in db.execute(
        "EXPLAIN QUERY PLAN " + sql, tuple([None] * n)))


def test_first_entry_lookup_uses_the_index(db):
    plan = _plan(db, "SELECT MIN(recorded_at) FROM softbook_ev WHERE event_id=?"
                     " AND market=? AND COALESCE(player,'')=?", 3)
    assert "idx_sbev_event_market" in plan, plan


def test_alert_vs_now_lookup_uses_the_index(db):
    """The one that was a full SCAN — 15.7s of the 26."""
    plan = _plan(db, "SELECT recorded_at, snapshot_ev, best_soft_price, best_soft_book"
                     " FROM softbook_ev WHERE event_id=? AND market=? AND side=?"
                     " AND IFNULL(player,'')=? AND IFNULL(point,-999)=IFNULL(?,-999)"
                     " ORDER BY recorded_at DESC LIMIT 1", 5)
    assert "idx_sbev_event_market" in plan, plan
    assert "SCAN softbook_ev" not in plan, plan
