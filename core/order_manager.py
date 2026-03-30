import logging
import time
import uuid

logger = logging.getLogger("order_manager")


class OrderManager:
    def __init__(self, config, kalshi_client, database):
        self.config = config
        self._client = kalshi_client
        self._db = database
        self.bankroll = config.INITIAL_BANKROLL
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._pause_until = 0.0
        self._positions: dict[tuple[str, str], dict] = {}  # (strategy, ticker) -> position

    def calculate_position_size(self, net_edge: float) -> float:
        kelly_size = self.bankroll * self.config.KELLY_FRACTION * net_edge
        max_size = self.bankroll * self.config.MAX_SINGLE_TRADE_PCT
        return min(kelly_size, max_size)

    def is_circuit_breaker_active(self) -> bool:
        if self._daily_loss >= self.config.MAX_DAILY_LOSS:
            return True
        if self._consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
            if time.time() < self._pause_until:
                return True
            self._consecutive_losses = 0
        return False

    def record_loss(self, amount: float = 0.0):
        self._consecutive_losses += 1
        self._daily_loss += amount
        if self._consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
            self._pause_until = time.time() + 900
            logger.warning(f"Circuit breaker: {self._consecutive_losses} consecutive losses. Pausing.")

    def record_win(self, amount: float = 0.0):
        self._consecutive_losses = 0

    def reset_daily(self):
        self._daily_loss = 0.0

    # ── Position tracking ────────────────────────────────────────────

    def get_position(self, strategy: str, ticker: str) -> dict:
        return self._positions.get((strategy, ticker), {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0})

    def update_position(self, strategy: str, ticker: str, side: str, qty: int, price: float, size_usdc: float):
        key = (strategy, ticker)
        pos = self._positions.get(key, {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0})
        if pos["side"] == side or pos["qty"] == 0:
            total_cost = pos["avg_price"] * pos["qty"] + price * qty
            pos["qty"] += qty
            pos["avg_price"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0.0
            pos["side"] = side
            pos["size_usdc"] += size_usdc
        else:
            # Opposite side reduces position
            pos["qty"] -= qty
            pos["size_usdc"] -= size_usdc
            if pos["qty"] <= 0:
                pos = {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0}
        self._positions[key] = pos

    def get_strategy_exposure(self, strategy: str) -> float:
        return sum(p["size_usdc"] for (s, _), p in self._positions.items() if s == strategy)

    async def requote(self, old_order_id: str, ticker: str, side: str, new_price: float,
                      size_usdc: float, strategy: str):
        """Cancel existing order and place a new one. Returns new order result."""
        cancelled = await self.cancel_order(old_order_id)
        if not cancelled:
            logger.warning(f"Requote: failed to cancel {old_order_id}, skipping replacement")
            return None
        return await self.limit_order(ticker, side, new_price, size_usdc, strategy, ticker)

    # ── Order execution ────────────────────────────────────────────

    def _is_live(self, strategy: str) -> bool:
        """Check if a strategy should trade live (real money)."""
        if not self.config.is_paper_mode:
            return True  # Global live mode
        return strategy in self.config.LIVE_STRATEGIES

    async def market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        if self.bankroll < size_usdc:
            logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
            return {"status": "skipped", "reason": "insufficient_bankroll"}
        if self._is_live(strategy):
            logger.info(f"[LIVE] {strategy} {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            return await self._live_market_order(ticker, side, size_usdc, price, strategy, market_id)
        return await self._paper_market_order(ticker, side, size_usdc, price, strategy, market_id)

    async def limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        if self._is_live(strategy):
            # Check real Kalshi balance before placing live order
            real_balance = self._client.get_balance()
            if real_balance is not None and real_balance < size_usdc:
                logger.debug(f"Insufficient Kalshi balance: ${real_balance:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
                return {"status": "skipped", "reason": "insufficient_balance"}
            logger.info(f"[LIVE] {strategy} LIMIT {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            return await self._live_limit_order(ticker, side, price, size_usdc, strategy, market_id)
        return await self._paper_limit_order(ticker, side, price, size_usdc, strategy, market_id)

    async def cancel_order(self, order_id):
        if self.config.is_paper_mode:
            await self._db.update_order_status(order_id, "cancelled")
            return True
        return self._client.cancel_order(order_id)

    async def _paper_market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.estimate_fee(count, int(price * 100))
        self.bankroll -= size_usdc
        await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="paper_filled",
        )
        logger.info(f"[PAPER] Market {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_filled", "size_usdc": size_usdc, "price": price}

    async def _paper_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="paper_open",
        )
        logger.info(f"[PAPER] Limit {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f}")
        return {"order_id": order_id, "status": "paper_open", "price": price, "size_usdc": size_usdc}

    async def _live_market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        count = max(1, int(size_usdc / price))
        resp = self._client.place_market_order(ticker, side, count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        fee = self.config.estimate_fee(count, int(price * 100))
        await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="filled",
        )
        self.bankroll -= size_usdc
        return {"order_id": order_id, "status": "filled", "size_usdc": size_usdc, "price": price}

    async def _live_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        count = max(1, int(size_usdc / price))
        resp = self._client.place_limit_order(ticker, side, int(price * 100), count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="open",
        )
        return {"order_id": order_id, "status": "open", "price": price, "size_usdc": size_usdc}
