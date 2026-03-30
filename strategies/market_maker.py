import logging
import time
from typing import Optional

logger = logging.getLogger("market_maker")


class MarketMakerStrategy:
    """Two-sided market making: post bid+ask and profit from the spread."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._active_quotes: dict[str, dict] = {}
        # {ticker: {yes_order_id, no_order_id, last_quote_time, mid_price, size_usdc}}
        self._last_summary_time = 0.0

    def calculate_mid_price(self, levels: dict) -> Optional[float]:
        """Volume-weighted mid from top orderbook levels."""
        yes_levels = levels.get("yes", [])
        no_levels = levels.get("no", [])
        if not yes_levels or not no_levels:
            return None
        # Best ask on each side
        best_yes = yes_levels[0][0]
        best_no = no_levels[0][0]
        # Volume-weighted average of top 3 levels per side
        def vwap(lvls, n=3):
            subset = lvls[:n]
            total_val = sum(p * q for p, q in subset)
            total_qty = sum(q for _, q in subset)
            return total_val / total_qty if total_qty > 0 else subset[0][0]
        yes_vwap = vwap(yes_levels)
        no_vwap = vwap(no_levels)
        return (yes_vwap + (1.0 - no_vwap)) / 2

    def calculate_quote_prices(self, mid: float, minutes_to_close: float = 60) -> tuple[float, float]:
        """Return (yes_bid, no_bid) prices. Tighter spread as market closes."""
        base_spread = self.config.MM_SPREAD_CENTS
        if minutes_to_close < 2:
            spread_cents = max(1, base_spread - 3)
        elif minutes_to_close < 5:
            spread_cents = max(2, base_spread - 2)
        elif minutes_to_close < 10:
            spread_cents = max(2, base_spread - 1)
        else:
            spread_cents = base_spread
        half_spread = spread_cents / 100 / 2
        yes_bid = max(0.01, round(mid - half_spread, 2))
        no_bid = max(0.01, round((1.0 - mid) - half_spread, 2))
        return yes_bid, no_bid

    def apply_inventory_skew(self, yes_bid: float, no_bid: float, ticker: str) -> tuple[float, float]:
        """Skew quotes based on current inventory to flatten position."""
        pos = self._order_manager.get_position("market_maker", ticker)
        if pos["qty"] == 0:
            return yes_bid, no_bid
        skew = 0.01  # 1 cent skew per unit
        if pos["side"] == "yes":
            # Long YES → widen yes bid (less aggressive buying), tighten no bid
            yes_bid = max(0.01, yes_bid - skew)
            no_bid = min(0.99, no_bid + skew)
        else:
            # Long NO → widen no bid, tighten yes bid
            no_bid = max(0.01, no_bid - skew)
            yes_bid = min(0.99, yes_bid + skew)
        return yes_bid, no_bid

    def _minutes_to_close(self, ticker: str) -> float:
        """Get minutes until market closes."""
        from datetime import datetime, timezone
        market = self._scanner.get_market(ticker)
        if not market:
            return 60.0
        close_str = market.get("close_time")
        if not close_str:
            return 60.0
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            return max((close - datetime.now(timezone.utc)).total_seconds() / 60, 0.1)
        except (ValueError, TypeError):
            return 60.0

    def _select_markets(self) -> list[dict]:
        """Select liquid crypto markets for market making."""
        markets = self._scanner.get_unlocked_markets()
        eligible = []
        for m in markets:
            vol_str = m.get("volume_fp", m.get("volume_24h_fp", "0"))
            volume = float(vol_str) if vol_str else 0
            if volume >= self.config.MM_MIN_VOLUME:
                eligible.append((volume, m))
        eligible.sort(key=lambda x: x[0], reverse=True)
        max_markets = self.config.MM_MAX_MARKETS
        return [m for _, m in eligible[:max_markets]]

    async def scan_and_execute(self):
        if not self.config.MM_ENABLED:
            return
        if self._order_manager.is_circuit_breaker_active():
            await self._cancel_all_quotes()
            return

        now = time.time()

        # Requote stale quotes
        for ticker in list(self._active_quotes):
            quote = self._active_quotes[ticker]
            if now - quote["last_quote_time"] >= self.config.MM_REQUOTE_INTERVAL:
                await self._requote(ticker)

        # Find new markets to quote (up to max)
        if len(self._active_quotes) < self.config.MM_MAX_MARKETS:
            markets = self._select_markets()
            for market in markets:
                ticker = market["ticker"]
                if ticker in self._active_quotes:
                    continue
                if len(self._active_quotes) >= self.config.MM_MAX_MARKETS:
                    break
                await self._place_initial_quotes(ticker)

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[MM] Active quotes on {len(self._active_quotes)} markets, "
                f"exposure=${self._order_manager.get_strategy_exposure('market_maker'):.2f}"
            )
            self._last_summary_time = now

    async def _place_initial_quotes(self, ticker: str):
        """Place initial two-sided quotes on a market."""
        levels = self._client.get_orderbook_levels(ticker)
        mid = self.calculate_mid_price(levels)
        if mid is None:
            return
        mtc = self._minutes_to_close(ticker)
        yes_bid, no_bid = self.calculate_quote_prices(mid, mtc)
        yes_bid, no_bid = self.apply_inventory_skew(yes_bid, no_bid, ticker)
        size = min(
            self._order_manager.calculate_position_size(self.config.MM_SPREAD_CENTS / 100),
            self.config.MM_MAX_POSITION,
        )
        if size < 1.0:
            return
        half_size = size / 2
        yes_result = await self._order_manager.limit_order(
            ticker=ticker, side="yes", price=yes_bid,
            size_usdc=half_size, strategy="market_maker", market_id=ticker,
        )
        no_result = await self._order_manager.limit_order(
            ticker=ticker, side="no", price=no_bid,
            size_usdc=half_size, strategy="market_maker", market_id=ticker,
        )
        self._active_quotes[ticker] = {
            "yes_order_id": yes_result.get("order_id"),
            "no_order_id": no_result.get("order_id"),
            "last_quote_time": time.time(),
            "mid_price": mid,
            "size_usdc": half_size,
        }
        logger.info(
            f"[MM] Quoting {ticker}: YES@{yes_bid:.2f} NO@{no_bid:.2f} "
            f"mid={mid:.3f} size=${size:.2f}"
        )

    async def _requote(self, ticker: str):
        """Cancel existing quotes and place new ones at updated prices."""
        quote = self._active_quotes.get(ticker)
        if not quote:
            return
        levels = self._client.get_orderbook_levels(ticker)
        mid = self.calculate_mid_price(levels)
        if mid is None:
            await self._cancel_quote(ticker)
            return
        mtc = self._minutes_to_close(ticker)
        yes_bid, no_bid = self.calculate_quote_prices(mid, mtc)
        yes_bid, no_bid = self.apply_inventory_skew(yes_bid, no_bid, ticker)
        # Requote both sides
        yes_result = await self._order_manager.requote(
            quote["yes_order_id"], ticker, "yes", yes_bid,
            quote["size_usdc"], "market_maker",
        )
        no_result = await self._order_manager.requote(
            quote["no_order_id"], ticker, "no", no_bid,
            quote["size_usdc"], "market_maker",
        )
        if yes_result and no_result:
            self._active_quotes[ticker] = {
                "yes_order_id": yes_result.get("order_id"),
                "no_order_id": no_result.get("order_id"),
                "last_quote_time": time.time(),
                "mid_price": mid,
                "size_usdc": quote["size_usdc"],
            }
            logger.debug(f"[MM] Requoted {ticker}: YES@{yes_bid:.2f} NO@{no_bid:.2f}")
        else:
            logger.warning(f"[MM] Requote failed for {ticker}, removing")
            del self._active_quotes[ticker]

    async def _cancel_quote(self, ticker: str):
        quote = self._active_quotes.pop(ticker, None)
        if quote:
            await self._order_manager.cancel_order(quote["yes_order_id"])
            await self._order_manager.cancel_order(quote["no_order_id"])

    async def _cancel_all_quotes(self):
        for ticker in list(self._active_quotes):
            await self._cancel_quote(ticker)
        logger.warning("[MM] All quotes cancelled (circuit breaker)")
