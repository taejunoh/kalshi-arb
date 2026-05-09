import asyncio
import os
import tempfile

import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_ladder_violation_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_ladder_violation(
            series="KXBTC", event_key="26MAY09-1500",
            ladder_type="cumulative",
            leg_a_ticker="KXBTC-26MAY09-1500-B70250",
            leg_b_ticker="KXBTC-26MAY09-1500-B70350",
            inversion_amount=0.07,
            leg_a_depth=120.0, leg_b_depth=80.0,
            action_taken="arb",
            sub_strategy="ladder_crypto",
        )
        rows = await db.fetch_recent_ladder_violations("ladder_crypto", limit=5)
        assert len(rows) == 1
        r = rows[0]
        assert r["series"] == "KXBTC"
        assert r["inversion_amount"] == 0.07
        assert r["action_taken"] == "arb"
