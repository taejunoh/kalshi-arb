import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("market_scanner")


# Series tickers targeted by non-crypto strategies
STRATEGY_SERIES = [
    # Shutdown fade (legacy V1 — kept until KXNUMSHUTDOWNS settles 2027)
    "KXFEDMENTION", "KXNUMSHUTDOWNS", "KXGOVSHUT", "KXGOVSHUTLENG",
    # Mentions (Fed press conferences, presidential speeches, etc.)
    "KXPOTUS", "KXPOTUSSPEECH", "KXSOTUWORD",
    # Popular event markets (spread scanner)
    "KXINX", "KXCPI", "KXGDP", "KXFED", "KXUNEMPLOY",
    "KXBTCRANGE", "KXETHRANGE", "KXNASDAQ", "KXSP500",
    # Ladder Consistency Arb (A1) — non-crypto sub-strategy universes
    # ladder_fed
    "KXFEDDECISION",
    # ladder_approval
    "KXTRUMPAPPROVALYEAR", "KXTRUMPAPPROVALBELOW",
    "KX538APPROVE", "KX538APPROVEMAX", "KX538APPROVEMIN", "KX538APPROVEY",
    # ladder_shutdown — KXGOVTSHUTLENGTH is the active duration ladder
    # (KXGOVSHUTLENG above is empty in current Kalshi listing)
    "KXGOVTSHUTLENGTH",
    # xvenue_polymarket — cross-venue vs Polymarket. Without these, the
    # NBA/NHL/World-Cup entries in data/polymarket_kalshi_mapping.json
    # resolve to an empty Kalshi market list and can never fire. (Only
    # xvenue consumes the broad _all_markets pool by prefix; ladder/macro
    # filter to their own TARGET_SERIES, so adding these is inert for them.)
    "KXNBA", "KXNHL", "KXMENWORLDCUP",
]


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

    def _fetch_strategy_markets(self) -> list[dict]:
        """Fetch markets for non-crypto strategies using targeted series queries."""
        all_markets = []
        for prefix in STRATEGY_SERIES:
            try:
                data = self._client._get(
                    f"/trade-api/v2/markets?status=open&series_ticker={prefix}&limit=200"
                )
                if data:
                    all_markets.extend(data.get("markets", []))
            except Exception:
                pass  # Skip unavailable series silently
        return all_markets

    async def all_markets_poll_loop(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                markets = await loop.run_in_executor(None, self._fetch_strategy_markets)
                self._all_markets = {m["ticker"]: m for m in markets}
                now = time.time()
                if now - self._last_all_summary_time >= 60:
                    logger.info(f"Targeted scan: {len(markets)} strategy markets from {len(STRATEGY_SERIES)} series")
                    self._last_all_summary_time = now
            except Exception as e:
                logger.error(f"Strategy market scan failed: {e}")
            await asyncio.sleep(self._all_markets_poll_interval)

    async def poll_loop(self):
        loop = asyncio.get_event_loop()
        while True:
            try:
                markets = await loop.run_in_executor(None, self._client.get_active_markets)
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
