"""/api/feed-health — catches the 2026-08-22 silent feed outage.

MLB, NBA, WNBA, NHL, NPB and KBO all stopped producing odds_observations on
08-22 (Kalshi changed market titles from "A vs B Winner?" to "X wins", which
broke the title-parsing matcher). Nothing on the dashboard surfaced it for
four days: the strategies were "active" and simply never fired, which looks
identical to a quiet slate.

The discriminator is "Kalshi has open markets for this series, but no
observation landed" — an off-season sport has no open markets and is silent
by design.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from monitoring.dashboard.api import create_app
from core.database import Database


class _Kalshi:
    """Open markets for MLB and WTA only; everything else is off-season."""
    def __init__(self, with_markets=("KXMLBGAME", "KXWTAMATCH")):
        self._with = set(with_markets)

    def _get(self, path):
        for s in self._with:
            if f"series_ticker={s}" in path:
                return {"markets": [{"ticker": f"{s}-X"}]}
        return {"markets": []}


class _Config:
    LIVE_STRATEGIES = set()
    DATABASE_PATH = None


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "fh.db"))
    await d.initialize()
    yield d
    await d.close()


async def _client(db, kalshi):
    cfg = _Config()
    cfg.DATABASE_PATH = db.db_path
    app = create_app(db, kalshi_client=kalshi, config=cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _obs(db_path, ticker, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    c = sqlite3.connect(db_path)
    series = ticker.split("-")[0]
    c.execute(
        "INSERT INTO odds_observations "
        "(timestamp, kalshi_ticker, series, sport, phase) VALUES (?,?,?,?,?)",
        (ts, ticker, series, series, "pre"))
    c.commit()
    c.close()


@pytest.mark.asyncio
async def test_open_markets_with_no_recent_observations_is_stale(db):
    """The exact 08-22 signature: MLB quoted, no rows."""
    _obs(db.db_path, "KXMLBGAME-26AUG22X", hours_ago=96)
    _obs(db.db_path, "KXWTAMATCH-26AUG26X", hours_ago=0.2)
    async with await _client(db, _Kalshi()) as c:
        d = (await c.get("/api/feed-health")).json()
    by = {r["sport"]: r for r in d["rows"]}
    assert by["MLB"]["stale"] is True
    assert by["MLB"]["age_hours"] > 6
    assert by["WTA"]["stale"] is False
    assert d["stale_count"] == 1


@pytest.mark.asyncio
async def test_offseason_sport_is_not_stale(db):
    """No open markets => silent by design, must not raise an alarm."""
    async with await _client(db, _Kalshi(with_markets=())) as c:
        d = (await c.get("/api/feed-health")).json()
    assert d["stale_count"] == 0
    assert all(r["stale"] is False for r in d["rows"])


@pytest.mark.asyncio
async def test_fresh_feed_is_not_stale(db):
    _obs(db.db_path, "KXMLBGAME-26AUG26X", hours_ago=0.1)
    _obs(db.db_path, "KXWTAMATCH-26AUG26X", hours_ago=0.1)
    async with await _client(db, _Kalshi()) as c:
        d = (await c.get("/api/feed-health")).json()
    assert d["stale_count"] == 0


@pytest.mark.asyncio
async def test_stale_rows_sort_first(db):
    _obs(db.db_path, "KXWTAMATCH-26AUG26X", hours_ago=0.1)
    async with await _client(db, _Kalshi()) as c:
        d = (await c.get("/api/feed-health")).json()
    assert d["rows"][0]["sport"] == "MLB"  # never observed + open markets
    assert d["rows"][0]["stale"] is True
