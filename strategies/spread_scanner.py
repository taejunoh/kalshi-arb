import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("spread_scanner")

# Map ticker prefix to Binance symbol
CRYPTO_SYMBOL_MAP = {"KXBTC": "BTC", "KXETH": "ETH", "KXSOL": "SOL", "KXXRP": "XRP"}


class SpreadScannerStrategy:
    """Scan crypto markets for mispricings using Binance spot price as fair value."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client, price_feed=None):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._price_feed = price_feed
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: dict[str, float] = {}  # ticker -> trade_time (cooldown)

    def _get_symbol(self, ticker: str) -> Optional[str]:
        """Extract crypto symbol from ticker (e.g., KXBTC-... → BTC)."""
        prefix = ticker.split("-")[0] if "-" in ticker else ""
        return CRYPTO_SYMBOL_MAP.get(prefix)

    def _parse_threshold(self, ticker: str) -> Optional[tuple[str, float]]:
        """Parse threshold from ticker suffix. Returns (type, value).
        B75000 → ("above", 75000.0)
        T76249.99 → ("range", 76249.99)
        """
        parts = ticker.split("-")
        if len(parts) < 3:
            return None
        suffix = parts[-1]
        if suffix.startswith("B"):
            try:
                return ("above", float(suffix[1:]))
            except ValueError:
                return None
        elif suffix.startswith("T"):
            try:
                return ("range", float(suffix[1:]))
            except ValueError:
                return None
        return None

    def estimate_probability(self, ticker: str, current_price: float) -> Optional[float]:
        """Estimate YES probability from Binance spot price vs market threshold."""
        parsed = self._parse_threshold(ticker)
        if parsed is None:
            return None
        market_type, threshold = parsed

        if market_type == "above":
            # "BTC above $X" — probability depends on distance from threshold
            distance_pct = (current_price - threshold) / threshold
            # Sigmoid: sharp transition around threshold
            prob = 1.0 / (1.0 + math.exp(-distance_pct * 200))
            return max(0.03, min(0.97, prob))
        elif market_type == "range":
            # Range markets are harder — low probability for exact ranges
            distance_pct = abs(current_price - threshold) / current_price
            # Closer to threshold = higher probability
            prob = max(0.02, 0.5 * math.exp(-distance_pct * 100))
            return max(0.02, min(0.50, prob))
        return None

    def evaluate_crypto(self, yes_ask: float, no_ask: float, fair_prob: float) -> Optional[dict]:
        """Compare market price to Binance-estimated fair value. NO-only mode."""
        min_edge = self.config.SPREAD_SCANNER_MIN_EDGE
        # NO side only — backtest shows NO is more profitable and predictable
        no_edge = (1.0 - fair_prob) - no_ask
        if no_edge > min_edge:
            return {"side": "no", "price": no_ask, "edge": no_edge, "fair": fair_prob}
        return None

    def _hours_to_close(self, market: dict) -> float:
        close_str = market.get("close_time")
        if not close_str:
            return 999.0
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return max((close - now).total_seconds() / 3600, 0.01)
        except (ValueError, TypeError):
            return 999.0

    async def scan_and_execute(self):
        if not self.config.SPREAD_SCANNER_ENABLED:
            return
        if self._order_manager.is_circuit_breaker_active():
            return
        now = time.time()
        if now - self._last_scan_time < self.config.SPREAD_SCANNER_INTERVAL:
            return
        self._last_scan_time = now

        # Use crypto market pool (short-duration) instead of monthly events
        markets = self._scanner.get_active_markets()
        candidates = []
        max_hours = getattr(self.config, "SPREAD_SCANNER_MAX_HOURS", 12)

        for market in markets:
            ticker = market["ticker"]
            # Skip recently traded (1 hour cooldown)
            if now - self._traded_tickers.get(ticker, 0) < 3600:
                continue

            hours = self._hours_to_close(market)
            if hours > max_hours or hours < 0.05:  # Skip >12h and <3min
                continue

            symbol = self._get_symbol(ticker)
            if not symbol or not self._price_feed:
                continue

            spot_price = self._price_feed.get_price(symbol)
            if spot_price is None:
                continue
            if self._price_feed.is_stale(symbol):
                continue

            fair_prob = self.estimate_probability(ticker, spot_price)
            if fair_prob is None:
                continue

            yes_ask_str = market.get("yes_ask_dollars")
            no_ask_str = market.get("no_ask_dollars")
            if not yes_ask_str or not no_ask_str:
                continue

            yes_ask = float(yes_ask_str)
            no_ask = float(no_ask_str)

            # Skip extreme prices
            if yes_ask < 0.05 or no_ask < 0.05:
                continue

            signal = self.evaluate_crypto(yes_ask, no_ask, fair_prob)
            if signal is None:
                continue

            candidates.append((signal["edge"], ticker, market, yes_ask, no_ask, signal, fair_prob, hours))

        # Sort by edge descending, take top 5
        candidates.sort(key=lambda x: x[0], reverse=True)
        trades_attempted = 0

        for edge, ticker, market, yes_ask, no_ask, signal, fair_prob, hours in candidates[:5]:
            try:
                size = self._order_manager.calculate_position_size(signal["edge"])
                if size < 1.0:
                    continue
                # Limit order at fair value
                limit_price = round(fair_prob if signal["side"] == "yes" else (1.0 - fair_prob), 2)
                limit_price = max(0.01, min(0.99, limit_price))
                logger.info(
                    f"[SPREAD] {ticker}: fair={fair_prob:.2f} ask={yes_ask:.2f}/{no_ask:.2f} "
                    f"edge={signal['edge']:.2f} → {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f} ({hours:.1f}h)"
                )
                # Mark BEFORE order to prevent duplicates even if order/DB fails
                self._traded_tickers[ticker] = now
                await self._order_manager.limit_order(
                    ticker=ticker, side=signal["side"], price=limit_price,
                    size_usdc=size, strategy="spread_scanner", market_id=ticker,
                )
                trades_attempted += 1
            except Exception as e:
                logger.error(f"Spread scanner error on {ticker}: {e}")

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[SPREAD] Scanned {len(markets)} crypto markets, "
                f"{len(candidates)} opportunities, {trades_attempted} trades"
            )
            self._last_summary_time = now
