"""tournament_slug column + Tennis metadata lookup — added 2026-05-11."""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from core.database import Database
from strategies.sports_trader import TENNIS_SLUG_META


def test_tennis_slug_meta_has_french_open():
    meta = TENNIS_SLUG_META["tennis_atp_french_open"]
    assert meta["tier"] == "GS"
    assert meta["surface"] == "clay"
    assert meta["format"] == "BO5"


def test_tennis_slug_meta_atp_vs_wta_format_differs_at_gs():
    """Men's GS = BO5, Women's GS = BO3 — higher variance for men's longshots."""
    assert TENNIS_SLUG_META["tennis_atp_us_open"]["format"] == "BO5"
    assert TENNIS_SLUG_META["tennis_wta_us_open"]["format"] == "BO3"


def test_tennis_slug_meta_italian_open_is_m1000_clay():
    """The currently-active tournament (5/11) — verify correct entry."""
    atp = TENNIS_SLUG_META["tennis_atp_italian_open"]
    assert atp["tier"] == "M1000"
    assert atp["surface"] == "clay"
    wta = TENNIS_SLUG_META["tennis_wta_italian_open"]
    assert wta["tier"] == "WTA1000"
    assert wta["surface"] == "clay"


@pytest.mark.asyncio
async def test_record_odds_observation_accepts_tournament_slug(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    await db.record_odds_observation(
        kalshi_ticker="KXATPMATCH-26MAY12ALCSIN-ALC",
        series="KXATPMATCH",
        sport="ATP",
        phase="pre",
        minutes_to_start=120,
        home_team="Carlos Alcaraz",
        away_team="Jannik Sinner",
        yes_sub_title="carlos alcaraz",
        is_yes_home=True,
        is_yes_draw=False,
        kalshi_yes_ask=0.42,
        kalshi_no_ask=0.58,
        us_yes_prob=None,
        eu_yes_prob=0.50,
        pinnacle_yes_prob=0.51,
        pinnacle_age_s=120,
        n_books_eu=12,
        n_books_us=None,
        has_pinnacle=True,
        kalshi_yes_bid=None, kalshi_no_bid=None,
        kalshi_yes_bid_size=None, kalshi_yes_ask_size=None,
        kalshi_no_bid_size=None, kalshi_no_ask_size=None,
        kalshi_volume_24h=None, kalshi_open_interest=None,
        tournament_slug="tennis_atp_italian_open",
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT tournament_slug FROM odds_observations WHERE kalshi_ticker=?",
            ("KXATPMATCH-26MAY12ALCSIN-ALC",),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "tennis_atp_italian_open"


@pytest.mark.asyncio
async def test_tournament_slug_column_exists_after_initialize(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(odds_observations)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "tournament_slug" in cols


@pytest.mark.asyncio
async def test_migration_adds_column_to_existing_db(tmp_path):
    """Simulate an existing DB without the tournament_slug column (created
    by a pre-2026-05-12 schema), then re-initialize and verify the
    migration adds the column."""
    db_path = tmp_path / "old.db"
    # Create a minimal schema without tournament_slug (mimics old DB)
    import sqlite3 as sqlite_sync
    conn = sqlite_sync.connect(db_path)
    conn.execute(
        """CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            kalshi_ticker TEXT NOT NULL,
            series TEXT, sport TEXT, phase TEXT
        )"""
    )
    conn.commit()
    conn.close()
    # Now initialize Database — should add tournament_slug via migration
    db = Database(str(db_path))
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(odds_observations)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "tournament_slug" in cols
