"""The per-pick stake is one value, read by both the app card and Telegram.

It lived only in the phone's SecureStore, so the server never saw it. The user
changed it to $25 in July; Telegram kept printing $20 — its code default —
for a month, on the same picks the card labelled $25. Same class of split as
the bet rule before core.is_bet: two surfaces describing one decision from two
copies.

Now it lives on filter_prefs and both read adb.unit_stake(). The device copy
survives as an offline fallback, refreshed from the server on load.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_app_db as adb  # noqa: E402
import softbook_ev_recorder as rec  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(path))
    monkeypatch.delenv("SOFTBOOK_UNIT_STAKE", raising=False)
    c = sqlite3.connect(path)
    adb.init_schema(c)
    return c


def _set(db, v):
    db.execute("INSERT INTO filter_prefs(id, unit_stake) VALUES (1, ?)"
               " ON CONFLICT(id) DO UPDATE SET unit_stake=excluded.unit_stake", (v,))
    db.commit()


# ── the shared value ──────────────────────────────────────────────────

def test_it_reads_what_the_app_stored(db):
    _set(db, 25.0)
    assert adb.unit_stake(db) == 25.0


def test_an_unset_stake_falls_back_to_the_default(db):
    assert adb.unit_stake(db) == adb.DEFAULT_UNIT_STAKE


def test_it_opens_its_own_connection_when_given_none(db):
    """The recorder calls this without holding a handle on the app db."""
    _set(db, 25.0)
    assert adb.unit_stake() == 25.0


def test_a_missing_db_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "nope.db"))
    monkeypatch.delenv("SOFTBOOK_UNIT_STAKE", raising=False)
    assert adb.unit_stake() == adb.DEFAULT_UNIT_STAKE


def test_a_pre_migration_db_does_not_raise(tmp_path, monkeypatch):
    """filter_prefs without the column — an app db from before this change."""
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE filter_prefs(id INTEGER PRIMARY KEY, min_ev REAL)")
    c.execute("INSERT INTO filter_prefs(id, min_ev) VALUES (1, 0.0)")
    c.commit()
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(path))
    monkeypatch.delenv("SOFTBOOK_UNIT_STAKE", raising=False)
    assert adb.unit_stake() == adb.DEFAULT_UNIT_STAKE


def test_the_column_is_added_idempotently(db):
    adb.init_schema(db)                       # second run must not raise
    cols = {r[1] for r in db.execute("PRAGMA table_info(filter_prefs)")}
    assert "unit_stake" in cols


# ── telegram reads the same number ────────────────────────────────────

def test_the_alert_quotes_the_app_value(db):
    """The bug itself: card $25, Telegram $20."""
    _set(db, 25.0)
    assert rec.unit_stake() == 25.0


def test_a_change_applies_to_the_very_next_alert(db):
    """Read per send, not at import — otherwise the recorder keeps the old
    number until it restarts, which is how a month of $20 happened."""
    _set(db, 25.0)
    assert rec.unit_stake() == 25.0
    _set(db, 40.0)
    assert rec.unit_stake() == 40.0


def test_the_env_override_still_wins(db, monkeypatch):
    """Tests and any host without an app db."""
    _set(db, 25.0)
    monkeypatch.setenv("SOFTBOOK_UNIT_STAKE", "5")
    assert rec.unit_stake() == 5.0


def test_a_garbage_env_value_falls_through_to_the_db(db, monkeypatch):
    _set(db, 25.0)
    monkeypatch.setenv("SOFTBOOK_UNIT_STAKE", "not-a-number")
    assert rec.unit_stake() == 25.0
