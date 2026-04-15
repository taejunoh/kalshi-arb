import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger("order_manager")


class OrderManager:
    def __init__(self, config, kalshi_client, database, notifier=None):
        self.config = config
        self._client = kalshi_client
        self._db = database
        self._notifier = notifier
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

    async def hydrate_bankroll(self) -> None:
        """Restore bankroll from DB if present (survives restarts). No-op in live
        mode — main.py's real_balance sync overrides after this if applicable."""
        if self._db is None:
            return
        stored = await self._db.get_bot_state("bankroll")
        if stored is not None:
            self.bankroll = stored
            logger.info(f"Hydrated bankroll from DB: ${self.bankroll:.2f}")

    async def _persist_bankroll(self) -> None:
        """Write current bankroll to DB. Called after every mutation."""
        if self._db is None:
            return
        try:
            await self._db.set_bot_state("bankroll", self.bankroll)
        except Exception as e:
            logger.warning(f"Bankroll persist failed: {e}")

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

    async def market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                           minutes_to_start: Optional[float] = None):
        if self.bankroll < size_usdc:
            logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
            return {"status": "skipped", "reason": "insufficient_bankroll"}
        if self._is_live(strategy):
            logger.info(f"[LIVE] {strategy} {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            return await self._live_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                                 minutes_to_start=minutes_to_start)
        return await self._paper_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                              minutes_to_start=minutes_to_start)

    async def limit_order(self, ticker, side, price, size_usdc, strategy, market_id,
                          minutes_to_start: Optional[float] = None):
        if self._is_live(strategy):
            # Check real Kalshi balance before placing live order
            real_balance = self._client.get_balance()
            if real_balance is not None and real_balance < size_usdc:
                logger.debug(f"Insufficient Kalshi balance: ${real_balance:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
                return {"status": "skipped", "reason": "insufficient_balance"}
            logger.info(f"[LIVE] {strategy} LIMIT {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            result = await self._live_limit_order(ticker, side, price, size_usdc, strategy, market_id,
                                                  minutes_to_start=minutes_to_start)
            if self._notifier and result.get("status") != "failed":
                await self._notifier.send(
                    f"📊 <b>LIVE Order</b>\n"
                    f"Strategy: {strategy}\n"
                    f"Market: {ticker}\n"
                    f"{side.upper()} @ {price:.3f} | ${size_usdc:.2f}",
                    msg_type="trade",
                )
            return result
        # Paper path: mirror market_order's bankroll guard. Without this,
        # market_maker's 30-second quoting loop drains bankroll indefinitely
        # into negative territory (observed -$817) because _paper_limit_order
        # only deducts without checking.
        if self.bankroll < size_usdc:
            logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
            return {"status": "skipped", "reason": "insufficient_bankroll"}
        return await self._paper_limit_order(ticker, side, price, size_usdc, strategy, market_id,
                                              minutes_to_start=minutes_to_start)

    async def cancel_order(self, order_id):
        """Cancel an order. If the order was still open (not yet filled),
        restore its size+fee to bankroll. Filled orders don't refund because
        their capital is already committed to a position. Historically the
        missing refund caused requote-heavy strategies (market_maker,
        mentions_trader) to slowly drain bankroll."""
        refunded = await self._refund_if_open(order_id)
        if self.config.is_paper_mode:
            await self._db.update_order_status(order_id, "cancelled")
            return True
        ok = self._client.cancel_order(order_id)
        if not ok and refunded:
            # Kalshi refused the cancel — our refund was speculative. Roll back.
            self.bankroll -= refunded
            await self._persist_bankroll()
        return ok

    async def _refund_if_open(self, order_id: str) -> float:
        """If the order is in 'open'/'paper_open', credit size+fee back and
        return the refund amount. Otherwise return 0. No-op for unknown ids."""
        import aiosqlite
        async with aiosqlite.connect(self._db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT status, price, size FROM orders WHERE order_id = ?", (order_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return 0.0
        if row["status"] not in ("open", "paper_open"):
            return 0.0
        price = float(row["price"])
        size = float(row["size"])
        if price <= 0:
            return 0.0
        count = max(1, int(size / price))
        fee = self.config.estimate_fee(count, int(price * 100))
        refund = size + fee
        self.bankroll += refund
        await self._persist_bankroll()
        return refund

    async def _paper_market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                                   minutes_to_start: Optional[float] = None):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.estimate_fee(count, int(price * 100))
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        trade_id = await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="paper_filled",
            minutes_to_start=minutes_to_start,
        )
        logger.info(f"[PAPER] Market {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_filled", "size_usdc": size_usdc, "price": price, "trade_id": trade_id}

    async def _paper_limit_order(self, ticker, side, price, size_usdc, strategy, market_id,
                                  minutes_to_start: Optional[float] = None):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.estimate_fee(count, int(price * 100))
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="paper_open",
        )
        logger.info(f"[PAPER] Limit {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_open", "price": price, "size_usdc": size_usdc}

    async def _live_market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                                  minutes_to_start: Optional[float] = None):
        count = max(1, int(size_usdc / price))
        resp = self._client.place_market_order(ticker, side, count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        fee = self.config.estimate_fee(count, int(price * 100))
        trade_id = await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="filled",
            minutes_to_start=minutes_to_start,
        )
        self.bankroll -= size_usdc
        await self._persist_bankroll()
        return {"order_id": order_id, "status": "filled", "size_usdc": size_usdc, "price": price, "trade_id": trade_id}

    async def _live_limit_order(self, ticker, side, price, size_usdc, strategy, market_id,
                                 minutes_to_start: Optional[float] = None):
        count = max(1, int(size_usdc / price))
        # Kalshi API: yes_price is ALWAYS the YES contract price in cents.
        # When buying NO at price X, yes_price = 100 - X (since NO cost = 100 - yes_price).
        price_cents = int(price * 100)
        yes_price_cents = price_cents if side == "yes" else (100 - price_cents)
        resp = self._client.place_limit_order(ticker, side, yes_price_cents, count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        fee = self.config.estimate_fee(count, yes_price_cents)
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        try:
            await self._db.record_order(
                order_id=order_id, market_id=market_id, strategy=strategy,
                side=side, price=price, size=size_usdc, order_type="limit", status="open",
            )
        except Exception as e:
            logger.warning(f"DB record_order failed (order already placed on Kalshi): {e}")
        return {"order_id": order_id, "status": "open", "price": price, "size_usdc": size_usdc}
