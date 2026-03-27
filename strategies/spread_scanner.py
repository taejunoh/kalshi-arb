import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("spread_scanner")


class SpreadScannerStrategy:
    """Scan all markets for wide bid-ask spreads and trade toward fair value."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: dict[str, float] = {}  # ticker -> trade_time (cooldown 1 hour)
        self._eval_cache: dict[str, float] = {}  # ticker -> last_eval_time (short cooldown)

    def calculate_spread(self, yes_ask: float, no_ask: float) -> float:
        """Spread = how much you overpay buying both sides. Lower = tighter."""
        return (yes_ask + no_ask) - 1.0

    def calculate_vwap_mid(self, levels: dict) -> Optional[float]:
        """Volume-weighted mid price from orderbook levels."""
        yes_levels = levels.get("yes", [])
        no_levels = levels.get("no", [])
        # Require at least 2 levels on each side for meaningful VWAP
        if len(yes_levels) < 2 or len(no_levels) < 2:
            return None
        # VWAP of yes side (weighted by qty)
        yes_value = sum(p * q for p, q in yes_levels)
        yes_vol = sum(q for _, q in yes_levels)
        # VWAP of no side
        no_value = sum(p * q for p, q in no_levels)
        no_vol = sum(q for _, q in no_levels)
        if yes_vol == 0 or no_vol == 0:
            return None
        yes_vwap = yes_value / yes_vol
        no_vwap = no_value / no_vol
        # Both VWAPs should be in reasonable range
        if yes_vwap < 0.10 or no_vwap < 0.10:
            return None
        # Mid is where yes_vwap and (1 - no_vwap) meet
        return (yes_vwap + (1.0 - no_vwap)) / 2

    def score_market(self, spread: float, volume: float, days_to_close: float) -> float:
        """Higher score = more attractive opportunity."""
        if days_to_close <= 0:
            return 0.0
        return spread * volume / days_to_close

    def evaluate(self, yes_ask: float, no_ask: float, vwap_mid: float) -> Optional[dict]:
        """If mid price deviates from VWAP, take the underpriced side."""
        current_mid = (yes_ask + (1.0 - no_ask)) / 2
        deviation = vwap_mid - current_mid
        # Require meaningful deviation — at least 10% edge
        if abs(deviation) < 0.10:
            return None
        if deviation > 0:
            # VWAP says price should be higher → YES is underpriced
            return {"side": "yes", "price": yes_ask, "edge": deviation}
        else:
            # VWAP says price should be lower → NO is underpriced
            return {"side": "no", "price": no_ask, "edge": abs(deviation)}

    def _days_to_close(self, market: dict) -> float:
        close_str = market.get("close_time")
        if not close_str:
            return 30.0  # Default if unknown
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return max((close - now).total_seconds() / 86400, 0.01)
        except (ValueError, TypeError):
            return 30.0

    async def scan_and_execute(self):
        if not self.config.SPREAD_SCANNER_ENABLED:
            return
        if self._order_manager.is_circuit_breaker_active():
            return
        now = time.time()
        if now - self._last_scan_time < self.config.SPREAD_SCANNER_INTERVAL:
            return
        self._last_scan_time = now

        markets = self._scanner.get_all_markets()
        scored = []
        for market in markets:
            ticker = market["ticker"]
            # Skip recently traded tickers (1 hour cooldown)
            if now - self._traded_tickers.get(ticker, 0) < 3600:
                continue
            if now - self._eval_cache.get(ticker, 0) < 60:
                continue
            yes_ask_str = market.get("yes_ask_dollars")
            no_ask_str = market.get("no_ask_dollars")
            volume_str = market.get("volume_fp", market.get("volume_24h_fp", "0"))
            if not yes_ask_str or not no_ask_str:
                continue
            yes_ask = float(yes_ask_str)
            no_ask = float(no_ask_str)
            volume = float(volume_str)
            if volume < self.config.SPREAD_SCANNER_MIN_VOLUME:
                continue
            # Skip extreme prices — low prices aren't "undervalued", they're low probability
            if yes_ask < 0.15 or no_ask < 0.15:
                continue
            # Skip if both sides aren't reasonably priced (avoid one-sided markets)
            if yes_ask > 0.90 or no_ask > 0.90:
                continue
            spread = self.calculate_spread(yes_ask, no_ask)
            if spread < self.config.SPREAD_SCANNER_MIN_WIDTH:
                continue
            days = self._days_to_close(market)
            score = self.score_market(spread, volume, days)
            scored.append((score, ticker, market, yes_ask, no_ask))

        # Sort by score descending, take top 5
        scored.sort(key=lambda x: x[0], reverse=True)
        trades_attempted = 0
        for score, ticker, market, yes_ask, no_ask in scored[:5]:
            try:
                self._eval_cache[ticker] = now
                levels = self._client.get_orderbook_levels(ticker)
                vwap_mid = self.calculate_vwap_mid(levels)
                if vwap_mid is None:
                    logger.debug(f"[SPREAD] {ticker}: skipped — one-sided orderbook")
                    continue
                signal = self.evaluate(yes_ask, no_ask, vwap_mid)
                if signal is None:
                    logger.debug(f"[SPREAD] {ticker}: skipped — deviation too small")
                    continue
                size = self._order_manager.calculate_position_size(signal["edge"])
                if size < 1.0:
                    logger.debug(f"[SPREAD] {ticker}: skipped — size ${size:.2f} too small")
                    continue
                # Use limit order (Maker) at VWAP fair value for better entry
                limit_price = round(vwap_mid if signal["side"] == "yes" else (1.0 - vwap_mid), 2)
                limit_price = max(0.01, min(0.99, limit_price))
                logger.info(
                    f"[SPREAD] {ticker}: spread={self.calculate_spread(yes_ask, no_ask):.3f} "
                    f"edge={signal['edge']:.3f} → {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}"
                )
                await self._order_manager.limit_order(
                    ticker=ticker, side=signal["side"], price=limit_price,
                    size_usdc=size, strategy="spread_scanner", market_id=ticker,
                )
                self._traded_tickers[ticker] = now
                trades_attempted += 1
            except Exception as e:
                logger.error(f"Spread scanner error on {ticker}: {e}")

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[SPREAD] Scanned {len(markets)} markets, "
                f"{len(scored)} wide spreads, {trades_attempted} trades"
            )
            self._last_summary_time = now
