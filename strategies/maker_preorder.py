import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("maker_preorder")

# Match "5 minute", "5-minute", "5min" but NOT "15 minute", "15-minute", "15min", etc.
_FIVE_MIN_RE = re.compile(r'(?<!\d)5[\s-]?min(?:ute)?s?(?!\d)', re.IGNORECASE)


class MakerPreorderStrategy:
    """Maker Pre-Order: place limit orders on both sides of 5-min markets with sum < $1.00."""

    def __init__(self, config, order_manager, market_scanner, polymarket_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = polymarket_client
        self._active_orders: dict[str, dict] = {}  # market_id -> {up_order, down_order, placed_at}

    def calculate_order_prices(self) -> tuple[float, float]:
        """Split MAKER_TARGET_SUM evenly between UP and DOWN."""
        half = self.config.MAKER_TARGET_SUM / 2
        return round(half, 4), round(half, 4)

    def expected_profit_per_dollar(self) -> float:
        """Expected profit per $1 deployed if both sides fill."""
        return 1.0 - self.config.MAKER_TARGET_SUM

    def _is_5min_market(self, market: dict) -> bool:
        q = market.get("question", "")
        return bool(_FIVE_MIN_RE.search(q))

    async def scan_and_execute(self):
        """Scan for 5-min markets, place orders, monitor fills."""
        if self._order_manager.is_circuit_breaker_active():
            return

        # Monitor existing orders first
        await self._monitor_active_orders()

        # Look for new opportunities
        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                if self._is_5min_market(market):
                    await self._place_orders(market)
            except Exception as e:
                logger.error(f"Maker pre-order error on {market.get('conditionId')}: {e}")

    async def _place_orders(self, market: dict):
        condition_id = market["conditionId"]

        # Skip if we already have active orders on this market
        if condition_id in self._active_orders:
            return

        up_token, down_token = self._client.get_token_ids(market)
        if not up_token or not down_token:
            return

        # Check profitability
        if self.expected_profit_per_dollar() <= 0:
            return

        up_price, down_price = self.calculate_order_prices()
        size_usdc = self._order_manager.calculate_position_size(self.expected_profit_per_dollar())
        if size_usdc < 1.0:
            return

        half_size = size_usdc / 2

        # Lock market
        self._scanner.lock_market(condition_id)

        logger.info(
            f"[MAKER] Placing orders on {condition_id}: "
            f"UP@{up_price:.3f} DOWN@{down_price:.3f} size=${size_usdc:.2f}"
        )

        up_result = await self._order_manager.limit_order(
            token_id=up_token, side="BUY", price=up_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=condition_id,
        )

        down_result = await self._order_manager.limit_order(
            token_id=down_token, side="BUY", price=down_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=condition_id,
        )

        self._active_orders[condition_id] = {
            "up_order_id": up_result.get("order_id"),
            "down_order_id": down_result.get("order_id"),
            "up_status": up_result.get("status"),
            "down_status": down_result.get("status"),
            "placed_at": time.time(),
            "size_usdc": half_size,
        }

    async def _monitor_active_orders(self):
        """Check active orders and manage risk for one-sided fills."""
        to_remove = []
        for market_id, info in self._active_orders.items():
            # In paper mode, simulate both sides filling
            if info["up_status"].startswith("paper") and info["down_status"].startswith("paper"):
                logger.info(f"[MAKER] Both sides filled (paper) on {market_id}")
                to_remove.append(market_id)
                continue

            # Auto-cancel if orders have been open too long (5 min = 300s)
            elapsed = time.time() - info["placed_at"]
            if elapsed > 240:  # 4 min — cancel with 60s remaining
                logger.warning(f"[MAKER] Auto-cancelling unfilled orders on {market_id}")
                await self._order_manager.cancel_order(info["up_order_id"])
                await self._order_manager.cancel_order(info["down_order_id"])
                self._scanner.unlock_market(market_id)
                to_remove.append(market_id)

        for mid in to_remove:
            del self._active_orders[mid]
