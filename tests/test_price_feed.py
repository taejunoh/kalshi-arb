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


@pytest.mark.asyncio
async def test_process_binance_kline_message():
    feed = PriceFeed()
    msg = json.dumps({
        "e": "kline",
        "E": int(time.time() * 1000),
        "s": "BTCUSDT",
        "k": {"t": 0, "T": 0, "s": "BTCUSDT", "i": "1m", "c": "76950.25", "o": "76900", "h": "77000", "l": "76800", "v": "1.0"},
    })
    feed._process_message("BTC", msg)
    assert feed.get_price("BTC") == 76950.25


@pytest.mark.asyncio
async def test_process_message_ignores_unknown_format():
    feed = PriceFeed()
    msg = json.dumps({"e": "ping", "E": 0})
    feed._process_message("BTC", msg)
    assert feed.get_price("BTC") is None


@pytest.mark.asyncio
async def test_watchdog_warns_when_stream_silently_dead(caplog):
    import logging
    feed = PriceFeed()
    feed.STALENESS_WARN_THRESHOLD = 0.05
    feed.STALENESS_CHECK_INTERVAL = 0.02
    feed._subscribed.add("BTC")
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)

    caplog.set_level(logging.WARNING, logger="price_feed")
    task = asyncio.create_task(feed.watchdog())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    warnings = [r for r in caplog.records if "stale" in r.getMessage()]
    assert len(warnings) >= 1
    assert "BTC" in warnings[0].getMessage()
    assert "BTC" in feed._warned_stale


@pytest.mark.asyncio
async def test_watchdog_clears_warning_on_recovery(caplog):
    import logging
    feed = PriceFeed()
    feed.STALENESS_WARN_THRESHOLD = 0.05
    feed.STALENESS_CHECK_INTERVAL = 0.02
    feed._subscribed.add("BTC")
    feed._warned_stale.add("BTC")

    caplog.set_level(logging.INFO, logger="price_feed")
    task = asyncio.create_task(feed.watchdog())
    await asyncio.sleep(0.03)
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "BTC" not in feed._warned_stale
    recoveries = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(recoveries) >= 1


@pytest.mark.asyncio
async def test_watchdog_never_tick_warns_immediately(caplog):
    import logging
    feed = PriceFeed()
    feed.STALENESS_WARN_THRESHOLD = 0.05
    feed.STALENESS_CHECK_INTERVAL = 0.02
    feed._subscribed.add("ETH")

    caplog.set_level(logging.WARNING, logger="price_feed")
    task = asyncio.create_task(feed.watchdog())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    warnings = [r for r in caplog.records if "stale" in r.getMessage() and "never" in r.getMessage()]
    assert len(warnings) >= 1
