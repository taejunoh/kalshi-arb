import asyncio
import json
import math
import time
import logging
from collections import deque
from typing import Optional

import websockets

logger = logging.getLogger("price_feed")


class PriceFeed:
    # kline_1m on binance.us emits at candle-close cadence — BTC measured at
    # ~60s between ticks (live probe 2026-05-19 PM: 0 msgs in 20s window),
    # ETH p50 ~9s. The pre-2026-05-19 5s threshold (sized for @trade 1Hz)
    # made `is_stale` True every observer iter → contexts=0 → silent
    # crypto_observer. 90s covers one full kline cycle + 30s grace, with
    # the 60s watchdog still alerting on truly silent streams.
    KLINE_STALE_THRESHOLD = 90.0
    # bookTicker is sub-second (live probe 2026-05-19 PM: 52 msgs / 20s,
    # gap p50=0.01s, max=3s). Keep mid path tight so `get_fresh_spot` can
    # tell a healthy mid feed from a degraded one.
    MID_STALE_THRESHOLD = 5.0
    STALENESS_WARN_THRESHOLD = 60.0
    STALENESS_CHECK_INTERVAL = 15.0
    VOLATILITY_WINDOW = 120  # keep last 120 ticks for volatility calculation

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._last_update: dict[str, float] = {}
        self._candle_opens: dict[str, float] = {}
        self._candle_window_start: dict[str, int] = {}
        self._price_history: dict[str, deque] = {}  # symbol -> deque of (time, price)
        self._subscribed: set[str] = set()
        self._subscribe_time: dict[str, float] = {}
        self._warned_stale: set[str] = set()
        # bookTicker mid (parallel data source for crypto_fair_value_mid_v1).
        # Decoupled from _prices so the kline-derived last-trade signal is
        # untouched.
        self._mid_prices: dict[str, float] = {}
        self._mid_last_update: dict[str, float] = {}

    def get_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def get_candle_open(self, symbol: str) -> Optional[float]:
        return self._candle_opens.get(symbol)

    def is_stale(self, symbol: str) -> bool:
        last = self._last_update.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > self.KLINE_STALE_THRESHOLD

    def get_mid(self, symbol: str) -> Optional[float]:
        return self._mid_prices.get(symbol)

    def is_mid_stale(self, symbol: str) -> bool:
        last = self._mid_last_update.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > self.MID_STALE_THRESHOLD

    def get_fresh_spot(self, symbol: str) -> Optional[float]:
        """Prefer bookTicker mid (sub-second fresh) over kline last-trade
        (60s candle cadence on binance.us). Falls back to kline if mid is
        stale or never seen; returns None only when both sources are stale.
        Use this in any strategy whose hypothesis depends on accurate
        real-time spot (e.g., strike comparisons, ATM-bucket selection).
        """
        if not self.is_mid_stale(symbol):
            mid = self._mid_prices.get(symbol)
            if mid is not None and mid > 0:
                return mid
        if not self.is_stale(symbol):
            price = self._prices.get(symbol)
            if price is not None and price > 0:
                return price
        return None

    def is_spot_stale(self, symbol: str) -> bool:
        """True only when both mid (5s) and kline (90s) are stale. Mirrors
        get_fresh_spot's fallback chain — they should be used together.
        """
        return self.is_mid_stale(symbol) and self.is_stale(symbol)

    def get_volatility(self, symbol: str) -> Optional[float]:
        """Annualized realized volatility from recent trade ticks.
        Returns None if insufficient data (< 20 ticks)."""
        history = self._price_history.get(symbol)
        if not history or len(history) < 20:
            return None
        prices = [p for _, p in history]
        log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
        if not log_returns:
            return None
        mean = sum(log_returns) / len(log_returns)
        variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
        std = math.sqrt(variance)
        # Annualize: ticks arrive ~1/sec, so ~31.5M ticks/year
        # More practically: use time span of window to scale
        time_span_seconds = history[-1][0] - history[0][0]
        if time_span_seconds <= 0:
            return None
        ticks_per_year = len(log_returns) / time_span_seconds * 365.25 * 24 * 3600
        return std * math.sqrt(ticks_per_year)

    def _process_message(self, symbol: str, raw_msg: str):
        data = json.loads(raw_msg)
        # @trade / @aggTrade emit {"p": "..."}; @kline_* emits {"k": {"c": "..."}}.
        # binance.us silent-killed trade event streams 2026-05-19, so we now run
        # on @kline_1m by default but keep @trade parsing for backwards-compat.
        if "p" in data:
            price = float(data["p"])
        elif "k" in data and "c" in data["k"]:
            price = float(data["k"]["c"])
        else:
            return
        now = time.time()
        prev_price = self._prices.get(symbol)
        self._prices[symbol] = price
        self._last_update[symbol] = now

        # Volatility history: skip duplicate consecutive prices. @kline_1m emits
        # a tick every second even when no trade occurred, so close stays flat
        # for low-volume pairs — appending duplicates inflates the window with
        # zero log-returns and deflates realized vol.
        if prev_price is None or price != prev_price:
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=self.VOLATILITY_WINDOW)
            self._price_history[symbol].append((now, price))

        current_window = self._get_15min_window()
        prev_window = self._candle_window_start.get(symbol)
        if prev_window is None or current_window != prev_window:
            self._candle_opens[symbol] = price
            self._candle_window_start[symbol] = current_window
            logger.debug(f"{symbol} new candle open: {price} (window {current_window})")

    def _get_15min_window(self) -> int:
        now = int(time.time())
        return now - (now % 900)

    async def watchdog(self):
        """Periodically check subscribed symbols for stream staleness.

        binance.us has silently dropped streams in the past (e.g., @aggTrade
        deprecation 2026-05) where the WS stays connected but emits zero
        messages. subscribe() only catches connect/disconnect errors, so this
        loop flags symbols that haven't ticked within STALENESS_WARN_THRESHOLD.
        """
        while True:
            await asyncio.sleep(self.STALENESS_CHECK_INTERVAL)
            now = time.time()
            for symbol in list(self._subscribed):
                last = self._last_update.get(symbol)
                if last is None:
                    sub_time = self._subscribe_time.get(symbol, now)
                    age = now - sub_time
                    if age <= self.STALENESS_WARN_THRESHOLD:
                        continue
                    gap = None
                else:
                    gap = now - last
                stale = gap is None or gap > self.STALENESS_WARN_THRESHOLD
                if stale and symbol not in self._warned_stale:
                    gap_str = f"{gap:.0f}s" if gap is not None else "never"
                    logger.warning(
                        f"{symbol} price feed stale: no tick in {gap_str} "
                        f"(threshold {self.STALENESS_WARN_THRESHOLD:.0f}s) — "
                        f"WS may be silently dead"
                    )
                    self._warned_stale.add(symbol)
                elif not stale and symbol in self._warned_stale:
                    logger.info(f"{symbol} price feed recovered (last tick {gap:.1f}s ago)")
                    self._warned_stale.discard(symbol)

    def _process_book_ticker(self, symbol: str, raw_msg: str):
        # @bookTicker format: {"u":..., "s":"BTCUSDT", "b":"bid", "B":"bidQty",
        # "a":"ask", "A":"askQty"}. We compute mid = (bid + ask) / 2 and store
        # separately from kline-derived _prices.
        data = json.loads(raw_msg)
        bid_raw = data.get("b")
        ask_raw = data.get("a")
        if bid_raw is None or ask_raw is None:
            return
        try:
            bid = float(bid_raw)
            ask = float(ask_raw)
        except (TypeError, ValueError):
            return
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        self._mid_prices[symbol] = (bid + ask) / 2.0
        self._mid_last_update[symbol] = time.time()

    async def subscribe_book_ticker(self, symbol: str, ws_url: str):
        """Subscribe to @bookTicker stream for mid-price tracking.

        Mid is updated on every L1 change (very high frequency). Strategies
        consuming mid should read get_mid() in their scan loop — there is no
        per-tick callback or history buffer (vol still comes from kline).
        """
        backoff = 1
        logged_max_backoff = False
        while True:
            try:
                async with websockets.connect(ws_url, open_timeout=10) as ws:
                    logger.info(f"Connected to {symbol} bookTicker feed")
                    backoff = 1
                    logged_max_backoff = False
                    async for msg in ws:
                        self._process_book_ticker(symbol, msg)
            except asyncio.CancelledError:
                logger.info(f"{symbol} bookTicker shutting down")
                return
            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                if backoff < 30:
                    logger.warning(f"{symbol} bookTicker disconnected: {e}. Reconnecting in {backoff}s...")
                elif not logged_max_backoff:
                    logger.warning(f"{symbol} bookTicker unreachable, retrying silently every {backoff}s")
                    logged_max_backoff = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as e:
                if not logged_max_backoff:
                    logger.error(f"{symbol} bookTicker unexpected error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def subscribe(self, symbol: str, ws_url: str):
        self._subscribed.add(symbol)
        self._subscribe_time.setdefault(symbol, time.time())
        backoff = 1
        logged_max_backoff = False
        while True:
            try:
                async with websockets.connect(ws_url, open_timeout=10) as ws:
                    logger.info(f"Connected to {symbol} price feed")
                    backoff = 1
                    logged_max_backoff = False
                    async for msg in ws:
                        self._process_message(symbol, msg)
            except asyncio.CancelledError:
                logger.info(f"{symbol} feed shutting down")
                return
            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                if backoff < 30:
                    logger.warning(f"{symbol} feed disconnected: {e}. Reconnecting in {backoff}s...")
                elif not logged_max_backoff:
                    logger.warning(f"{symbol} feed unreachable, retrying silently every {backoff}s")
                    logged_max_backoff = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as e:
                if not logged_max_backoff:
                    logger.error(f"{symbol} feed unexpected error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
