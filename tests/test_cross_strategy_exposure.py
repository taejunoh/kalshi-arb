import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_total_open_exposure_sums_across_strategies():
    """Multiple strategies on same ticker → exposure sums."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUN-C25",
            direction="yes", price=0.30, size=50.0, fee=0.07,
            status="paper_filled",
        )
        await db.record_trade(
            strategy="macro_event", market_id="KXFEDDECISION-26JUN-C25",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_filled",
        )
        await db.record_trade(
            strategy="ladder_fed", market_id="KXFEDDECISION-26JUN-H0",
            direction="yes", price=0.40, size=30.0, fee=0.07,
            status="paper_filled",
        )
        total = await db.total_open_exposure("KXFEDDECISION-26JUN-C25")
        assert total == 70.0


@pytest.mark.asyncio
async def test_total_open_exposure_excludes_settled_trades():
    """Closed trades (status=settled, paper_settled) must not contribute."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="macro_event", market_id="KXCPI-26MAY-T0.3",
            direction="yes", price=0.30, size=20.0, fee=0.07,
            status="paper_settled",
        )
        await db.record_trade(
            strategy="macro_event", market_id="KXCPI-26MAY-T0.3",
            direction="yes", price=0.30, size=15.0, fee=0.07,
            status="paper_filled",
        )
        total = await db.total_open_exposure("KXCPI-26MAY-T0.3")
        assert total == 15.0


@pytest.mark.asyncio
async def test_total_open_exposure_returns_zero_when_no_trades():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        total = await db.total_open_exposure("KXCPI-26MAY-T0.3")
        assert total == 0.0
