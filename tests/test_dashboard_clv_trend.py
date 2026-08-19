"""/api/clv-trend must read pre_close, never the degenerate `closing` snapshot.

`closing.yes_ask` has been a constant 1.0 on every row since 2026-06 (empty
book at settlement -> ask defaults to 1.00). Scoring CLV off it turned the
panel into a restatement of `1 - entry_price`: tennis_underdog_v1 reported
"+0.732" on a 0.27 average entry while actually losing money, and every paper
strategy cleared the old 0.10 promote threshold for free.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from monitoring.dashboard.api import create_app
from core.database import Database


class _Config:
    LIVE_STRATEGIES = {"sports_tennis_underdog_v1"}
    DATABASE_PATH = None  # filled per-test


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "clv.db"))
    await database.initialize()
    yield database
    await database.close()


async def _client(db):
    cfg = _Config()
    cfg.DATABASE_PATH = db.db_path
    app = create_app(db, config=cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _trade(db, tid, strategy, status, direction, entry, close_px,
                 close_kind="pre_close"):
    await db.record_trade(strategy=strategy, market_id=f"KXATPMATCH-26AUG19X{tid}-X",
                          direction=direction, price=entry, size=1.5, fee=0.02,
                          status=status)
    await db.record_clv_snapshot(trade_id=tid, snapshot_at="entry",
                                 yes_ask=entry, no_ask=round(1 - entry, 2))
    await db.record_clv_snapshot(trade_id=tid, snapshot_at=close_kind,
                                 yes_ask=close_px, no_ask=round(1 - close_px, 2))


@pytest.mark.asyncio
async def test_degenerate_closing_snapshot_produces_no_row(db):
    """A trade with only a `closing` snapshot must not be scored at all."""
    await _trade(db, 1, "sports_tennis_underdog_v1", "settled", "yes",
                 0.27, 1.0, close_kind="closing")
    async with await _client(db) as c:
        rows = (await c.get("/api/clv-trend")).json()["rows"]
    assert rows == [], f"closing-only trade leaked into the panel: {rows}"


@pytest.mark.asyncio
async def test_pre_close_snapshot_is_scored(db):
    await _trade(db, 1, "sports_tennis_underdog_v1", "settled", "yes", 0.27, 0.30)
    async with await _client(db) as c:
        rows = (await c.get("/api/clv-trend")).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["avg_clv_30d"] == pytest.approx(0.03)
    assert rows[0]["book"] == "live"
    assert rows[0]["is_live"] is True
    # The artifact would have produced 1.0 - 0.27 = +0.73.
    assert rows[0]["avg_clv_30d"] < 0.1


@pytest.mark.asyncio
async def test_paper_and_live_are_separate_rows(db):
    await _trade(db, 1, "sports_underdog_v31", "paper_settled", "yes", 0.33, 0.35)
    await _trade(db, 2, "sports_tennis_underdog_v1", "settled", "yes", 0.27, 0.29)
    async with await _client(db) as c:
        rows = (await c.get("/api/clv-trend")).json()["rows"]
    books = {r["strategy"]: r["book"] for r in rows}
    assert books == {"sports_underdog_v31": "paper",
                     "sports_tennis_underdog_v1": "live"}
    # A demoted strategy still reports — that is how it earns its way back.
    assert any(r["strategy"] == "sports_underdog_v31" and not r["is_live"]
               for r in rows)


@pytest.mark.asyncio
async def test_no_side_uses_no_ask_not_yes_ask(db):
    """A NO-direction trade is scored on the no leg."""
    await _trade(db, 1, "sports_tennis_live_fade_v1", "settled", "no", 0.18, 0.22)
    async with await _client(db) as c:
        rows = (await c.get("/api/clv-trend")).json()["rows"]
    # entry no_ask = 1-0.18 = 0.82, close no_ask = 1-0.22 = 0.78 -> -0.04
    assert rows[0]["avg_clv_30d"] == pytest.approx(-0.04)


@pytest.mark.asyncio
async def test_z_score_reported_for_multi_trade_rows(db):
    for i in range(1, 6):
        await _trade(db, i, "sports_tennis_underdog_v1", "settled", "yes", 0.27, 0.30)
    async with await _client(db) as c:
        rows = (await c.get("/api/clv-trend")).json()["rows"]
    assert rows[0]["n_30d"] == 5
    # Identical deltas -> zero variance -> se is 0, so z must be reported as
    # None rather than dividing by zero.
    assert rows[0]["se_30d"] is None
    assert rows[0]["z_30d"] is None
