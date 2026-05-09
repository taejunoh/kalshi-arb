import os
import tempfile
import pytest

from core.database import Database


@pytest.mark.asyncio
async def test_record_and_fetch_macro_consensus():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_consensus(
            event_key="FOMC-26JUN", bucket="C25",
            prob=0.65, source="fedwatch",
        )
        row = await db.fetch_macro_consensus("FOMC-26JUN", "C25")
        assert row is not None
        assert row["prob"] == 0.65
        assert row["source"] == "fedwatch"
        assert row["fetched_at"]


@pytest.mark.asyncio
async def test_record_macro_consensus_upserts():
    """Re-recording (event_key, bucket, source) updates prob in place."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_consensus("FOMC-26JUN", "C25", 0.65, "fedwatch")
        await db.record_macro_consensus("FOMC-26JUN", "C25", 0.70, "fedwatch")
        row = await db.fetch_macro_consensus("FOMC-26JUN", "C25")
        assert row["prob"] == 0.70
