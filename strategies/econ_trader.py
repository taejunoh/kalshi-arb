"""Economic indicator trading strategy — CPI/GDP markets."""
import asyncio
import json
import logging
import os
import time
from typing import Optional

from core.econ_data import EconDataProvider

logger = logging.getLogger("econ_trader")


class EconTraderStrategy:
    """Trade CPI/GDP markets using BLS data + statistical model."""

    TRADED_TICKERS_FILE = "data/econ_traded_tickers.json"

    def __init__(self, config, order_manager, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._client = kalshi_client
        self._econ = EconDataProvider()
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: set[str] = set()
        self._data_loaded = False

        self._load_traded_tickers()

    def _get_live_econ_exposure(self) -> float:
        """Get total exposure from Kalshi API for econ positions."""
        try:
            positions = self._client.get_positions()
            return sum(
                float(p.get("market_exposure_dollars", 0))
                for p in positions
                if any(s in p.get("ticker", "") for s in ["KXCPI", "KXGDP", "KXUNEMPLOY"])
                and float(p.get("position_fp", 0)) != 0
            )
        except Exception as e:
            logger.error(f"[ECON] Failed to get exposure, blocking trades: {e}")
            return float("inf")

    def _gate_fee_pct(self, price: float) -> float:
        """④ per-mode gate fee. With REAL_FEE_GATE_MODE='paper', a non-live
        strategy uses the real Kalshi fee 0.07*(1-p) as the gate; LIVE strategies
        (and modes 'off'/'all') delegate to config.gate_fee_pct unchanged."""
        cfg = self.config
        if cfg.REAL_FEE_GATE_MODE == "paper" and not cfg.is_live_strategy(getattr(self, "STRATEGY_NAME", "")):
            return 0.0 if price <= 0 or price >= 1 else 0.07 * (1.0 - price)
        return cfg.gate_fee_pct(price)

    def _load_traded_tickers(self):
        try:
            if os.path.exists(self.TRADED_TICKERS_FILE):
                with open(self.TRADED_TICKERS_FILE) as f:
                    self._traded_tickers = set(json.load(f))
                logger.info(f"[ECON] Loaded {len(self._traded_tickers)} traded tickers")
        except Exception as e:
            logger.warning(f"[ECON] Could not load traded tickers: {e}")

    def _save_traded_tickers(self):
        try:
            with open(self.TRADED_TICKERS_FILE, "w") as f:
                json.dump(sorted(self._traded_tickers), f)
        except Exception as e:
            logger.warning(f"[ECON] Could not save traded tickers: {e}")

    def _parse_cpi_threshold(self, ticker: str) -> Optional[float]:
        """Extract CPI threshold from ticker. KXCPI-26MAR-T0.9 → 0.9"""
        parts = ticker.split("-")
        if len(parts) < 3:
            return None
        suffix = parts[-1]
        if suffix.startswith("T"):
            try:
                return float(suffix[1:])
            except ValueError:
                return None
        return None

    def _parse_gdp_threshold(self, ticker: str) -> Optional[float]:
        """Extract GDP threshold from ticker. KXGDP-26APR30-T2.5 → 2.5"""
        parts = ticker.split("-")
        if len(parts) < 3:
            return None
        suffix = parts[-1]
        if suffix.startswith("T"):
            try:
                return float(suffix[1:])
            except ValueError:
                return None
        return None

    async def scan_and_execute(self):
        if not getattr(self.config, "ECON_ENABLED", False):
            return
        if self._order_manager.is_circuit_breaker_active(strategy="econ_trader"):
            return

        now = time.time()
        scan_interval = getattr(self.config, "ECON_SCAN_INTERVAL", 3600)  # 1 hour default
        if now - self._last_scan_time < scan_interval:
            return
        self._last_scan_time = now

        # Load economic data (once per scan, cached)
        if not self._data_loaded:
            await self._econ.fetch_cpi_history()
            self._data_loaded = True

        # Total exposure limit: max 30% of bankroll. _get_live_econ_exposure
        # is sync (calls _client.get_positions) — dispatched via to_thread.
        total_exposure = await asyncio.to_thread(self._get_live_econ_exposure)
        max_exposure = self._order_manager.bankroll * 0.30
        if total_exposure > max_exposure:
            return

        trades_attempted = 0

        # --- CPI Markets (max 5 per cycle) ---
        cpi_estimate = self._econ.estimate_cpi_mom(n_months=6)
        if cpi_estimate:
            trades_attempted += await self._scan_cpi_markets(cpi_estimate)

        if now - self._last_summary_time >= 300:
            logger.info(f"[ECON] Scan complete: {trades_attempted} trades")
            self._last_summary_time = now

    async def _scan_cpi_markets(self, estimate: dict) -> int:
        """Scan KXCPI markets and trade based on statistical edge."""
        try:
            data = await asyncio.to_thread(
                self._client._get,
                "/trade-api/v2/markets?status=open&series_ticker=KXCPI&limit=200",
            )
            markets = data.get("markets", []) if data else []
        except Exception as e:
            logger.error(f"[ECON] Failed to fetch CPI markets: {e}")
            return 0

        mean = estimate["mean"]
        std = estimate["std"]
        min_edge = getattr(self.config, "ECON_MIN_EDGE", 0.10)  # 10% default
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        trades = 0
        max_trades = 5  # max per cycle
        for market in markets:
            if trades >= max_trades:
                break

            ticker = market["ticker"]
            if ticker in self._traded_tickers:
                continue

            threshold = self._parse_cpi_threshold(ticker)
            if threshold is None:
                continue

            ya = float(market.get("yes_ask_dollars", "0"))
            na = float(market.get("no_ask_dollars", "0"))
            if ya < 0.03 or na < 0.03:
                continue

            # P(CPI MoM > threshold)
            fair_prob = self._econ.probability_above_threshold(threshold, mean, std)
            fair_prob = max(0.02, min(0.98, fair_prob))

            # Edge calculation (net of fees)
            yes_fee_pct = self._gate_fee_pct(ya) if ya > 0 else 0
            no_fee_pct = self._gate_fee_pct(na) if na > 0 else 0
            yes_edge = fair_prob - ya * (1 + yes_fee_pct)
            no_edge = (1 - fair_prob) - na * (1 + no_fee_pct)

            # Pick best side
            if yes_edge > min_edge and yes_edge >= no_edge:
                side, edge, price = "yes", yes_edge, ya
            elif no_edge > min_edge:
                side, edge, price = "no", no_edge, na
            else:
                continue

            # Sizing: 5% bankroll per CPI trade
            size = self._order_manager.bankroll * 0.05
            if size < 1.0:
                continue

            limit_price = round(fair_prob if side == "yes" else (1 - fair_prob), 2)
            limit_price = max(0.01, min(0.99, limit_price))

            logger.info(
                f"[ECON] {ticker}: CPI>{threshold}% mean={mean:.3f}% std={std:.3f}% "
                f"fair={fair_prob:.2f} ask={ya:.2f}/{na:.2f} edge={edge:.2f} "
                f"→ {side} ${size:.2f} LIMIT@{limit_price}"
            )

            self._traded_tickers.add(ticker)
            self._save_traded_tickers()
            await self._order_manager.limit_order(
                ticker=ticker, side=side, price=limit_price,
                size_usdc=size, strategy="econ_trader", market_id=ticker,
            )
            trades += 1

        return trades
