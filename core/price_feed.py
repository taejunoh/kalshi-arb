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
    STALE_THRESHOLD = 5.0
    VOLATILITY_WINDOW = 120  # keep last 120 ticks for volatility calculation

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._last_update: dict[str, float] = {}
        self._candle_opens: dict[str, float] = {}
        self._candle_window_start: dict[str, int] = {}
        self._price_history: dict[str, deque] = {}  # symbol -> deque of (time, price)

    def get_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def get_candle_open(self, symbol: str) -> Optional[float]:
        return self._candle_opens.get(symbol)

    def is_stale(self, symbol: str) -> bool:
        last = self._last_update.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > self.STALE_THRESHOLD

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
        price = float(data["p"])
        now = time.time()
        self._prices[symbol] = price
        self._last_update[symbol] = now

        # Track price history for volatility calculation
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

    async def subscribe(self, symbol: str, ws_url: str):
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
