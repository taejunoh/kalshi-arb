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
async def test_book_ticker_parses_mid():
    feed = PriceFeed()
    msg = json.dumps({"u": 1, "s": "BTCUSDT", "b": "76700.00", "B": "0.5", "a": "76710.00", "A": "0.3"})
    feed._process_book_ticker("BTC", msg)
    assert feed.get_mid("BTC") == 76705.0
    assert feed.is_mid_stale("BTC") is False


@pytest.mark.asyncio
async def test_book_ticker_rejects_inverted_or_zero():
    feed = PriceFeed()
    # ask < bid (crossed book) → reject
    bad = json.dumps({"u": 1, "s": "BTCUSDT", "b": "76710", "a": "76700"})
    feed._process_book_ticker("BTC", bad)
    assert feed.get_mid("BTC") is None
    # zero ask → reject
    bad2 = json.dumps({"u": 2, "s": "BTCUSDT", "b": "76700", "a": "0"})
    feed._process_book_ticker("BTC", bad2)
    assert feed.get_mid("BTC") is None


@pytest.mark.asyncio
async def test_is_mid_stale_when_never_seen():
    feed = PriceFeed()
    assert feed.is_mid_stale("BTC") is True


@pytest.mark.asyncio
async def test_kline_threshold_is_lenient_for_60s_candle_cadence():
    """binance.us @kline_1m emits at candle-close (~60s). is_stale must
    tolerate that cadence; the pre-5/19 5s threshold made every observer
    iter skip → 0 contexts → silent observer (see [[price-feed-staleness-
    watchdog-2026-05-19]])."""
    feed = PriceFeed()
    feed._prices["BTC"] = 76800.0
    feed._last_update["BTC"] = time.time() - 45.0  # 45s old kline tick
    assert feed.is_stale("BTC") is False
    feed._last_update["BTC"] = time.time() - 120.0  # 2 min old → truly dead
    assert feed.is_stale("BTC") is True


@pytest.mark.asyncio
async def test_mid_threshold_stays_tight_for_subsecond_bookticker():
    """bookTicker is sub-second on binance.us (live probe 52 msgs / 20s).
    Mid path keeps the 5s threshold so degraded mid feeds are detected
    early and `get_fresh_spot` can fall through to kline."""
    feed = PriceFeed()
    feed._mid_prices["BTC"] = 76805.0
    feed._mid_last_update["BTC"] = time.time() - 4.0
    assert feed.is_mid_stale("BTC") is False
    feed._mid_last_update["BTC"] = time.time() - 10.0
    assert feed.is_mid_stale("BTC") is True


@pytest.mark.asyncio
async def test_get_fresh_spot_prefers_mid_when_fresh():
    feed = PriceFeed()
    feed._prices["BTC"] = 76700.0
    feed._last_update["BTC"] = time.time()
    feed._mid_prices["BTC"] = 76705.0
    feed._mid_last_update["BTC"] = time.time()
    assert feed.get_fresh_spot("BTC") == 76705.0
    assert feed.is_spot_stale("BTC") is False


@pytest.mark.asyncio
async def test_get_fresh_spot_falls_back_to_kline_when_mid_stale():
    feed = PriceFeed()
    feed._prices["BTC"] = 76700.0
    feed._last_update["BTC"] = time.time() - 30.0  # kline fresh enough
    feed._mid_prices["BTC"] = 76705.0
    feed._mid_last_update["BTC"] = time.time() - 30.0  # mid stale (>5s)
    assert feed.get_fresh_spot("BTC") == 76700.0
    assert feed.is_spot_stale("BTC") is False


@pytest.mark.asyncio
async def test_get_fresh_spot_returns_none_when_both_stale():
    feed = PriceFeed()
    feed._prices["BTC"] = 76700.0
    feed._last_update["BTC"] = time.time() - 200.0
    feed._mid_prices["BTC"] = 76705.0
    feed._mid_last_update["BTC"] = time.time() - 200.0
    assert feed.get_fresh_spot("BTC") is None
    assert feed.is_spot_stale("BTC") is True


@pytest.mark.asyncio
async def test_get_fresh_spot_returns_none_when_never_subscribed():
    feed = PriceFeed()
    assert feed.get_fresh_spot("BTC") is None
    assert feed.is_spot_stale("BTC") is True


@pytest.mark.asyncio
async def test_price_history_dedups_duplicate_prices():
    feed = PriceFeed()
    base = json.dumps({"s": "BTCUSDT", "p": "76000.0", "T": 0})
    same = json.dumps({"s": "BTCUSDT", "p": "76000.0", "T": 0})
    diff = json.dumps({"s": "BTCUSDT", "p": "76050.0", "T": 0})
    feed._process_message("BTC", base)
    feed._process_message("BTC", same)
    feed._process_message("BTC", same)
    feed._process_message("BTC", diff)
    # _prices reflects latest, _last_update bumped every msg, but history
    # only retains distinct-price ticks.
    assert feed.get_price("BTC") == 76050.0
    assert len(feed._price_history["BTC"]) == 2


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
async def test_watchdog_never_tick_warns_after_grace_period(caplog):
    import logging
    feed = PriceFeed()
    feed.STALENESS_WARN_THRESHOLD = 0.05
    feed.STALENESS_CHECK_INTERVAL = 0.02
    feed._subscribed.add("ETH")
    feed._subscribe_time["ETH"] = time.time()

    caplog.set_level(logging.WARNING, logger="price_feed")
    task = asyncio.create_task(feed.watchdog())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    warnings = [r for r in caplog.records if "stale" in r.getMessage() and "never" in r.getMessage()]
    assert len(warnings) >= 1


@pytest.mark.asyncio
async def test_watchdog_grace_period_suppresses_startup_warn(caplog):
    """Healthy stream that delivers its first tick within grace period should NOT warn."""
    import logging
    feed = PriceFeed()
    feed.STALENESS_WARN_THRESHOLD = 0.1
    feed.STALENESS_CHECK_INTERVAL = 0.02
    feed._subscribed.add("BTC")
    feed._subscribe_time["BTC"] = time.time()

    caplog.set_level(logging.WARNING, logger="price_feed")
    task = asyncio.create_task(feed.watchdog())
    # Stream delivers first message at 0.04s (well within 0.1s grace).
    await asyncio.sleep(0.04)
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    warnings = [r for r in caplog.records if "stale" in r.getMessage()]
    assert len(warnings) == 0, f"expected no warnings, got: {[r.getMessage() for r in warnings]}"
