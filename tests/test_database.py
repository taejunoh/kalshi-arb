import pytest
import pytest_asyncio
import aiosqlite
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_tables(db):
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert "trades" in tables
    assert "orders" in tables


@pytest.mark.asyncio
async def test_record_trade(db):
    await db.record_trade(
        strategy="sum_arb", market_id="market_123", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled",
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["strategy"] == "sum_arb"
    assert trades[0]["price"] == 0.48


@pytest.mark.asyncio
async def test_record_order(db):
    await db.record_order(
        order_id="order_abc", market_id="market_123", strategy="maker_preorder",
        side="BUY", price=0.47, size=50.0, order_type="limit", status="open",
    )
    orders = await db.get_open_orders()
    assert len(orders) == 1
    assert orders[0]["order_id"] == "order_abc"


@pytest.mark.asyncio
async def test_update_order_status(db):
    await db.record_order(
        order_id="order_abc", market_id="market_123", strategy="maker_preorder",
        side="BUY", price=0.47, size=50.0, order_type="limit", status="open",
    )
    await db.update_order_status("order_abc", "filled")
    orders = await db.get_open_orders()
    assert len(orders) == 0


@pytest.mark.asyncio
async def test_record_trade_returns_trade_id(db):
    """record_trade must return the new row id so callers can attach CLV snapshots."""
    trade_id_1 = await db.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled",
    )
    trade_id_2 = await db.record_trade(
        strategy="sum_arb", market_id="m2", direction="NO",
        price=0.52, size=50.0, fee=0.75, status="filled",
    )
    assert trade_id_1 == 1
    assert trade_id_2 == 2


@pytest.mark.asyncio
async def test_get_daily_pnl(db):
    await db.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled", pnl=2.0,
    )
    await db.record_trade(
        strategy="sum_arb", market_id="m2", direction="NO",
        price=0.49, size=50.0, fee=0.75, status="filled", pnl=-1.0,
    )
    daily_pnl = await db.get_daily_pnl()
    assert daily_pnl == 1.0


@pytest.mark.asyncio
async def test_record_trade_persists_minutes_to_start(db):
    await db.record_trade(
        strategy="sports_trader", market_id="KXMLBGAME-26APR102140TEX-TEX",
        direction="yes", price=0.50, size=2.0, fee=0.07, status="paper_filled",
        minutes_to_start=37.5,
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["minutes_to_start"] == 37.5


@pytest.mark.asyncio
async def test_record_trade_defaults_minutes_to_start_null(db):
    await db.record_trade(
        strategy="market_maker", market_id="KXMKT-X", direction="yes",
        price=0.50, size=10.0, fee=0.0, status="paper_filled",
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["minutes_to_start"] is None


@pytest.mark.asyncio
async def test_initialize_adds_minutes_to_start_to_legacy_db(tmp_path):
    # Simulate a pre-migration DB: create trades table WITHOUT the column
    import aiosqlite
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, strategy TEXT NOT NULL,
                market_id TEXT NOT NULL, direction TEXT NOT NULL,
                price REAL NOT NULL, size REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0, pnl REAL DEFAULT 0,
                status TEXT NOT NULL
            )
        """)
        await conn.commit()
    database = Database(db_path)
    await database.initialize()
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(trades)")
        cols = {row[1] for row in await cursor.fetchall()}
    assert "minutes_to_start" in cols


@pytest.mark.asyncio
async def test_count_strategy_fires_for_event_includes_cancelled(db):
    """2026-05-22: per-event cap must count attempts, not just fills.
    LIVE FOK rejects land as status='cancelled' — if those don't count,
    a thin book produces unbounded API hammering (KXETH-26MAY2317-B2080
    case, 22 fires in 20 min on same ticker, cap=2 bypassed)."""
    common = dict(
        strategy="deribit_tail_fade_high_v1", direction="no",
        price=0.30, size=5.0, fee=0.0,
    )
    # 2 cancelled (FOK rejects) + 1 paper_settled + 1 settled = 4 attempts
    await db.record_trade(market_id="KXETH-26MAY2317-B2080", status="cancelled", **common)
    await db.record_trade(market_id="KXETH-26MAY2317-B2080", status="cancelled", **common)
    await db.record_trade(market_id="KXETH-26MAY2317-B2090", status="paper_settled", **common)
    await db.record_trade(market_id="KXETH-26MAY2317-B2100", status="settled", **common)
    # 1 failed (pre-API local error) — excluded
    await db.record_trade(market_id="KXETH-26MAY2317-B2110", status="failed", **common)
    # Different event — excluded
    await db.record_trade(market_id="KXETH-26MAY2316-B2080", status="cancelled", **common)

    count = await db.count_strategy_fires_for_event(
        "deribit_tail_fade_high_v1", "KXETH-26MAY2317"
    )
    assert count == 4, f"expected 4 (cancelled + paper_settled + settled), got {count}"


@pytest.mark.asyncio
async def test_clv_snapshots_has_new_instrumentation_columns(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(clv_snapshots)")
        cols = {r[1] for r in await cur.fetchall()}
    expected = {"us_consensus_prob", "eu_consensus_prob",
                "pinnacle_age_s", "kalshi_yes_bid", "kalshi_yes_ask"}
    assert expected.issubset(cols), f"missing: {expected - cols}"
