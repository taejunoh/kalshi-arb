"""Settlement backfill works for individual-sport (Tennis/MMA) tickers — added 2026-05-11.

Integration test: feed ATP and UFC tickers through the real fetch_settlements()
function using a mocked Kalshi client and verify rows are inserted correctly.

The backfill script is fully sport-agnostic (no series/sport filtering) so no
refactoring is needed — these tests confirm that existing logic handles
Tennis and MMA tickers identically to NBA/MLB/NHL.
"""
import sqlite3
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from core.database import Database
from scripts.backfill_ticker_settlements import fetch_settlements


@pytest_asyncio.fixture
async def db_with_individual_sports(tmp_path):
    """DB seeded with ATP and UFC observations, both unsettled."""
    d = Database(str(tmp_path / "t.db"))
    await d.initialize()
    conn = sqlite3.connect(d.db_path)
    for ticker, series, sport in [
        ("KXATPMATCH-26MAY10ALCSIN-ALC", "KXATPMATCH", "ATP"),
        ("KXWTAMATCH-26MAY10SWIAIGA-SWI", "KXWTAMATCH", "WTA"),
        ("KXUFCFIGHT-26MAY16WELDAL-WEL", "KXUFCFIGHT", "UFC"),
    ]:
        conn.execute(
            """INSERT INTO odds_observations
               (timestamp, kalshi_ticker, series, sport, phase)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-05-10T10:00:00Z", ticker, series, sport, "pre"),
        )
    conn.commit()
    conn.close()
    return d


def _make_client(market_by_ticker):
    client = MagicMock()
    client.get_market = MagicMock(side_effect=lambda t: market_by_ticker.get(t))
    return client


@pytest.mark.asyncio
async def test_atp_ticker_settles_correctly(db_with_individual_sports):
    """ATP match ticker resolves to 'yes' and is stored with correct close prices."""
    db = db_with_individual_sports
    client = _make_client({
        "KXATPMATCH-26MAY10ALCSIN-ALC": {
            "status": "finalized",
            "result": "yes",
            "yes_ask_dollars": "0.95",
            "no_ask_dollars": "0.05",
            "close_time": "2026-05-10T18:30:00Z",
        },
        "KXWTAMATCH-26MAY10SWIAIGA-SWI": {"status": "active", "result": None},
        "KXUFCFIGHT-26MAY16WELDAL-WEL": {"status": "active", "result": None},
    })

    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=False)

    assert counts["inserted"] == 1
    assert counts["pending"] == 2

    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ticker_settlements WHERE kalshi_ticker = ?",
        ("KXATPMATCH-26MAY10ALCSIN-ALC",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["result"] == "yes"
    assert row["close_yes_ask"] == pytest.approx(0.95)
    assert row["close_no_ask"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_ufc_ticker_settles_correctly(db_with_individual_sports):
    """UFC fight ticker resolves to 'no' and is stored correctly."""
    db = db_with_individual_sports
    client = _make_client({
        "KXATPMATCH-26MAY10ALCSIN-ALC": {"status": "active", "result": None},
        "KXWTAMATCH-26MAY10SWIAIGA-SWI": {"status": "active", "result": None},
        "KXUFCFIGHT-26MAY16WELDAL-WEL": {
            "status": "finalized",
            "result": "no",
            "yes_ask_dollars": "0.03",
            "no_ask_dollars": "0.97",
            "close_time": "2026-05-16T22:30:00Z",
        },
    })

    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=False)

    assert counts["inserted"] == 1
    assert counts["pending"] == 2

    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ticker_settlements WHERE kalshi_ticker = ?",
        ("KXUFCFIGHT-26MAY16WELDAL-WEL",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["result"] == "no"
    assert row["close_yes_ask"] == pytest.approx(0.03)
    assert row["close_no_ask"] == pytest.approx(0.97)


@pytest.mark.asyncio
async def test_all_individual_sports_settle_in_one_pass(db_with_individual_sports):
    """All three individual-sport tickers (ATP, WTA, UFC) settle in a single run."""
    db = db_with_individual_sports
    client = _make_client({
        "KXATPMATCH-26MAY10ALCSIN-ALC": {
            "status": "finalized", "result": "yes",
            "yes_ask_dollars": "0.9", "no_ask_dollars": "0.1",
            "close_time": "2026-05-10T18:30:00Z",
        },
        "KXWTAMATCH-26MAY10SWIAIGA-SWI": {
            "status": "finalized", "result": "no",
            "yes_ask_dollars": "0.08", "no_ask_dollars": "0.92",
            "close_time": "2026-05-10T17:00:00Z",
        },
        "KXUFCFIGHT-26MAY16WELDAL-WEL": {
            "status": "settled", "result": "yes",
            "yes_ask_dollars": "0.85", "no_ask_dollars": "0.15",
            "close_time": "2026-05-16T22:30:00Z",
        },
    })

    counts = fetch_settlements(db.db_path, client, limit=None, dry_run=False)

    assert counts["inserted"] == 3
    assert counts["pending"] == 0
    assert counts["errors"] == 0

    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM ticker_settlements").fetchone()[0]
    conn.close()
    assert n == 3


@pytest.mark.asyncio
async def test_individual_sports_backfill_is_idempotent(db_with_individual_sports):
    """Re-running the backfill for individual-sport tickers doesn't duplicate rows."""
    db = db_with_individual_sports
    client = _make_client({
        "KXATPMATCH-26MAY10ALCSIN-ALC": {
            "status": "finalized", "result": "yes",
            "yes_ask_dollars": "0.9", "no_ask_dollars": "0.1",
        },
        "KXWTAMATCH-26MAY10SWIAIGA-SWI": {
            "status": "finalized", "result": "no",
            "yes_ask_dollars": "0.08", "no_ask_dollars": "0.92",
        },
        "KXUFCFIGHT-26MAY16WELDAL-WEL": {"status": "active", "result": None},
    })

    c1 = fetch_settlements(db.db_path, client, limit=None, dry_run=False)
    c2 = fetch_settlements(db.db_path, client, limit=None, dry_run=False)

    assert c1["inserted"] == 2
    # Second run: ATP+WTA already settled, only UFC re-queried (still pending)
    assert c2["inserted"] == 0
    assert c2["pending"] == 1

    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM ticker_settlements").fetchone()[0]
    conn.close()
    assert n == 2
