"""In-memory Kalshi orderbook cache with TTL + async refresh loop.

Used by B2 crypto_fair_value strategy's 1-sec fast-scan to avoid
hitting Kalshi REST on every tick. Caller (strategy) calls
set_target_tickers() with the dynamic ATM ±N list; refresh_loop
polls each one every refresh_interval_secs.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("orderbook_cache")


@dataclass
class OrderbookEntry:
    yes_ask: float
    yes_bid: float
    no_ask: float
    no_bid: float
    yes_depth: float
    no_depth: float
    ts: float  # epoch seconds


class OrderbookCache:
    def __init__(
        self,
        client,
        refresh_interval_secs: float = 5.0,
        stale_threshold_secs: float = 10.0,
    ):
        self._client = client
        self._refresh_interval = refresh_interval_secs
        self._stale_threshold = stale_threshold_secs
        self._cache: dict[str, OrderbookEntry] = {}
        self._target_tickers: list[str] = []

    def set_target_tickers(self, tickers: list[str]) -> None:
        """Caller updates the watch list (e.g., when ATM shifts)."""
        self._target_tickers = list(tickers)

    def get(self, ticker: str) -> Optional[OrderbookEntry]:
        e = self._cache.get(ticker)
        if e is None:
            return None
        if (time.time() - e.ts) > self._stale_threshold:
            return None
        return e

    def _refresh_all_sync(self, tickers: list[str]) -> None:
        # 2026-05-19 night: original inline loop blocked the event loop.
        # First fix (e52e87f) parallelized via asyncio.gather + to_thread,
        # which dropped ConnectTimeout 17→0/min — but burst-fired all
        # ticker requests in <0.2s and produced 19k+ Kalshi 429s in 23min
        # (vs 0 pre-fix). The right shape is serial-in-a-thread: same
        # Kalshi load as pre-fix (0 429s observed) but the event loop
        # stays free to service sports scan_loops between fetches.
        for ticker in tickers:
            try:
                prices = self._client.get_market_prices(ticker)
                if prices is None or prices[0] is None:
                    continue
                yes_price, no_price = prices
                depths = self._client.get_order_book_depth(ticker)
                yes_depth, no_depth = depths if depths else (0.0, 0.0)
                self._cache[ticker] = OrderbookEntry(
                    yes_ask=yes_price,
                    yes_bid=max(0.0, 1.0 - no_price),
                    no_ask=no_price,
                    no_bid=max(0.0, 1.0 - yes_price),
                    yes_depth=yes_depth or 0.0,
                    no_depth=no_depth or 0.0,
                    ts=time.time(),
                )
            except Exception as e:
                logger.warning(f"orderbook fetch error for {ticker}: {e}")

    async def refresh_loop(self):
        """Background task — poll each target ticker every refresh_interval."""
        while True:
            try:
                tickers = list(self._target_tickers)
                if tickers:
                    await asyncio.to_thread(self._refresh_all_sync, tickers)
            except Exception as e:
                logger.exception(f"refresh_loop iteration error: {e}")
            await asyncio.sleep(self._refresh_interval)
