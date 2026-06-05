"""Live market orders log realized fill price; paper stores NULL."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from core.order_manager import OrderManager
from core.database import Database


@pytest.mark.asyncio
async def test_trades_schema_has_realized_fill_price_column(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(trades)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "realized_fill_price" in cols


@pytest.mark.asyncio
async def test_record_trade_accepts_realized_fill_price(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    trade_id = await db.record_trade(
        "sports_longshot_v31", "KX-T1", "yes", 0.30, 3.0, 0.07,
        "filled", realized_fill_price=0.32,
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
    assert row["realized_fill_price"] == 0.32


@pytest.mark.asyncio
async def test_paper_order_stores_null_realized(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    om = OrderManager(config, kalshi_client=MagicMock(), database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._paper_market_order(
        "KX-T1", "yes", 3.0, 0.30, "sports_longshot", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    assert row["realized_fill_price"] is None


@pytest.mark.asyncio
async def test_live_order_extracts_realized_from_kalshi_response(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    # Kalshi market-order response with realized fill info
    client.place_market_order = MagicMock(return_value={
        "id": "ord123",
        "status": "filled",
        "taker_fill_cost": 96,   # cents paid total for this side
        "taker_fill_count": 3,   # contracts filled
    })
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._live_market_order(
        "KX-T1", "yes", 1.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    # 96 cents / 3 contracts / 100 = $0.32 per contract
    assert row["realized_fill_price"] == pytest.approx(0.32, abs=1e-6)


@pytest.mark.asyncio
async def test_live_order_fallback_when_fill_fields_missing(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    # Response without realized fields
    client.place_market_order = MagicMock(return_value={"id": "ord123", "status": "filled"})
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    result = await om._live_market_order(
        "KX-T1", "yes", 1.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT realized_fill_price FROM trades WHERE id=?", (result["trade_id"],))
        row = await cur.fetchone()
    # No fill info → NULL
    assert row["realized_fill_price"] is None


@pytest.mark.asyncio
async def test_ttl_limit_prices_aggressive_on_yes_side(tmp_path):
    """YES-side v31 entry posts limit at signal + AGG cents to cross the spread."""
    from core.order_manager import LIMIT_AGGRESSION_CENTS
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    client.invalidate_balance_cache = MagicMock()
    client.place_limit_order = MagicMock(return_value={"order_id": "ord-yes"})
    # Order completes immediately, unfilled, so we can snapshot the placed price.
    client.get_order = MagicMock(return_value={
        "fill_count_fp": 0, "remaining_count_fp": 10,
        "status": "canceled", "taker_fill_cost_dollars": 0,
        "maker_fill_cost_dollars": 0,
    })
    client.cancel_order = MagicMock(return_value=True)
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    await om._live_limit_ttl_order("KX-T1", "yes", 3.0, 0.40, "sports_longshot_v31", "KX-T1")
    # Signal 40¢ YES → place at 40 + AGG (willing to pay more).
    args = client.place_limit_order.call_args[0]
    assert args[2] == 40 + LIMIT_AGGRESSION_CENTS


@pytest.mark.asyncio
async def test_ttl_limit_prices_aggressive_on_no_side(tmp_path):
    """NO-side entry bumps yes_price DOWN so cost cap rises by AGG cents."""
    from core.order_manager import LIMIT_AGGRESSION_CENTS
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    client.invalidate_balance_cache = MagicMock()
    client.place_limit_order = MagicMock(return_value={"order_id": "ord-no"})
    client.get_order = MagicMock(return_value={
        "fill_count_fp": 0, "remaining_count_fp": 10,
        "status": "canceled", "taker_fill_cost_dollars": 0,
        "maker_fill_cost_dollars": 0,
    })
    client.cancel_order = MagicMock(return_value=True)
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    await om._live_limit_ttl_order("KX-T1", "no", 3.0, 0.40, "sports_longshot_v31", "KX-T1")
    # NO 40¢ signal → yes_price = (100 - 40) - AGG = 58 (paying up to 42¢).
    args = client.place_limit_order.call_args[0]
    assert args[2] == (100 - 40) - LIMIT_AGGRESSION_CENTS


@pytest.mark.asyncio
async def test_ttl_limit_uses_per_strategy_aggression_override(tmp_path):
    """Strategies in LIMIT_AGGRESSION_CENTS_BY_STRATEGY use their bumped cap."""
    from core.order_manager import LIMIT_AGGRESSION_CENTS_BY_STRATEGY
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    client.invalidate_balance_cache = MagicMock()
    client.place_limit_order = MagicMock(return_value={"order_id": "ord-mid"})
    client.get_order = MagicMock(return_value={
        "fill_count_fp": 0, "remaining_count_fp": 10,
        "status": "canceled", "taker_fill_cost_dollars": 0,
        "maker_fill_cost_dollars": 0,
    })
    client.cancel_order = MagicMock(return_value=True)
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    # midshot_v31 has override (25) — should beat default (15)
    assert LIMIT_AGGRESSION_CENTS_BY_STRATEGY["sports_midshot_v31"] == 25
    await om._live_limit_ttl_order("KX-T1", "yes", 3.0, 0.30, "sports_midshot_v31", "KX-T1")
    args = client.place_limit_order.call_args[0]
    assert args[2] == 30 + 25, f"midshot_v31 should use 25¢ aggression, got yes_price={args[2]} (expected 55)"


@pytest.mark.asyncio
async def test_ttl_limit_default_aggression_for_non_overridden_strategy(tmp_path):
    """Strategies not in override dict fall back to the default 15¢ cap."""
    from core.order_manager import LIMIT_AGGRESSION_CENTS, LIMIT_AGGRESSION_CENTS_BY_STRATEGY
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    config = MagicMock()
    config.INITIAL_BANKROLL = 2000.0
    config.LIVE_STRATEGIES = set()
    config.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): order_manager now calls config.real_fee (real Kalshi
    # taker fee 0.07·p·(1-p)) instead of estimate_fee. Stub it with the legacy
    # flag-OFF formula so balance/pricing arithmetic stays numeric.
    config.real_fee = MagicMock(
        side_effect=lambda count, price_cents, maker=False: min(
            (0.0175 if maker else 0.07) * max(0.0, min(1.0, price_cents / 100.0))
            * (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
            max(0.0, min(1.0, price_cents / 100.0)) * count,
            (1.0 - max(0.0, min(1.0, price_cents / 100.0))) * count,
        )
    )
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.MAX_DAILY_LOSS = 200
    config.MAX_DAILY_LOSS_LIVE = 200
    config.MAX_CONSECUTIVE_LOSSES = 1000
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    client.invalidate_balance_cache = MagicMock()
    client.place_limit_order = MagicMock(return_value={"order_id": "ord-und"})
    client.get_order = MagicMock(return_value={
        "fill_count_fp": 0, "remaining_count_fp": 10,
        "status": "canceled", "taker_fill_cost_dollars": 0,
        "maker_fill_cost_dollars": 0,
    })
    client.cancel_order = MagicMock(return_value=True)
    om = OrderManager(config, kalshi_client=client, database=db, notifier=None)
    await om.hydrate_bankroll()
    # underdog_v31 NOT in override dict (shield strategy, keeps 15¢ default)
    assert "sports_underdog_v31" not in LIMIT_AGGRESSION_CENTS_BY_STRATEGY
    await om._live_limit_ttl_order("KX-T1", "yes", 3.0, 0.30, "sports_underdog_v31", "KX-T1")
    args = client.place_limit_order.call_args[0]
    assert args[2] == 30 + LIMIT_AGGRESSION_CENTS, f"underdog_v31 should use default {LIMIT_AGGRESSION_CENTS}¢, got yes_price={args[2]}"
