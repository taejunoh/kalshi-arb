import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_xvenue_polymarket_blocked_when_paper_filled_exists():
    """xvenue_polymarket re-entry on existing paper_filled trade must be skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_trade(
            strategy="xvenue_polymarket",
            market_id="KXPRES-2028-T_DEM",
            direction="no", price=0.45, size=20.0, fee=0.07,
            status="paper_filled",
        )
        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0

        result = await om._paper_market_order(
            ticker="KXPRES-2028-T_DEM", side="no",
            size_usdc=20.0, price=0.45,
            strategy="xvenue_polymarket",
            market_id="KXPRES-2028-T_DEM",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
