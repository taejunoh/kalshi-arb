import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("spread_scanner")

# Map ticker prefix to Binance symbol
CRYPTO_SYMBOL_MAP = {"KXBTC": "BTC", "KXETH": "ETH", "KXSOL": "SOL", "KXXRP": "XRP"}


class SpreadScannerStrategy:
    """Scan crypto markets for mispricings using Binance spot price as fair value."""

    TRADED_TICKERS_FILE = "data/spread_traded_tickers.json"

    def __init__(self, config, order_manager, market_scanner, kalshi_client, price_feed=None):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._price_feed = price_feed
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: dict[str, float] = {}  # ticker -> trade_time (cooldown)

        # Load persisted traded tickers to survive restarts
        self._load_traded_tickers()

    def _load_traded_tickers(self):
        try:
            if os.path.exists(self.TRADED_TICKERS_FILE):
                with open(self.TRADED_TICKERS_FILE) as f:
                    data = json.load(f)
                self._traded_tickers.update(data)
                logger.info(f"[SPREAD] Loaded {len(data)} traded tickers from file")
        except Exception as e:
            logger.warning(f"[SPREAD] Could not load traded tickers: {e}")

    def _get_live_spread_exposure(self) -> float:
        """Get total exposure from Kalshi API for crypto positions.
        Returns large value on failure to prevent trading when unknown."""
        try:
            positions = self._client.get_positions()
            return sum(
                float(p.get("market_exposure_dollars", 0))
                for p in positions
                if any(s in p.get("ticker", "") for s in ["KXBTC", "KXETH", "KXSOL", "KXXRP"])
                and float(p.get("position_fp", 0)) != 0
            )
        except Exception as e:
            logger.error(f"[SPREAD] Failed to get exposure, blocking trades: {e}")
            return float("inf")

    def _save_traded_tickers(self):
        try:
            with open(self.TRADED_TICKERS_FILE, "w") as f:
                json.dump(self._traded_tickers, f)
        except Exception as e:
            logger.warning(f"[SPREAD] Could not save traded tickers: {e}")

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

    def _normal_cdf(self, x: float) -> float:
        """Approximate Normal CDF using error function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def estimate_probability(self, ticker: str, current_price: float,
                             hours_to_close: float = 1.0,
                             volatility: Optional[float] = None,
                             momentum: float = 0.0) -> Optional[float]:
        """Estimate YES probability using volatility-based model (simplified Black-Scholes).

        P(price > K at settle) = Φ(z) where z = (ln(S/K) + drift) / (σ√T)
        - S = current spot, K = threshold, T = time in years
        - σ = annualized volatility from Binance trades
        - drift = momentum adjustment (positive = bullish bias)
        """
        parsed = self._parse_threshold(ticker)
        if parsed is None:
            return None
        market_type, threshold = parsed

        if threshold <= 0 or current_price <= 0:
            return None

        if market_type == "above":
            # Default volatility: ~60% annualized for crypto (conservative)
            vol = volatility if volatility and volatility > 0.01 else 0.60
            T = max(hours_to_close / 8760.0, 0.0001)  # hours → years
            vol_sqrt_T = vol * math.sqrt(T)

            if vol_sqrt_T < 0.0001:
                # Near-zero time: price is basically at current level
                return 0.97 if current_price > threshold else 0.03

            z = math.log(current_price / threshold) / vol_sqrt_T
            # Momentum adjustment: add directly to z-score, capped at ±1.0
            # 2% 15-min momentum → z shift ~0.4 → prob shift ~15%
            # 5% momentum → z shift 1.0 → prob shift ~34%
            momentum_z = max(-1.0, min(1.0, momentum * 20))
            z += momentum_z
            prob = self._normal_cdf(z)
            return max(0.03, min(0.97, prob))

        elif market_type == "range":
            # Range markets: probability of landing near threshold
            vol = volatility if volatility and volatility > 0.01 else 0.60
            T = max(hours_to_close / 8760.0, 0.0001)
            vol_sqrt_T = vol * math.sqrt(T)
            if vol_sqrt_T < 0.0001:
                return 0.03
            distance = abs(math.log(current_price / threshold)) / vol_sqrt_T
            prob = max(0.02, 0.4 * math.exp(-0.5 * distance ** 2))
            return max(0.02, min(0.50, prob))

        return None

    def evaluate_crypto(self, yes_ask: float, no_ask: float, fair_prob: float) -> Optional[dict]:
        """Compare market price to Binance-estimated fair value.
        Bidirectional: picks whichever side has better net edge after fees."""
        min_edge = self.config.SPREAD_SCANNER_MIN_EDGE
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # YES side edge
        yes_fee_pct = min(fee, yes_ask, 1 - yes_ask) / yes_ask if yes_ask > 0 else 0
        yes_edge = fair_prob - yes_ask * (1 + yes_fee_pct)

        # NO side edge
        no_fee_pct = min(fee, no_ask, 1 - no_ask) / no_ask if no_ask > 0 else 0
        no_edge = (1.0 - fair_prob) - no_ask * (1 + no_fee_pct)

        # Pick better side if above threshold
        if yes_edge > min_edge and yes_edge >= no_edge:
            return {"side": "yes", "price": yes_ask, "edge": yes_edge, "fair": fair_prob}
        elif no_edge > min_edge:
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
        if self._order_manager.is_circuit_breaker_active(strategy="spread_scanner"):
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

            # V2: volatility + momentum for probability estimation
            volatility = self._price_feed.get_volatility(symbol)
            candle_open = self._price_feed.get_candle_open(symbol)
            momentum = 0.0
            if candle_open and candle_open > 0:
                momentum = (spot_price - candle_open) / candle_open

            fair_prob = self.estimate_probability(
                ticker, spot_price, hours_to_close=hours,
                volatility=volatility, momentum=momentum,
            )
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

        # Total exposure limit: max 30% of bankroll in spread positions
        # Use Kalshi API for real exposure (survives restarts)
        current_exposure = self._get_live_spread_exposure()
        max_exposure = self._order_manager.bankroll * 0.30
        if current_exposure > max_exposure:
            return

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
                self._save_traded_tickers()
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
