"""Stale-detection tests for the silence watchdog.

Regression for 2026-05-22 incident: bot tripped its daily-loss cap at
02:22 UTC and went silent for 15h, but the 13:06 UTC audit missed it
because the 24h window still contained 01-02 UTC fills. Stale-flag adds
"baseline ≥ 1.0/day AND last fire > 6h ago" so a mid-window stop fires.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts.strategy_silence_watchdog import (
    STALE_HOURS,
    _audit_strategy,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy TEXT NOT NULL,
            market_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            size REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0,
            pnl REAL DEFAULT 0,
            status TEXT NOT NULL
        )"""
    )
    return conn


def _insert(conn, strategy: str, ts: datetime, status: str = "paper_filled"):
    conn.execute(
        "INSERT INTO trades (timestamp, strategy, market_id, direction, price, size, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts.isoformat(), strategy, "KX-TEST", "yes", 0.5, 10.0, status),
    )


def test_stale_anomaly_fires_when_mid_window_stop():
    """Mid-window stop: fills earlier in 24h window then > STALE_HOURS gap.
    Mirrors 5/22 cme_basis_fade_v1 — 6 fills at 01-02 UTC then nothing for
    11h while the audit ran at 13:06 UTC."""
    conn = _make_conn()
    now = datetime.now(timezone.utc)
    # 14d baseline: fire every ~3h → strong baseline, no silence by design
    for h in range(0, 14 * 24, 3):
        _insert(conn, "x", now - timedelta(hours=h + 30))
    # Recent: a few fires 11h ago (inside window) then nothing
    _insert(conn, "x", now - timedelta(hours=11))
    _insert(conn, "x", now - timedelta(hours=11, minutes=2))

    r = _audit_strategy(conn, "x")
    assert r["silent_anomaly"] is False  # had fires in 24h
    assert r["stale_anomaly"] is True  # but stopped > STALE_HOURS ago
    assert r["hours_since_last"] is not None
    assert r["hours_since_last"] > STALE_HOURS


def test_stale_anomaly_skipped_when_recent_fire():
    """Strategy with a fire inside STALE_HOURS = not stale."""
    conn = _make_conn()
    now = datetime.now(timezone.utc)
    for h in range(0, 14 * 24, 3):
        _insert(conn, "x", now - timedelta(hours=h + 30))
    _insert(conn, "x", now - timedelta(hours=2))  # fresh

    r = _audit_strategy(conn, "x")
    assert r["silent_anomaly"] is False
    assert r["stale_anomaly"] is False


def test_silent_anomaly_still_works():
    """24h-zero silence still flags (baseline path unchanged)."""
    conn = _make_conn()
    now = datetime.now(timezone.utc)
    # Strong baseline (well above MIN_BASELINE_DAILY_FIRES=0.5)
    for h in range(0, 14 * 24, 3):
        _insert(conn, "x", now - timedelta(hours=h + 30))
    # No recent fires at all

    r = _audit_strategy(conn, "x")
    assert r["silent_anomaly"] is True
    # silent takes precedence; stale must NOT also fire (avoid double-count)
    assert r["stale_anomaly"] is False


def test_quiet_by_design_not_stale():
    """Low baseline (< STALE_MIN_BASELINE_DAILY=1.0) means long gaps are
    expected — must not flag stale."""
    conn = _make_conn()
    now = datetime.now(timezone.utc)
    # ~0.2 fires/day baseline (3 in 14d)
    _insert(conn, "x", now - timedelta(days=2))
    _insert(conn, "x", now - timedelta(days=5))
    _insert(conn, "x", now - timedelta(days=10))

    r = _audit_strategy(conn, "x")
    assert r["silent_anomaly"] is False  # baseline below MIN
    assert r["stale_anomaly"] is False  # baseline below STALE_MIN
