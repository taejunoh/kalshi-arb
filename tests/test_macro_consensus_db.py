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


@pytest.mark.asyncio
async def test_record_macro_event_signal_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_event_signal(
            ticker="KXCPI-26JUN-T0.3", event_key="CPI-26JUN", bucket="T0.3",
            kalshi_implied=0.25, consensus=0.40, deviation=0.15,
            side="yes", expected_size_usdc=30.0, action_taken="fired",
        )
        rows = await db.fetch_recent_macro_event_signals(limit=5)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "KXCPI-26JUN-T0.3"
        assert rows[0]["deviation"] == 0.15
        assert rows[0]["action_taken"] == "fired"


@pytest.mark.asyncio
async def test_record_macro_event_signal_allows_skipped_with_null_side():
    """Skipped signals (e.g., skip_ev) record action_taken without side."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(db_path=os.path.join(tmp, "t.db"))
        await db.initialize()
        await db.record_macro_event_signal(
            ticker="KXCPI-26JUN-T0.3", event_key="CPI-26JUN", bucket="T0.3",
            kalshi_implied=0.40, consensus=0.42, deviation=0.02,
            side=None, expected_size_usdc=None, action_taken="skip_ev",
        )
        rows = await db.fetch_recent_macro_event_signals(limit=5)
        assert rows[0]["action_taken"] == "skip_ev"
        assert rows[0]["side"] is None
