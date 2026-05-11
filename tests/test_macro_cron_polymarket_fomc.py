"""Tests for refresh_fomc_polymarket — populates macro_consensus FOMC
rows from polymarket_quotes when CME FedWatch is IP-blocked."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from scripts.macro_consensus_cron import (
    refresh_fomc_polymarket, POLYMARKET_FOMC_MAPPING,
    POLYMARKET_QUOTE_MAX_STALENESS_SEC,
)


def _fresh(seconds_ago: int = 30) -> str:
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return ts.isoformat()


@pytest.mark.asyncio
async def test_writes_one_row_per_mapped_bucket():
    """For every (event_key, bucket) in the mapping, if polymarket_quotes
    has a fresh quote, a macro_consensus row is written with source='polymarket'."""
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(
        return_value={"yes_price": 0.42, "fetched_at": _fresh()}
    )
    db.record_macro_consensus = AsyncMock()

    n = await refresh_fomc_polymarket(db)

    total_buckets = sum(len(b) for b in POLYMARKET_FOMC_MAPPING.values())
    assert n == total_buckets
    assert db.record_macro_consensus.await_count == total_buckets
    # All writes use source='polymarket'
    for call in db.record_macro_consensus.await_args_list:
        args = call.args
        assert args[-1] == "polymarket"


@pytest.mark.asyncio
async def test_skips_when_no_quote_in_db():
    """Polymarket cron may be down or never populated — fetch returns None."""
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value=None)
    db.record_macro_consensus = AsyncMock()

    n = await refresh_fomc_polymarket(db)

    assert n == 0
    db.record_macro_consensus.assert_not_called()


@pytest.mark.asyncio
async def test_skips_stale_quotes():
    """A quote older than POLYMARKET_QUOTE_MAX_STALENESS_SEC is dropped —
    polymarket cron crashed and we shouldn't write hours-old consensus."""
    stale_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(
        return_value={"yes_price": 0.42, "fetched_at": stale_iso}
    )
    db.record_macro_consensus = AsyncMock()

    n = await refresh_fomc_polymarket(db)

    assert n == 0
    db.record_macro_consensus.assert_not_called()


@pytest.mark.asyncio
async def test_uses_yes_price_as_bucket_probability():
    """Polymarket binary YES price = bucket probability directly. The 5
    FOMC outcomes are mutually exclusive and their yes_prices sum to ~1.0."""
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(
        return_value={"yes_price": 0.9685, "fetched_at": _fresh()}
    )
    db.record_macro_consensus = AsyncMock()

    await refresh_fomc_polymarket(db)

    # All recorded probabilities = 0.9685 (the test mock returns same quote
    # for every slug). What matters is the prob argument is yes_price, not
    # 1-yes_price.
    sample_call = db.record_macro_consensus.await_args_list[0]
    assert sample_call.args[2] == 0.9685


@pytest.mark.asyncio
async def test_missing_yes_price_skips():
    """Defensive: corrupted polymarket_quotes row without yes_price."""
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(
        return_value={"yes_price": None, "fetched_at": _fresh()}
    )
    db.record_macro_consensus = AsyncMock()

    n = await refresh_fomc_polymarket(db)

    assert n == 0
