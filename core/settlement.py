import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger("settlement")


class SettlementChecker:
    """Check settled markets and calculate PnL for paper trades."""

    def __init__(self, kalshi_client, database, order_manager):
        self._client = kalshi_client
        self._db = database
        self._order_manager = order_manager
        self._last_summary_time = 0.0
        # Cache settled market results to avoid repeated API calls
        self._settled_cache: dict[str, str] = {}  # ticker -> "yes" | "no"

    def calculate_pnl(self, direction: str, price: float, size_usdc: float,
                      fee: float, result: str) -> float:
        """Calculate PnL for a paper trade given the market result.

        Args:
            direction: "yes" or "no" — what the trade bought
            price: entry price (0.00-1.00)
            size_usdc: total USD invested
            fee: fee already paid
            result: "yes" or "no" — market settlement result
        Returns:
            PnL in USD (positive = profit, negative = loss)
        """
        if direction == result:
            # Won: each contract pays $1.00, we paid `price` per contract
            # contracts = size_usdc / price
            # payout = contracts * 1.00 = size_usdc / price
            # pnl = payout - size_usdc - fee
            pnl = size_usdc * ((1.0 - price) / price) - fee
        else:
            # Lost: contracts worth $0, lose entire investment
            pnl = -size_usdc
        return round(pnl, 2)

    async def check_and_settle(self):
        """Check unsettled paper trades and settle any finalized markets."""
        unsettled = await self._db.get_unsettled_trades()
        if not unsettled:
            return

        # Group by market_id to minimize API calls
        by_market: dict[str, list[dict]] = defaultdict(list)
        for trade in unsettled:
            by_market[trade["market_id"]].append(trade)

        settled_count = 0
        total_pnl = 0.0

        loop = asyncio.get_event_loop()
        for ticker, trades in by_market.items():
            # Check cache first
            result = self._settled_cache.get(ticker)
            if not result:
                # Query API (run in executor to avoid blocking)
                market = await loop.run_in_executor(None, self._client.get_market, ticker)
                if not market:
                    continue
                status = market.get("status", "")
                if status not in ("finalized", "settled"):
                    continue
                result = market.get("result")
                if not result:
                    continue
                self._settled_cache[ticker] = result

            # Settle all trades for this market
            for trade in trades:
                pnl = self.calculate_pnl(
                    direction=trade["direction"],
                    price=trade["price"],
                    size_usdc=trade["size"],
                    fee=trade["fee"],
                    result=result,
                )
                await self._db.settle_trade(trade["id"], pnl)
                self._order_manager.bankroll += pnl
                if pnl > 0:
                    self._order_manager.record_win(pnl)
                elif pnl < 0:
                    self._order_manager.record_loss(abs(pnl))
                total_pnl += pnl
                settled_count += 1
                logger.info(
                    f"[SETTLE] {ticker}: {trade['strategy']} {trade['direction']} "
                    f"@{trade['price']:.2f} → {result} | PnL: ${pnl:+.2f}"
                )

        now = time.time()
        if settled_count > 0 or now - self._last_summary_time >= 300:
            unsettled_remaining = len(unsettled) - settled_count
            logger.info(
                f"[SETTLE] Settled {settled_count} trades (PnL: ${total_pnl:+.2f}), "
                f"{unsettled_remaining} still pending | Bankroll: ${self._order_manager.bankroll:.2f}"
            )
            self._last_summary_time = now
