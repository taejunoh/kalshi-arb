import asyncio
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.config import Config
from core.database import Database
from core.order_manager import OrderManager


@pytest.mark.asyncio
async def test_ladder_strategy_blocked_when_paper_filled_exists():
    """Reproduces V1 over-fill: ladder_shutdown attempting re-entry on a
    market that already has paper_filled in DB must be blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config()
        cfg.TRADING_MODE = "paper"  # ensure paper path
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()

        # Seed an existing paper_filled trade for ladder_shutdown
        await db.record_trade(
            strategy="ladder_shutdown",
            market_id="KXNUMSHUTDOWNS-27JAN01-T2",
            direction="yes", price=0.30, size=10.0, fee=0.07,
            status="paper_filled",
        )

        client = MagicMock()
        notifier = MagicMock()
        notifier.send_telegram = MagicMock(return_value=None)
        om = OrderManager(cfg, client, db, notifier)
        om.bankroll = 1000.0  # bypass _persist_bankroll dependency

        result = await om._paper_market_order(
            ticker="KXNUMSHUTDOWNS-27JAN01-T2",
            side="yes", size_usdc=10.0, price=0.30,
            strategy="ladder_shutdown",
            market_id="KXNUMSHUTDOWNS-27JAN01-T2",
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate_trade"
