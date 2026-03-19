import asyncio
import logging
from typing import Optional

logger = logging.getLogger("market_scanner")


class MarketScanner:
    def __init__(self, polymarket_client):
        self._client = polymarket_client
        self._markets: dict[str, dict] = {}
        self._locked_markets: set[str] = set()
        self._poll_interval = 5.0

    def update_markets(self, markets: list[dict]):
        self._markets = {m["conditionId"]: m for m in markets}

    def get_active_markets(self) -> list[dict]:
        return list(self._markets.values())

    def get_unlocked_markets(self) -> list[dict]:
        return [m for cid, m in self._markets.items() if cid not in self._locked_markets]

    def get_market(self, condition_id: str) -> Optional[dict]:
        return self._markets.get(condition_id)

    def lock_market(self, condition_id: str):
        self._locked_markets.add(condition_id)
        logger.debug(f"Market locked: {condition_id}")

    def unlock_market(self, condition_id: str):
        self._locked_markets.discard(condition_id)
        logger.debug(f"Market unlocked: {condition_id}")

    def is_locked(self, condition_id: str) -> bool:
        return condition_id in self._locked_markets

    async def poll_loop(self):
        while True:
            try:
                markets = self._client.get_active_markets()
                self.update_markets(markets)
                logger.debug(f"Scanned {len(markets)} active crypto markets")
            except Exception as e:
                logger.error(f"Market scan failed: {e}")
            await asyncio.sleep(self._poll_interval)
