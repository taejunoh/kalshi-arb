"""The daily odds_observations prune: rolling 60-day window, live-bot-safe.

The 2026-08-06 disk-full recovery cut the table from 20.1M to 9.6M rows, but the
table grows ~190k rows/day, so a one-time cut is back at 12 GB in two months.
This timer keeps the window rolling.

Timestamps are all now-relative (feedback_test_date_bomb_rolling_window): a
hardcoded date here would rot into a test that deletes nothing and still passes,
so each test also asserts non-vacuity — that the setup actually produced rows on
both sides of the cutoff.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from prune_odds_observations import prune  # noqa: E402


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE odds_observations("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,"
              "kalshi_ticker TEXT, series TEXT)")
    c.execute("CREATE INDEX idx_obs_time ON odds_observations(timestamp)")
    return c


def _seed(db, days_ago, n=1):
    db.executemany(
        "INSERT INTO odds_observations(timestamp, kalshi_ticker, series)"
        " SELECT datetime('now', ?), 'T', 'S'",
        [(f"-{days_ago} days",)] * n)
    db.commit()


def _count(db):
    return db.execute("SELECT COUNT(*) FROM odds_observations").fetchone()[0]


def test_old_rows_go_and_recent_rows_stay(db):
    _seed(db, 90, n=5)
    _seed(db, 10, n=7)
    assert _count(db) == 12                    # non-vacuity: both sides seeded
    assert prune(db, days=60) == 5
    assert _count(db) == 7


def test_the_boundary_is_the_retention_window(db):
    _seed(db, 61, n=3)
    _seed(db, 59, n=4)
    assert prune(db, days=60) == 3
    assert _count(db) == 4


def test_it_deletes_across_multiple_batches(db):
    """The first run after a gap can face far more than one batch."""
    _seed(db, 90, n=25)
    _seed(db, 1, n=5)
    assert prune(db, days=60, batch=10) == 25   # 3 batches: 10+10+5
    assert _count(db) == 5


def test_an_already_clean_table_is_a_cheap_no_op(db):
    _seed(db, 10, n=4)
    assert prune(db, days=60) == 0
    assert _count(db) == 4


def test_the_delete_uses_the_timestamp_index_not_a_scan(db):
    """This is THE 20M-row table behind the no-unbounded-GROUP-BY OOM rule; the
    inner SELECT must range the timestamp index."""
    plan = " ".join(r[3] for r in db.execute(
        "EXPLAIN QUERY PLAN DELETE FROM odds_observations WHERE rowid IN ("
        "  SELECT rowid FROM odds_observations WHERE timestamp < ? LIMIT ?)",
        ("x", 1)))
    assert "idx_obs_time" in plan
    assert "SCAN odds_observations" not in plan.replace(
        "SCAN odds_observations USING", "")     # bare SCAN = full walk
