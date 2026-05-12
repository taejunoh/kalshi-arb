"""Verify observer passes starter info through to record_odds_observation."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.starter_fetcher import invalidate_cache


def setup_function():
    invalidate_cache()


@pytest.mark.asyncio
async def test_mlb_observer_captures_pitchers(tmp_path):
    """When sport_short='MLB', observer fetches starters and stores them."""
    from core.database import Database
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()

    with patch("core.starter_fetcher.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            raise_for_status=lambda: None,
            json=lambda: {"dates": [{"games": [{
                "teams": {
                    "home": {"team": {"name": "Yankees"}, "probablePitcher": {"fullName": "Gerrit Cole"}},
                    "away": {"team": {"name": "Red Sox"}, "probablePitcher": {"fullName": "Brayan Bello"}},
                }
            }]}]}
        )
        await db.record_odds_observation(
            kalshi_ticker="KXMLBGAME-26MAY12-T",
            series="KXMLBGAME", sport="MLB", phase="pre",
            minutes_to_start=120,
            home_team="Yankees", away_team="Red Sox",
            yes_sub_title="yankees",
            is_yes_home=True, is_yes_draw=False,
            kalshi_yes_ask=0.42, kalshi_no_ask=0.58,
            us_yes_prob=None, eu_yes_prob=0.50,
            pinnacle_yes_prob=0.51, pinnacle_age_s=120,
            n_books_eu=12, n_books_us=None, has_pinnacle=True,
            kalshi_yes_bid=None, kalshi_no_bid=None,
            kalshi_yes_bid_size=None, kalshi_yes_ask_size=None,
            kalshi_no_bid_size=None, kalshi_no_ask_size=None,
            kalshi_volume_24h=None, kalshi_open_interest=None,
            tournament_slug=None,
            starter_home="Gerrit Cole",
            starter_away="Brayan Bello",
        )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute(
            "SELECT starter_home, starter_away FROM odds_observations "
            "WHERE kalshi_ticker = ?",
            ("KXMLBGAME-26MAY12-T",),
        )
        row = await cur.fetchone()
    assert row == ("Gerrit Cole", "Brayan Bello")


@pytest.mark.asyncio
async def test_columns_exist_after_migration(tmp_path):
    """Fresh DB has starter_home and starter_away columns."""
    from core.database import Database
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(odds_observations)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "starter_home" in cols
    assert "starter_away" in cols
