import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("settlement")

# Cap for _settled_cache to prevent unbounded growth over long-running bots.
# ~30 games/day × 180 days of history ≈ 5400 entries; 2000 keeps ~2 months hot.
MAX_SETTLED_CACHE = 2000


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SettlementChecker:
    """Check settled markets and calculate PnL for paper and live trades.
    Paper fills transition paper_filled → paper_settled and credit the
    paper bankroll on win. Live fills transition filled → settled without
    touching the paper bankroll — Kalshi's real balance already reflects
    the outcome. Both modes feed the shared circuit-breaker counter."""

    def __init__(self, kalshi_client, database, order_manager, notifier=None):
        self._client = kalshi_client
        self._db = database
        self._order_manager = order_manager
        self._notifier = notifier
        self._last_summary_time = 0.0
        # Cache settled market results to avoid repeated API calls.
        # Value: (result, yes_ask, no_ask) — asks needed for CLV closing snapshots
        # across multiple check_and_settle() calls on the same ticker.
        # Insertion-ordered dict → oldest entry is evicted first when cap reached.
        self._settled_cache: dict[str, tuple[str, Optional[float], Optional[float]]] = {}

    def _remember_settled(self, ticker: str, result: str,
                          yes_ask: Optional[float], no_ask: Optional[float]) -> None:
        """Insert with FIFO cap so the cache doesn't grow forever."""
        self._settled_cache[ticker] = (result, yes_ask, no_ask)
        while len(self._settled_cache) > MAX_SETTLED_CACHE:
            oldest_key = next(iter(self._settled_cache))
            del self._settled_cache[oldest_key]

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

    async def simulate_paper_fills(self):
        """Simulate fills for paper limit orders based on current market prices."""
        open_orders = await self._db.get_paper_open_orders()
        if not open_orders:
            return

        loop = asyncio.get_event_loop()
        filled = 0
        for order in open_orders:
            ticker = order["market_id"]
            try:
                market = await loop.run_in_executor(None, self._client.get_market, ticker)
                if not market or market.get("status") != "open":
                    # Market closed/settled — cancel unfilled order
                    await self._db.update_order_status(order["order_id"], "paper_cancelled")
                    continue

                order_side = order["side"]
                order_price = order["price"]

                yes_ask = float(market.get("yes_ask_dollars", "0") or "0")
                no_ask = float(market.get("no_ask_dollars", "0") or "0")

                # Fill if market price is at or better than our limit
                should_fill = False
                if order_side == "yes" and yes_ask > 0 and yes_ask <= order_price:
                    should_fill = True
                elif order_side == "no" and no_ask > 0 and no_ask <= order_price:
                    should_fill = True

                if should_fill:
                    count = max(1, int(order["size"] / order_price))
                    fee = self._order_manager.config.estimate_fee(count, int(order_price * 100))
                    await self._db.update_order_status(order["order_id"], "paper_filled")
                    await self._db.record_trade(
                        strategy=order["strategy"],
                        market_id=ticker,
                        direction=order_side,
                        price=order_price,
                        size=order["size"],
                        fee=fee,
                        status="paper_filled",
                    )
                    filled += 1
            except Exception as e:
                logger.debug(f"Paper fill check error for {ticker}: {e}")

        if filled > 0:
            logger.info(f"[SETTLE] Simulated {filled} paper limit order fills")

    async def check_and_settle(self):
        """Check unsettled trades (paper + live) and settle any finalized
        markets. See class docstring for the paper vs live split."""
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
            cached = self._settled_cache.get(ticker)
            if cached is not None:
                result, close_yes_ask, close_no_ask = cached
            else:
                market = await loop.run_in_executor(None, self._client.get_market, ticker)
                if not market:
                    continue
                if market.get("status", "") not in ("finalized", "settled"):
                    continue
                result = market.get("result")
                if not result:
                    continue
                close_yes_ask = _safe_float(market.get("yes_ask_dollars"))
                close_no_ask = _safe_float(market.get("no_ask_dollars"))
                self._remember_settled(ticker, result, close_yes_ask, close_no_ask)

            # Settle all trades for this market
            for trade in trades:
                is_live = trade["status"] == "filled"
                settled_status = "settled" if is_live else "paper_settled"
                # 2026-05-20: use realized fill price for LIVE trades so PnL
                # reflects actual paid, not signal-price intended. Paper trades
                # have realized_fill_price = NULL and fall back to signal price.
                # Pair with order_manager fix that stores actual size + fee in
                # the trade row so contracts = size / effective_price stays
                # accurate. Past LIVE rows (pre-fix) will need a one-off
                # backfill if exact PnL matters historically.
                effective_price = trade.get("realized_fill_price")
                if effective_price is None or effective_price <= 0:
                    effective_price = trade["price"]
                pnl = self.calculate_pnl(
                    direction=trade["direction"],
                    price=effective_price,
                    size_usdc=trade["size"],
                    fee=trade["fee"],
                    result=result,
                )
                await self._db.settle_trade(trade["id"], pnl, status=settled_status)
                # Best-effort closing CLV snapshot (ignore errors — settlement must continue)
                try:
                    await self._db.record_clv_snapshot(
                        trade_id=trade["id"], snapshot_at="closing",
                        yes_ask=close_yes_ask, no_ask=close_no_ask, pinnacle_prob=None,
                    )
                except Exception as e:
                    logger.debug(f"[SETTLE] CLV closing snapshot failed for trade {trade['id']}: {e}")
                # Bankroll: only paper trades touch order_manager.bankroll.
                # Live wins/losses settle directly on Kalshi's real balance
                # — crediting the paper bankroll here would inflate it with
                # live winnings and mix the two modes' accounting.
                if not is_live and trade["direction"] == result:
                    payout = trade["size"] / trade["price"]
                    self._order_manager.bankroll += payout
                    await self._order_manager._persist_bankroll()
                # Circuit-breaker counters fire on both paper & live so a
                # losing streak anywhere pauses all entries (see order_manager
                # docstring). Daily loss cap is split per-mode via _is_live.
                if pnl > 0:
                    self._order_manager.record_win(pnl, strategy=trade["strategy"])
                elif pnl < 0:
                    self._order_manager.record_loss(abs(pnl), strategy=trade["strategy"])
                total_pnl += pnl
                settled_count += 1
                logger.info(
                    f"[SETTLE{'-LIVE' if is_live else ''}] {ticker}: {trade['strategy']} "
                    f"{trade['direction']} @{trade['price']:.2f} → {result} | PnL: ${pnl:+.2f}"
                )
                # Telegram notification
                if self._notifier:
                    await self._notifier.notify_settlement(
                        ticker=ticker, strategy=trade["strategy"],
                        direction=trade["direction"], price=trade["price"],
                        pnl=pnl, bankroll=self._order_manager.bankroll,
                    )

        now = time.time()
        if settled_count > 0 or now - self._last_summary_time >= 300:
            unsettled_remaining = len(unsettled) - settled_count
            logger.info(
                f"[SETTLE] Settled {settled_count} trades (PnL: ${total_pnl:+.2f}), "
                f"{unsettled_remaining} still pending | Bankroll: ${self._order_manager.bankroll:.2f}"
            )
            self._last_summary_time = now
