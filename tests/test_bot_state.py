"""Tests for bot_state key-value persistence (used for bankroll across restarts)."""
import pytest
import pytest_asyncio
import aiosqlite

from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database


@pytest.mark.asyncio
async def test_bot_state_table_created(db):
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "bot_state" in tables


@pytest.mark.asyncio
async def test_set_and_get_bot_state(db):
    await db.set_bot_state("bankroll", 1234.56)
    assert await db.get_bot_state("bankroll") == 1234.56


@pytest.mark.asyncio
async def test_get_bot_state_missing_returns_none(db):
    assert await db.get_bot_state("nonexistent") is None


@pytest.mark.asyncio
async def test_set_bot_state_overwrites(db):
    await db.set_bot_state("bankroll", 100.0)
    await db.set_bot_state("bankroll", 75.0)
    assert await db.get_bot_state("bankroll") == 75.0


@pytest.mark.asyncio
async def test_set_bot_state_updates_timestamp(db):
    """Each write should refresh updated_at."""
    await db.set_bot_state("bankroll", 100.0)
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT updated_at FROM bot_state WHERE key='bankroll'")
        first = (await cursor.fetchone())[0]
    # small delay to ensure ISO timestamp changes
    import asyncio
    await asyncio.sleep(0.01)
    await db.set_bot_state("bankroll", 101.0)
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT updated_at FROM bot_state WHERE key='bankroll'")
        second = (await cursor.fetchone())[0]
    assert first != second
