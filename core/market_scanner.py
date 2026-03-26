import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("market_scanner")


class MarketScanner:
    def __init__(self, kalshi_client):
        self._client = kalshi_client
        self._markets: dict[str, dict] = {}
        self._all_markets: dict[str, dict] = {}
        self._locked_markets: set[str] = set()
        self._poll_interval = 5.0
        self._all_markets_poll_interval = 30.0
        self._last_summary_time = 0.0
        self._last_all_summary_time = 0.0

    def update_markets(self, markets: list[dict]):
        self._markets = {m["ticker"]: m for m in markets}

    def get_active_markets(self) -> list[dict]:
        return list(self._markets.values())

    def get_unlocked_markets(self) -> list[dict]:
        return [m for ticker, m in self._markets.items() if ticker not in self._locked_markets]

    def get_market(self, ticker: str) -> Optional[dict]:
        return self._markets.get(ticker)

    def lock_market(self, ticker: str):
        self._locked_markets.add(ticker)
        logger.debug(f"Market locked: {ticker}")

    def unlock_market(self, ticker: str):
        self._locked_markets.discard(ticker)
        logger.debug(f"Market unlocked: {ticker}")

    def is_locked(self, ticker: str) -> bool:
        return ticker in self._locked_markets

    # ── Broad market pool (all Kalshi markets) ─────────────────────

    def get_all_markets(self) -> list[dict]:
        return list(self._all_markets.values())

    def search_markets(self, keyword: str) -> list[dict]:
        kw = keyword.lower()
        return [m for m in self._all_markets.values()
                if kw in m.get("title", "").lower()]

    def filter_markets(self, pattern: str) -> list[dict]:
        regex = re.compile(pattern, re.IGNORECASE)
        return [m for m in self._all_markets.values()
                if regex.search(m.get("title", ""))]

    async def all_markets_poll_loop(self):
        while True:
            try:
                markets = self._client.get_all_active_markets()
                self._all_markets = {m["ticker"]: m for m in markets}
                now = time.time()
                if now - self._last_all_summary_time >= 60:
                    logger.info(f"Broad scan: {len(markets)} total active markets")
                    self._last_all_summary_time = now
            except Exception as e:
                logger.error(f"Broad market scan failed: {e}")
            await asyncio.sleep(self._all_markets_poll_interval)

    async def poll_loop(self):
        while True:
            try:
                markets = self._client.get_active_markets()
                self.update_markets(markets)
                now = time.time()
                if now - self._last_summary_time >= 60:
                    locked = len(self._locked_markets)
                    logger.info(
                        f"Scanned {len(markets)} active crypto markets "
                        f"({locked} locked, {len(markets) - locked} available)"
                    )
                    self._last_summary_time = now
            except Exception as e:
                logger.error(f"Market scan failed: {e}")
            await asyncio.sleep(self._poll_interval)
