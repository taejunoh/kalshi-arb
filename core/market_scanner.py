import asyncio
import logging
from typing import Optional

logger = logging.getLogger("market_scanner")


class MarketScanner:
    def __init__(self, kalshi_client):
        self._client = kalshi_client
        self._markets: dict[str, dict] = {}
        self._locked_markets: set[str] = set()
        self._poll_interval = 5.0

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

    async def poll_loop(self):
        while True:
            try:
                markets = self._client.get_active_markets()
                self.update_markets(markets)
                logger.debug(f"Scanned {len(markets)} active crypto markets")
            except Exception as e:
                logger.error(f"Market scan failed: {e}")
            await asyncio.sleep(self._poll_interval)
