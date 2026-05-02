"""Tests for SportsScanObserver: records consensus per-market without gates."""
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from core.database import Database
from strategies.sports_scan_observer import SportsScanObserver


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


def _make_observer(db, yes_ask="0.42", no_ask="0.59", yes_sub_title="Los Angeles L",
                   title="Los Angeles Lakers vs Boston Celtics",
                   ticker="KXNBAGAME-26APR21LALBOS-LAL"):
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = "dummy"
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = 100.0
    order_mgr._db = db
    client = MagicMock()
    market = {
        "ticker": ticker,
        "title": title,
        "yes_ask_dollars": yes_ask,
        "no_ask_dollars": no_ask,
        "yes_sub_title": yes_sub_title,
    }
    client._get = MagicMock(return_value={"markets": [market]})
    obs = SportsScanObserver(config, order_mgr, client)
    return obs


def _patch_helpers(obs, commence_time="2099-01-01T00:00:00Z",
                   us_home=0.55, eu_home=0.52, pin_home=0.54, age_s=90,
                   home="Los Angeles Lakers", away="Boston Celtics",
                   draw_home=None, draw_eu=None, draw_pin=None):
    """Stub _fetch_odds and _dual_region_consensus_dicts — avoid real network."""
    matched = {
        "id": "g1", "home_team": home, "away_team": away,
        "commence_time": commence_time, "bookmakers": [],
    }

    async def _fake_fetch(sport_key):
        return [matched]

    def _build_dict(home_p, draw_p):
        d = {"home_team": home, "away_team": away,
             "home_prob": home_p, "away_prob": 1.0 - home_p - (draw_p or 0)}
        if draw_p is not None:
            d["draw_prob"] = draw_p
        return d

    async def _fake_dual_dicts(sport_key, game):
        return (_build_dict(us_home, draw_home),
                _build_dict(eu_home, draw_eu),
                _build_dict(pin_home, draw_pin),
                age_s)

    obs._fetch_odds = _fake_fetch
    obs._dual_region_consensus_dicts = _fake_dual_dicts
    obs._match_kalshi_to_odds = MagicMock(return_value=matched)


@pytest.mark.asyncio
async def test_observer_records_observation(db):
    obs = _make_observer(db)
    _patch_helpers(obs)
    await obs.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM odds_observations ORDER BY id"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    # Only NBA matched (matched_game stub returns for every _fetch_odds call,
    # but client._get is mocked to return same market for every series call
    # → 3 rows, one per series with the stubbed NBA market).
    assert len(rows) >= 1
    r = rows[0]
    assert r["kalshi_ticker"] == "KXNBAGAME-26APR21LALBOS-LAL"
    assert r["sport"] in ("NBA", "MLB", "NHL")
    assert r["kalshi_yes_ask"] == pytest.approx(0.42)
    assert r["kalshi_no_ask"] == pytest.approx(0.59)
    # YES = Lakers = home → us_yes = us_home
    assert r["is_yes_home"] == 1
    assert r["us_yes_prob"] == pytest.approx(0.55)
    assert r["eu_yes_prob"] == pytest.approx(0.52)
    assert r["pinnacle_yes_prob"] == pytest.approx(0.54)
    assert r["pinnacle_age_s"] == 90


@pytest.mark.asyncio
async def test_observer_yes_away_flips_probabilities(db):
    obs = _make_observer(
        db,
        yes_sub_title="Boston",
        title="Los Angeles Lakers vs Boston Celtics",
        ticker="KXNBAGAME-26APR21LALBOS-BOS",
    )
    _patch_helpers(obs)
    await obs.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT is_yes_home, us_yes_prob, eu_yes_prob, pinnacle_yes_prob "
            "FROM odds_observations WHERE kalshi_ticker LIKE '%-BOS' LIMIT 1"
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["is_yes_home"] == 0
    # YES = Celtics = away → prob flips to (1 - home_prob)
    assert row["us_yes_prob"] == pytest.approx(1.0 - 0.55)
    assert row["eu_yes_prob"] == pytest.approx(1.0 - 0.52)
    assert row["pinnacle_yes_prob"] == pytest.approx(1.0 - 0.54)


@pytest.mark.asyncio
async def test_observer_phase_pre_vs_live(db):
    obs = _make_observer(db)
    _patch_helpers(obs, commence_time="2099-01-01T00:00:00Z")
    await obs.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT phase FROM odds_observations LIMIT 1")
        row = await cursor.fetchone()
    assert row["phase"] == "pre"

    # Fresh observer with a commence_time in the past → live phase.
    obs2 = _make_observer(db)
    _patch_helpers(obs2, commence_time="2000-01-01T00:00:00Z")
    await obs2.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT phase FROM odds_observations ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
    assert row["phase"] == "live"


@pytest.mark.asyncio
async def test_observer_skips_when_no_api_key(db):
    obs = _make_observer(db)
    obs._odds_api_key = ""  # simulate unconfigured
    _patch_helpers(obs)
    await obs.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM odds_observations")
        count = (await cursor.fetchone())[0]
    assert count == 0


@pytest.mark.asyncio
async def test_observer_skips_when_odds_match_fails(db):
    obs = _make_observer(db)
    _patch_helpers(obs)
    obs._match_kalshi_to_odds = MagicMock(return_value=None)
    await obs.scan_and_execute()

    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM odds_observations")
        count = (await cursor.fetchone())[0]
    assert count == 0


@pytest.mark.asyncio
async def test_observer_does_not_place_orders(db):
    obs = _make_observer(db)
    _patch_helpers(obs)
    # Prove by construction: order_manager has no market_order called.
    obs._order_manager.market_order = AsyncMock()
    await obs.scan_and_execute()
    obs._order_manager.market_order.assert_not_called()
