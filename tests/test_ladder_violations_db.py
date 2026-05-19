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


@pytest.mark.asyncio
async def test_ladder_violation_walked_fields_roundtrip(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    await db.record_ladder_violation(
        series="KXBTC", event_key="26MAY1915", ladder_type="bucketed",
        leg_a_ticker="KXBTC-26MAY1915-B66250", leg_b_ticker="KXBTC-26MAY1915-B66350",
        inversion_amount=0.0, leg_a_depth=30.0, leg_b_depth=50.0,
        action_taken="phase0_log", sub_strategy="ladder_crypto",
        walked_edge_usdc=0.18,
        walked_legs_json='[{"ticker":"KXBTC-26MAY1915-B66250","walked_avg":0.05}]',
        top_vs_walked_max_gap_cents=1.5,
        would_fire=True, phase="phase0_paper",
    )
    rows = await db.fetch_recent_ladder_violations("ladder_crypto", limit=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["walked_edge_usdc"] == pytest.approx(0.18)
    assert r["top_vs_walked_max_gap_cents"] == pytest.approx(1.5)
    assert r["would_fire"] == 1
    assert r["phase"] == "phase0_paper"
    assert "KXBTC-26MAY1915-B66250" in r["walked_legs_json"]
