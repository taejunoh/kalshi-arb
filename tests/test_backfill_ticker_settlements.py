"""Tests for ticker_settlements backfill script."""
from unittest.mock import MagicMock
import sqlite3

import pytest
import pytest_asyncio

from core.database import Database
from scripts.backfill_ticker_settlements import fetch_settlements


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    await d.initialize()
    # Seed observer data with 3 unique tickers
    conn = sqlite3.connect(d.db_path)
    for t in ["KXMLBGAME-26APR24-AAA", "KXMLBGAME-26APR24-BBB", "KXNHLGAME-26APR24-CCC"]:
        conn.execute(
            """INSERT INTO odds_observations
               (timestamp, kalshi_ticker, series, sport, phase)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-04-24T10:00:00Z", t, "KXMLBGAME", "MLB", "live"),
        )
    conn.commit()
    conn.close()
    yield d


def _make_client(market_by_ticker):
    client = MagicMock()
    client.get_market = MagicMock(side_effect=lambda t: market_by_ticker.get(t))
    return client


@pytest.mark.asyncio
async def test_inserts_finalized_tickers(db):
    client = _make_client({
        "KXMLBGAME-26APR24-AAA": {
            "status": "finalized", "result": "yes",
            "yes_ask_dollars": "0.95", "no_ask_dollars": "0.05",
            "close_time": "2026-04-24T22:00:00Z",
        },
        "KXMLBGAME-26APR24-BBB": {
            "status": "finalized", "result": "no",
            "yes_ask_dollars": "0.02", "no_ask_dollars": "0.98",
            "close_time": "2026-04-24T22:30:00Z",
        },
        "KXNHLGAME-26APR24-CCC": None,  # not available
    })

    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=False)
    assert counts["inserted"] == 2
    assert counts["errors"] == 1

    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM ticker_settlements ORDER BY kalshi_ticker"))
    assert len(rows) == 2
    assert rows[0]["result"] == "yes"
    assert rows[0]["close_yes_ask"] == 0.95
    assert rows[1]["result"] == "no"


@pytest.mark.asyncio
async def test_skips_pending_markets(db):
    """Markets with status != finalized/settled should be skipped (not inserted)."""
    client = _make_client({
        "KXMLBGAME-26APR24-AAA": {"status": "active", "result": None},
        "KXMLBGAME-26APR24-BBB": {"status": "open", "result": None},
        "KXNHLGAME-26APR24-CCC": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.8", "no_ask_dollars": "0.2"},
    })
    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=False)
    assert counts["inserted"] == 1
    assert counts["pending"] == 2

    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM ticker_settlements").fetchone()[0]
    assert n == 1


@pytest.mark.asyncio
async def test_idempotent_rerun(db):
    """Re-running with same data shouldn't duplicate rows — the query filters
    to tickers NOT already in ticker_settlements."""
    client = _make_client({
        "KXMLBGAME-26APR24-AAA": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXMLBGAME-26APR24-BBB": {"status": "finalized", "result": "no",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXNHLGAME-26APR24-CCC": {"status": "active", "result": None},
    })
    c1 = fetch_settlements(db.db_path, client, limit=None, dry_run=False)
    c2 = fetch_settlements(db.db_path, client, limit=None, dry_run=False)
    # Second run: 2 already settled; only 1 ticker (CCC) re-queried, still pending
    assert c1["inserted"] == 2
    assert c2["inserted"] == 0
    assert c2["pending"] == 1

    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM ticker_settlements").fetchone()[0]
    assert n == 2


@pytest.mark.asyncio
async def test_dry_run_does_not_write(db):
    client = _make_client({
        "KXMLBGAME-26APR24-AAA": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXMLBGAME-26APR24-BBB": {"status": "finalized", "result": "no",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXNHLGAME-26APR24-CCC": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
    })
    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=True)
    assert counts["inserted"] == 3  # counted but not written

    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM ticker_settlements").fetchone()[0]
    assert n == 0  # dry-run didn't write


@pytest.mark.asyncio
async def test_limit_respected(db):
    client = _make_client({
        "KXMLBGAME-26APR24-AAA": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXMLBGAME-26APR24-BBB": {"status": "finalized", "result": "no",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
        "KXNHLGAME-26APR24-CCC": {"status": "finalized", "result": "yes",
                                   "yes_ask_dollars": "0.5", "no_ask_dollars": "0.5"},
    })
    counts = fetch_settlements(db.db_path, client, limit=2, dry_run=False)
    assert counts["inserted"] == 2
