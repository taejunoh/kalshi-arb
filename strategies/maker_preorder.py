import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("maker_preorder")

MIN_CLOSE_MINUTES = 2
MAX_CLOSE_MINUTES = 30


class MakerPreorderStrategy:
    """Maker Pre-Order: place limit orders on both sides of short-duration markets with sum < $1.00."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._active_orders: dict[str, dict] = {}

    def calculate_order_prices(self) -> tuple[float, float]:
        half = self.config.MAKER_TARGET_SUM / 2
        return round(half, 4), round(half, 4)

    def expected_profit_per_dollar(self) -> float:
        return 1.0 - self.config.MAKER_TARGET_SUM

    def _is_short_duration_market(self, market: dict) -> bool:
        close_time_str = market.get("close_time")
        if not close_time_str:
            return False
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_until_close = (close_time - now).total_seconds() / 60
            return MIN_CLOSE_MINUTES <= minutes_until_close <= MAX_CLOSE_MINUTES
        except (ValueError, TypeError):
            return False

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active():
            return
        await self._monitor_active_orders()
        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                if self._is_short_duration_market(market):
                    await self._place_orders(market)
            except Exception as e:
                logger.error(f"Maker pre-order error on {market.get('ticker')}: {e}")

    async def _place_orders(self, market: dict):
        ticker = market["ticker"]
        if ticker in self._active_orders:
            return
        if self.expected_profit_per_dollar() <= 0:
            return
        yes_price, no_price = self.calculate_order_prices()
        size_usdc = self._order_manager.calculate_position_size(self.expected_profit_per_dollar())
        if size_usdc < 1.0:
            return
        half_size = size_usdc / 2
        self._scanner.lock_market(ticker)
        logger.info(
            f"[MAKER] Placing orders on {ticker}: "
            f"YES@{yes_price:.3f} NO@{no_price:.3f} size=${size_usdc:.2f}"
        )
        yes_result = await self._order_manager.limit_order(
            ticker=ticker, side="yes", price=yes_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=ticker,
        )
        no_result = await self._order_manager.limit_order(
            ticker=ticker, side="no", price=no_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=ticker,
        )
        self._active_orders[ticker] = {
            "yes_order_id": yes_result.get("order_id"),
            "no_order_id": no_result.get("order_id"),
            "yes_status": yes_result.get("status"),
            "no_status": no_result.get("status"),
            "placed_at": time.time(),
            "size_usdc": half_size,
        }

    async def _monitor_active_orders(self):
        to_remove = []
        for ticker, info in self._active_orders.items():
            if info["yes_status"].startswith("paper") and info["no_status"].startswith("paper"):
                logger.info(f"[MAKER] Both sides filled (paper) on {ticker}")
                to_remove.append(ticker)
                continue
            elapsed = time.time() - info["placed_at"]
            if elapsed > 240:
                logger.warning(f"[MAKER] Auto-cancelling unfilled orders on {ticker}")
                await self._order_manager.cancel_order(info["yes_order_id"])
                await self._order_manager.cancel_order(info["no_order_id"])
                self._scanner.unlock_market(ticker)
                to_remove.append(ticker)
        for t in to_remove:
            del self._active_orders[t]
