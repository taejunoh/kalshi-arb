"""Real-balance pre-check on live market orders."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from core.order_manager import OrderManager
from core.database import Database


def _mk_config(live_strategies=None):
    cfg = MagicMock()
    cfg.INITIAL_BANKROLL = 2000.0
    cfg.LIVE_STRATEGIES = live_strategies or {"sports_longshot_v31"}
    cfg.MAX_DAILY_LOSS = 200
    cfg.MAX_DAILY_LOSS_LIVE = 200
    cfg.MAX_CONSECUTIVE_LOSSES = 1000
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.estimate_fee = MagicMock(return_value=0.07)
    # fee-fix (993a1a8): _live_market_order now uses config.real_fee(count, price_cents)
    # instead of estimate_fee. Stub it numeric so the balance guard (balance < required)
    # doesn't hit a MagicMock. Production uses real Config -> deterministic float.
    cfg.real_fee = MagicMock(return_value=0.07)
    return cfg


@pytest.mark.asyncio
async def test_live_order_rejected_when_balance_insufficient(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    client = MagicMock()
    client.get_balance = MagicMock(return_value=2.50)  # $2.50 — less than $3 + fee
    client.place_market_order = MagicMock()  # should NOT be called
    om = OrderManager(_mk_config(), kalshi_client=client, database=db, notifier=None)
    result = await om._live_market_order(
        "KX-T1", "yes", 3.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    assert result["status"] == "failed"
    assert result.get("reason") == "insufficient_balance"
    client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_live_order_proceeds_when_balance_sufficient(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    client = MagicMock()
    client.get_balance = MagicMock(return_value=100.0)
    client.place_market_order = MagicMock(return_value={"id": "ord1", "status": "filled"})
    om = OrderManager(_mk_config(), kalshi_client=client, database=db, notifier=None)
    result = await om._live_market_order(
        "KX-T1", "yes", 3.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    assert result["status"] == "filled"
    client.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_live_order_failed_when_balance_check_fails(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    client = MagicMock()
    client.get_balance = MagicMock(return_value=None)  # API failure
    client.place_market_order = MagicMock()
    om = OrderManager(_mk_config(), kalshi_client=client, database=db, notifier=None)
    result = await om._live_market_order(
        "KX-T1", "yes", 3.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    assert result["status"] == "failed"
    assert result.get("reason") == "balance_check_failed"
    client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_balance_guard_required_includes_fee(tmp_path):
    """Required = size_usdc + estimated fee. Balance just barely covering size must fail."""
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    client = MagicMock()
    client.get_balance = MagicMock(return_value=3.00)  # exactly size, no room for fee
    client.place_market_order = MagicMock()
    cfg = _mk_config()
    cfg.estimate_fee = MagicMock(return_value=0.50)
    # fee-fix (993a1a8): guard now reads config.real_fee, so stub that with the same
    # 0.50 magnitude -> required = 3.00 + 0.50 = 3.50 > 3.00 balance (fee-driven reject).
    cfg.real_fee = MagicMock(return_value=0.50)
    om = OrderManager(cfg, kalshi_client=client, database=db, notifier=None)
    result = await om._live_market_order(
        "KX-T1", "yes", 3.00, 0.30, "sports_longshot_v31", "KX-T1"
    )
    assert result["status"] == "failed"
    assert result.get("reason") == "insufficient_balance"


@pytest.mark.asyncio
async def test_sizing_bankroll_live_uses_real_balance_paper_uses_tracker(tmp_path):
    """Permanent bankroll fix: LIVE strategies size off the real Kalshi balance,
    NOT the (possibly drifted) shared paper tracker; paper strategies keep the
    tracker; balance-fetch failure falls back to the tracker."""
    db = Database(str(tmp_path / "t.db"))
    await db.initialize()
    client = MagicMock()
    client.get_balance = MagicMock(return_value=150.0)  # real Kalshi cash
    om = OrderManager(_mk_config(live_strategies={"sports_longshot_v31"}),
                      kalshi_client=client, database=db, notifier=None)
    om.bankroll = 4713.0  # inflated paper tracker (the bug this fix neutralises)

    # live strategy -> real balance (not the $4,713 tracker)
    assert om.sizing_bankroll("sports_longshot_v31") == 150.0
    # paper strategy -> paper tracker
    assert om.sizing_bankroll("sports_pinnacle_edge") == 4713.0
    # balance unavailable -> fall back to tracker (per-order guard still caps spend)
    client.get_balance = MagicMock(return_value=None)
    assert om.sizing_bankroll("sports_longshot_v31") == 4713.0
