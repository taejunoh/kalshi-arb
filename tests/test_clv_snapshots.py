"""Unit tests for clv_snapshots table + record/query methods."""
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
async def test_initialize_creates_clv_snapshots_table(db):
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "clv_snapshots" in tables


@pytest.mark.asyncio
async def test_record_clv_snapshot_persists(db):
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="NBA-LAL-BOS", direction="YES",
        price=0.45, size=3.0, fee=0.07, status="paper_filled",
    )
    trade_id = 1  # first insert
    await db.record_clv_snapshot(
        trade_id=trade_id, snapshot_at="entry",
        yes_ask=0.45, no_ask=0.57, pinnacle_prob=0.50,
    )
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT * FROM clv_snapshots WHERE trade_id = ?", (trade_id,))
        rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 1
    assert rows[0]["snapshot_at"] == "entry"
    assert rows[0]["yes_ask"] == 0.45
    assert rows[0]["no_ask"] == 0.57
    assert rows[0]["pinnacle_prob"] == 0.50


@pytest.mark.asyncio
async def test_record_clv_snapshot_unique_per_type(db):
    """Same (trade_id, snapshot_at) can only be recorded once."""
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="NBA-1", direction="YES",
        price=0.45, size=3.0, fee=0.07, status="paper_filled",
    )
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.45, no_ask=0.57)
    with pytest.raises(Exception):
        await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.46, no_ask=0.58)


@pytest.mark.asyncio
async def test_record_clv_snapshot_allows_both_entry_and_closing(db):
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="NBA-1", direction="YES",
        price=0.45, size=3.0, fee=0.07, status="paper_filled",
    )
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.45, no_ask=0.57)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM clv_snapshots WHERE trade_id = ?", (1,),
        )
        count = (await cursor.fetchone())[0]
    assert count == 2


@pytest.mark.asyncio
async def test_clv_positive_rate_counts_favorable_movement(db):
    """YES trade: closing_yes_ask > entry_yes_ask means CLV+.
    NO trade: closing_no_ask > entry_no_ask means CLV+.
    Production writes direction as lowercase `yes`/`no`; rate must match that."""
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m1", direction="yes",
        price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=1.5,
    )
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)

    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m2", direction="yes",
        price=0.60, size=3.0, fee=0.07, status="paper_settled", pnl=-2.0,
    )
    await db.record_clv_snapshot(trade_id=2, snapshot_at="entry", yes_ask=0.60, no_ask=0.42)
    await db.record_clv_snapshot(trade_id=2, snapshot_at="closing", yes_ask=0.50, no_ask=0.52)

    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m3", direction="no",
        price=0.30, size=3.0, fee=0.07, status="paper_settled", pnl=1.0,
    )
    await db.record_clv_snapshot(trade_id=3, snapshot_at="entry", yes_ask=0.72, no_ask=0.30)
    await db.record_clv_snapshot(trade_id=3, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)

    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    # 2 of 3 favorable → 66.7%
    assert abs(rate - 2 / 3) < 1e-6


@pytest.mark.asyncio
async def test_clv_positive_rate_lowercase_direction(db):
    """Case-sensitive compare against 'YES' would silently route every real
    trade into the else branch. Production writes lowercase, so this is a
    regression test for the direction-case bug."""
    # A single YES trade where the YES side moved favorably — should be CLV+.
    # If code compared against 'YES' (uppercase), it'd fall through to NO branch
    # and see no_ask 0.62→0.47 (down) = CLV- → rate 0.0 instead of 1.0.
    await db.record_trade(strategy="sports_pinnacle_edge", market_id="m1", direction="yes",
                          price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=1.5)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)

    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    assert rate == 1.0


@pytest.mark.asyncio
async def test_clv_positive_rate_skips_null_snapshot_rows_from_denominator(db):
    """Rows with NULL closing asks (e.g. settled_cache hit) must not inflate the denominator."""
    # Real settled row with both snapshots, moved favorably
    await db.record_trade(strategy="sports_pinnacle_edge", market_id="m1", direction="yes",
                          price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=1.5)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=1, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)

    # Second settled row where the closing snapshot missed asks (NULL)
    await db.record_trade(strategy="sports_pinnacle_edge", market_id="m2", direction="yes",
                          price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=1.5)
    await db.record_clv_snapshot(trade_id=2, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=2, snapshot_at="closing", yes_ask=None, no_ask=None)

    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    # Only trade 1 counts → 1/1 = 100%, NOT 1/2 = 50%.
    assert rate == 1.0


@pytest.mark.asyncio
async def test_clv_positive_rate_returns_zero_when_no_data(db):
    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    assert rate == 0.0


@pytest.mark.asyncio
async def test_clv_positive_rate_skips_trades_without_closing(db):
    """Trades missing closing snapshot should not count (pending settlement)."""
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m1", direction="YES",
        price=0.40, size=3.0, fee=0.07, status="paper_filled",
    )
    await db.record_clv_snapshot(trade_id=1, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    # no closing
    rate = await db.get_clv_positive_rate("sports_pinnacle_edge", days=30)
    assert rate == 0.0


@pytest.mark.asyncio
async def test_record_clv_snapshot_writes_new_instrumentation_fields(tmp_path):
    from core.database import Database
    import aiosqlite
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    trade_id = await db.record_trade(
        "sports_longshot_v31", "KX-GAME-TEST", "yes", 0.45, 10.0, 0.0, "paper_filled"
    )
    await db.record_clv_snapshot(
        trade_id=trade_id, snapshot_at="entry",
        yes_ask=0.46, no_ask=0.54, pinnacle_prob=0.40,
        us_consensus_prob=0.43, eu_consensus_prob=0.40,
        pinnacle_age_s=42, kalshi_yes_bid=0.45, kalshi_yes_ask=0.46,
    )
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM clv_snapshots WHERE trade_id=?", (trade_id,)
        )
        row = await cur.fetchone()
    assert row["us_consensus_prob"] == 0.43
    assert row["eu_consensus_prob"] == 0.40
    assert row["pinnacle_age_s"] == 42
    assert row["kalshi_yes_bid"] == 0.45
    assert row["kalshi_yes_ask"] == 0.46
