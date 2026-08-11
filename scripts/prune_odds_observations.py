#!/usr/bin/env python3
"""Daily 60-day retention for odds_observations — keeps bot.db from refilling.

2026-08-06: the disk hit 100% and SQLite went down. The recovery pruned
odds_observations from 20.1M to 9.6M rows and VACUUMed bot.db from 12 GB to
5.4 GB — but that was a one-time cut, and the table grows by ~190k rows/day, so
without this it is back at 12 GB in about two months. This timer deletes one
day's worth of aging rows per run, so the table holds a rolling ~11M-row window
instead of growing without bound.

No VACUUM here. VACUUM needs the bot stopped (and three watcher units carry
Wants=kalshi-bot that resurrect it — see ec2_disk_full_saga_2026-08-06), while a
daily delete of ~190k rows is small enough to run beside the live bot with a
busy_timeout. The freed pages go to SQLite's freelist and the next day's inserts
reuse them, so the FILE stays flat even though `df` never shows it shrinking.

Batched (50k per transaction) with a WAL checkpoint between batches, so the WAL
stays bounded and the bot's own writes interleave instead of queueing behind one
giant transaction. The DELETE hits idx_obs_time — never a table scan (this table
is the one the no-unbounded-GROUP-BY OOM rule is about).

Runs from kalshi-obs-prune.timer (daily). RETENTION_DAYS env-overridable; the
60-day window is the user's call from 2026-08-06, weighed against backtests
losing older observations (the chalk_shadow / v4_edge re-derivations read this
table).
"""
import os
import sqlite3
import sys

DB = os.getenv("BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
RETENTION_DAYS = int(os.getenv("OBS_RETENTION_DAYS", "60"))
BATCH = 50_000


def prune(db, days=None, batch=None):
    """Delete odds_observations older than `days`; returns rows deleted.
    Small batches + checkpoint so the live bot's writes interleave."""
    days = RETENTION_DAYS if days is None else days
    batch = BATCH if batch is None else batch
    db.execute("PRAGMA busy_timeout=30000")
    cutoff = db.execute(
        "SELECT datetime('now', ?)", (f"-{int(days)} days",)).fetchone()[0]
    total = 0
    while True:
        cur = db.execute(
            "DELETE FROM odds_observations WHERE rowid IN ("
            "  SELECT rowid FROM odds_observations WHERE timestamp < ? LIMIT ?)",
            (cutoff, batch))
        db.commit()
        total += cur.rowcount
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        if cur.rowcount < batch:
            return total


def main():
    db = sqlite3.connect(DB, timeout=60)
    try:
        n = prune(db)
        left = db.execute("SELECT COUNT(*) FROM odds_observations").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        print(f"[obs-prune] deleted {n} rows past {RETENTION_DAYS}d, "
              f"{left} remain, freelist {free} pages")
    finally:
        db.close()


if __name__ == "__main__":
    main()
