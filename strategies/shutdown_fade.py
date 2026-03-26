import logging
import time
from typing import Optional

logger = logging.getLogger("shutdown_fade")

SHUTDOWN_KEYWORDS = ["shutdown", "government shutdown", "appropriations", "funding deadline"]
SCALE_IN_THRESHOLDS = [0.25, 0.35, 0.50]


class ShutdownFadeStrategy:
    """Buy NO on government shutdown markets when YES is elevated.
    Historical base rate: Congress almost always deals last-minute."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._active_positions: dict[str, dict] = {}  # ticker -> {levels_filled, total_size}
        self._last_summary_time = 0.0

    def _find_shutdown_markets(self) -> list[dict]:
        results = []
        for kw in SHUTDOWN_KEYWORDS:
            results.extend(self._scanner.search_markets(kw))
        # Deduplicate by ticker
        seen = set()
        unique = []
        for m in results:
            t = m["ticker"]
            if t not in seen:
                seen.add(t)
                unique.append(m)
        return unique

    def _get_scale_levels(self) -> list[float]:
        n = self.config.SHUTDOWN_FADE_SCALE_LEVELS
        threshold = self.config.SHUTDOWN_FADE_THRESHOLD
        if n <= 1:
            return [threshold]
        # Generate evenly spaced levels from threshold up to 2x threshold (capped at 0.80)
        step = min(threshold, 0.15)
        return [min(threshold + i * step, 0.80) for i in range(n)]

    def evaluate(self, yes_price: float, ticker: str) -> Optional[dict]:
        """Check if YES price has breached a new scale-in level."""
        levels = self._get_scale_levels()
        pos = self._active_positions.get(ticker, {"levels_filled": 0, "total_size": 0.0})
        next_level = pos["levels_filled"]
        if next_level >= len(levels):
            return None  # All levels filled
        if yes_price < levels[next_level]:
            return None  # Not at threshold yet
        # Check max position
        max_pos = self.config.SHUTDOWN_FADE_MAX_POSITION
        if pos["total_size"] >= max_pos:
            return None
        remaining = max_pos - pos["total_size"]
        per_level = max_pos / len(levels)
        size = min(per_level, remaining)
        return {
            "side": "no",
            "yes_price": yes_price,
            "no_price": 1.0 - yes_price,
            "size_usdc": size,
            "level": next_level,
        }

    async def scan_and_execute(self):
        if not self.config.SHUTDOWN_FADE_ENABLED:
            return
        if self._order_manager.is_circuit_breaker_active():
            return
        markets = self._find_shutdown_markets()
        for market in markets:
            try:
                await self._check_market(market)
            except Exception as e:
                logger.error(f"Shutdown fade error on {market.get('ticker')}: {e}")
        now = time.time()
        if now - self._last_summary_time >= 60 and markets:
            logger.info(
                f"[SHUTDOWN FADE] Found {len(markets)} shutdown markets, "
                f"{len(self._active_positions)} active positions"
            )
            self._last_summary_time = now

    async def _check_market(self, market: dict):
        ticker = market["ticker"]
        yes_price, no_price = self._client.get_market_prices(ticker)
        if yes_price is None:
            return
        signal = self.evaluate(yes_price, ticker)
        if signal is None:
            return
        logger.info(
            f"[SHUTDOWN FADE] {ticker}: YES={yes_price:.2f} — "
            f"buying NO @ level {signal['level']} size=${signal['size_usdc']:.2f}"
        )
        result = await self._order_manager.market_order(
            ticker=ticker, side="no", size_usdc=signal["size_usdc"],
            price=signal["no_price"], strategy="shutdown_fade", market_id=ticker,
        )
        if result.get("status") != "failed":
            pos = self._active_positions.get(ticker, {"levels_filled": 0, "total_size": 0.0})
            pos["levels_filled"] += 1
            pos["total_size"] += signal["size_usdc"]
            self._active_positions[ticker] = pos
            self._order_manager.update_position(
                "shutdown_fade", ticker, "no",
                max(1, int(signal["size_usdc"] / signal["no_price"])),
                signal["no_price"], signal["size_usdc"],
            )
