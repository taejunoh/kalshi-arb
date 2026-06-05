import pytest
import pytest_asyncio
from core.order_manager import OrderManager
from core.config import Config
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def paper_manager(db):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    return OrderManager(config, kalshi_client=None, database=db)


def test_initial_bankroll(paper_manager):
    assert paper_manager.bankroll == 1000.0


@pytest.mark.asyncio
async def test_paper_market_order(paper_manager):
    result = await paper_manager.market_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", size_usdc=100.0,
        price=0.50, strategy="sum_arb", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_filled"
    assert result["size_usdc"] == 100.0
    # fee-fix 993a1a8: real Kalshi taker fee is 0.07*p*(1-p)*count, not flat $0.07*count.
    # bankroll = 1000 - 100 (size) - 3.50 (fee: 0.07 * 0.50 * 0.50 * 200 contracts) = 896.50
    assert paper_manager.bankroll == 896.50


@pytest.mark.asyncio
async def test_paper_market_order_exposes_trade_id(paper_manager):
    """CLV snapshots need the trade row id — market_order must return it."""
    result = await paper_manager.market_order(
        ticker="NBA-LAL-BOS", side="yes", size_usdc=3.0,
        price=0.45, strategy="sports_pinnacle_edge", market_id="NBA-LAL-BOS",
    )
    assert result["status"] == "paper_filled"
    assert result.get("trade_id") == 1


@pytest.mark.asyncio
async def test_paper_limit_order(paper_manager):
    result = await paper_manager.limit_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", price=0.48,
        size_usdc=50.0, strategy="maker_preorder", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_open"
    assert result["price"] == 0.48


@pytest.mark.asyncio
async def test_paper_limit_order_skips_when_bankroll_insufficient(paper_manager):
    """_paper_limit_order must reject orders larger than current bankroll.
    Previously missing this check allowed market_maker to drain bankroll
    into deeply negative territory (observed -$817) since limit_order placed
    quotes every 30 seconds with no balance guard. Symmetry with market_order
    which already has this check."""
    paper_manager.bankroll = 10.0
    result = await paper_manager.limit_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", price=0.48,
        size_usdc=50.0, strategy="maker_preorder", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "insufficient_bankroll"
    # Bankroll unchanged — no placement happened
    assert paper_manager.bankroll == 10.0


@pytest.mark.asyncio
async def test_paper_limit_order_skips_when_bankroll_negative(paper_manager):
    """Even with a tiny size_usdc, negative bankroll must block new orders."""
    paper_manager.bankroll = -100.0
    result = await paper_manager.limit_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", price=0.48,
        size_usdc=0.50, strategy="market_maker", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "skipped"
    assert paper_manager.bankroll == -100.0


@pytest.mark.asyncio
async def test_position_sizing(paper_manager):
    size = paper_manager.calculate_position_size(net_edge=0.05)
    assert size == 12.5


@pytest.mark.asyncio
async def test_circuit_breaker(paper_manager):
    paper_manager._daily_loss_paper = 201.0
    assert paper_manager.is_circuit_breaker_active() is True
    paper_manager._daily_loss_paper = 50.0
    assert paper_manager.is_circuit_breaker_active() is False


@pytest.mark.asyncio
async def test_consecutive_loss_tracking(paper_manager):
    paper_manager.record_loss()
    paper_manager.record_loss()
    assert paper_manager._consecutive_losses == 2
    paper_manager.record_win()
    assert paper_manager._consecutive_losses == 0


@pytest.mark.asyncio
async def test_cap_trip_alert_fires_once_per_bucket(paper_manager):
    """Cap-trip alert must fire exactly once when the bucket first crosses cap
    (one-shot per UTC day). Logs WARNING via the [CAP-TRIP] tag; absence of a
    notifier degrades to log-only. Regression for 2026-05-22 15h silence:
    record_loss had no signal at the moment the cap tripped — only the
    next-morning daily_live_audit surfaced the silence."""
    cap = paper_manager.config.MAX_DAILY_LOSS
    # Below cap → flag stays False
    paper_manager.record_loss(amount=cap - 10.0)
    assert paper_manager._cap_alert_paper_fired is False
    # Cross cap on this loss → flag flips True
    paper_manager.record_loss(amount=20.0)
    assert paper_manager._daily_loss_paper >= cap
    assert paper_manager._cap_alert_paper_fired is True
    # Subsequent loss past cap must NOT re-fire (dedup within day)
    paper_manager.record_loss(amount=50.0)
    assert paper_manager._cap_alert_paper_fired is True  # still True, no re-arm
    # reset_daily re-arms for the next UTC day
    paper_manager.reset_daily()
    assert paper_manager._cap_alert_paper_fired is False
    assert paper_manager._daily_loss_paper == 0.0


@pytest.mark.asyncio
async def test_cap_trip_alert_skipped_when_old_entry(paper_manager):
    """entry_today=False settle losses don't accumulate _daily_loss_* and
    therefore must not trip the cap alert."""
    cap = paper_manager.config.MAX_DAILY_LOSS
    paper_manager.record_loss(amount=cap + 100.0, entry_today=False)
    assert paper_manager._daily_loss_paper == 0.0
    assert paper_manager._cap_alert_paper_fired is False


@pytest.mark.asyncio
async def test_record_loss_old_entry_skips_daily_cap(paper_manager):
    """Settlement of an old (entry_today=False) trade must still tick the
    consecutive-loss counter (15-min cluster brake) but must NOT charge
    today's daily-loss cap. Today's entries (default entry_today=True) still
    accumulate normally. Regression for 2026-05-22 15h silence — old WNBA/MLB
    settle cluster tripped paper+live $200 caps by 02:30 UTC even though
    today's net PnL was +$60."""
    paper_manager.record_loss(amount=150.0, entry_today=False)
    assert paper_manager._daily_loss_paper == 0.0
    assert paper_manager._consecutive_losses == 1
    paper_manager.record_loss(amount=50.0, entry_today=True)
    assert paper_manager._daily_loss_paper == 50.0
    assert paper_manager._consecutive_losses == 2
    # Default behavior unchanged when entry_today omitted (today's entry)
    paper_manager.record_loss(amount=25.0)
    assert paper_manager._daily_loss_paper == 75.0
