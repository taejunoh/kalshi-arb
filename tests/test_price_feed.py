import asyncio
import json
import time
import pytest
from core.price_feed import PriceFeed


@pytest.mark.asyncio
async def test_process_binance_message():
    feed = PriceFeed()
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    assert feed.get_price("BTC") == 97400.50


@pytest.mark.asyncio
async def test_price_feed_staleness():
    feed = PriceFeed()
    assert feed.is_stale("BTC") is True
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    assert feed.is_stale("BTC") is False


@pytest.mark.asyncio
async def test_candle_open_tracking():
    feed = PriceFeed()
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    assert feed.get_candle_open("BTC") == 97400.50


@pytest.mark.asyncio
async def test_get_price_returns_none_for_unknown():
    feed = PriceFeed()
    assert feed.get_price("XRP") is None
