import asyncio
import time
from unittest.mock import MagicMock

import pytest

from core.kalshi_orderbook_cache import OrderbookCache, OrderbookEntry


def test_get_returns_none_when_unknown_ticker():
    cache = OrderbookCache(client=MagicMock())
    assert cache.get("KXBTC-X-Y") is None


def test_get_returns_entry_when_fresh():
    cache = OrderbookCache(client=MagicMock())
    entry = OrderbookEntry(
        yes_ask=0.30, yes_bid=0.28, no_ask=0.72, no_bid=0.70,
        yes_depth=100.0, no_depth=100.0, ts=time.time(),
    )
    cache._cache["KXBTC-X-Y"] = entry
    out = cache.get("KXBTC-X-Y")
    assert out is entry


def test_get_returns_none_when_stale():
    cache = OrderbookCache(client=MagicMock(), stale_threshold_secs=10.0)
    entry = OrderbookEntry(
        yes_ask=0.30, yes_bid=0.28, no_ask=0.72, no_bid=0.70,
        yes_depth=100.0, no_depth=100.0, ts=time.time() - 30.0,
    )
    cache._cache["KXBTC-X-Y"] = entry
    assert cache.get("KXBTC-X-Y") is None


@pytest.mark.asyncio
async def test_refresh_loop_populates_cache():
    """One iteration of refresh_loop fetches each ticker and populates cache."""
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.30, 0.72))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    cache = OrderbookCache(client=client, refresh_interval_secs=0.05)
    tickers = ["KXBTC-A", "KXBTC-B"]
    cache.set_target_tickers(tickers)

    task = asyncio.create_task(cache.refresh_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    a = cache.get("KXBTC-A")
    b = cache.get("KXBTC-B")
    assert a is not None
    assert a.yes_ask == 0.30
    assert b is not None
