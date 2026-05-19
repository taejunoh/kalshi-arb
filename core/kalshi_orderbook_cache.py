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

    @staticmethod
    def _fetch_ticker_sync(client, ticker):
        # 2026-05-19 night: this block used to run inline in refresh_loop
        # below, two sync httpx.Client calls per ticker × ~10-20 tickers
        # per OrderbookCache × 2 cache instances (crypto_fair_value +
        # crypto_fair_value_mid_v1), all on the main event loop every
        # refresh_interval=5s. That continuous blocking was the dominant
        # remaining contributor to the ~17 ConnectTimeout/min residual
        # after the main-loop sync-in-async sweep (see
        # [[sync-in-async-sweep-2026-05-19]]). Moved to a worker thread
        # so the loop stays responsive between fetches.
        try:
            prices = client.get_market_prices(ticker)
            if prices is None or prices[0] is None:
                return None
            yes_price, no_price = prices
            depths = client.get_order_book_depth(ticker)
            yes_depth, no_depth = depths if depths else (0.0, 0.0)
            return OrderbookEntry(
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
            return None

    async def refresh_loop(self):
        """Background task — poll each target ticker every refresh_interval."""
        while True:
            try:
                tickers = list(self._target_tickers)
                if tickers:
                    # Parallel fan-out: each ticker fetched in a worker
                    # thread, gather results. With 32-worker executor +
                    # ~10-20 tickers, all complete in ~1× single-fetch
                    # latency (was previously ~N× under serial sync).
                    entries = await asyncio.gather(
                        *(asyncio.to_thread(self._fetch_ticker_sync, self._client, t)
                          for t in tickers),
                        return_exceptions=True,
                    )
                    for ticker, entry in zip(tickers, entries):
                        if isinstance(entry, OrderbookEntry):
                            self._cache[ticker] = entry
            except Exception as e:
                logger.exception(f"refresh_loop iteration error: {e}")
            await asyncio.sleep(self._refresh_interval)
