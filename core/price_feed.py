import asyncio
import json
import time
import logging
from typing import Optional

import websockets

logger = logging.getLogger("price_feed")


class PriceFeed:
    STALE_THRESHOLD = 5.0

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._last_update: dict[str, float] = {}
        self._candle_opens: dict[str, float] = {}
        self._candle_window_start: dict[str, int] = {}

    def get_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def get_candle_open(self, symbol: str) -> Optional[float]:
        return self._candle_opens.get(symbol)

    def is_stale(self, symbol: str) -> bool:
        last = self._last_update.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > self.STALE_THRESHOLD

    def _process_message(self, symbol: str, raw_msg: str):
        data = json.loads(raw_msg)
        price = float(data["p"])
        self._prices[symbol] = price
        self._last_update[symbol] = time.time()

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
