import base64
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("kalshi_client")

CRYPTO_SERIES_PREFIXES = {"KXBTC", "KXETH", "KXSOL", "KXXRP"}


class KalshiClient:
    def __init__(self, config):
        self.config = config
        self._http: Optional[httpx.Client] = None
        self._private_key = None
        self._last_request_time = 0.0
        self._min_request_interval = 0.1

    def _init_http(self):
        if self._http is not None:
            return
        try:
            self._http = httpx.Client(base_url=self.config.KALSHI_API_BASE_URL, timeout=10.0)
            self._load_private_key()
            logger.info("Kalshi HTTP client initialized")
        except Exception as e:
            logger.error(f"Failed to init Kalshi client: {e}")

    def _load_private_key(self):
        key_path = self.config.KALSHI_RSA_PRIVATE_KEY_PATH
        if not key_path:
            logger.warning("No RSA private key path configured — running in offline mode")
            return
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with open(key_path, "rb") as f:
                self._private_key = load_pem_private_key(f.read(), password=None)
            logger.info("RSA private key loaded")
        except FileNotFoundError:
            logger.error(f"RSA private key not found: {key_path}")
        except Exception as e:
            logger.error(f"Failed to load RSA key: {e}")

    def _sign_request(self, timestamp_ms: int, method: str, path: str) -> str:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes
        message = f"{timestamp_ms}{method}{path}"
        signature = self._private_key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        if not isinstance(signature, bytes):
            signature = bytes(str(signature), "utf-8")
        return base64.b64encode(signature).decode()

    def _auth_headers(self, method: str, path: str) -> dict:
        ts = int(time.time() * 1000)
        sig = self._sign_request(ts, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "Content-Type": "application/json",
        }

    def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _get(self, path: str):
        self._init_http()
        if self._http is None:
            return None
        self._rate_limit()
        headers = self._auth_headers("GET", path) if self._private_key else {}
        resp = self._http.get(path, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_data: dict):
        self._init_http()
        if self._http is None:
            return None
        self._rate_limit()
        headers = self._auth_headers("POST", path) if self._private_key else {}
        resp = self._http.post(path, json=json_data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str):
        self._init_http()
        if self._http is None:
            return None
        self._rate_limit()
        headers = self._auth_headers("DELETE", path) if self._private_key else {}
        resp = self._http.delete(path, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_active_markets(self) -> list[dict]:
        try:
            data = self._get("/trade-api/v2/markets?status=open")
            if data is None:
                return []
            markets = data.get("markets", [])
            return self._filter_crypto_markets(markets)
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def _filter_crypto_markets(self, markets: list[dict]) -> list[dict]:
        result = []
        for m in markets:
            ticker = m.get("ticker", "")
            prefix = ticker.split("-")[0] if "-" in ticker else ticker
            if prefix in CRYPTO_SERIES_PREFIXES:
                result.append(m)
        return result

    def get_market(self, ticker: str) -> Optional[dict]:
        try:
            data = self._get(f"/trade-api/v2/markets/{ticker}")
            return data.get("market") if data else None
        except Exception as e:
            logger.error(f"Failed to get market {ticker}: {e}")
            return None

    def get_market_prices(self, ticker: str) -> tuple[Optional[float], Optional[float]]:
        """Return (yes_price, no_price) as decimals (0.00-1.00)."""
        try:
            data = self._get(f"/trade-api/v2/markets/{ticker}")
            if data is None:
                return None, None
            market = data.get("market", {})
            yes_cents = market.get("yes_ask")
            no_cents = market.get("no_ask")
            if yes_cents is None or no_cents is None:
                return None, None
            return yes_cents / 100, no_cents / 100
        except Exception as e:
            logger.error(f"Failed to get prices for {ticker}: {e}")
            return None, None

    def get_order_book_depth(self, ticker: str) -> tuple[float, float]:
        """Return (yes_depth_usd, no_depth_usd) from the order book."""
        try:
            data = self._get(f"/trade-api/v2/markets/{ticker}/orderbook")
            if data is None:
                return 0.0, 0.0
            book = data.get("orderbook", {})
            yes_levels = book.get("yes", [])
            no_levels = book.get("no", [])
            yes_depth = sum(qty * (price_cents / 100) for price_cents, qty in yes_levels)
            no_depth = sum(qty * (price_cents / 100) for price_cents, qty in no_levels)
            return yes_depth, no_depth
        except Exception as e:
            logger.error(f"Failed to get order book for {ticker}: {e}")
            return 0.0, 0.0

    def place_market_order(self, ticker: str, side: str, count: int):
        try:
            data = self._post("/trade-api/v2/portfolio/orders", {
                "ticker": ticker, "action": "buy", "side": side,
                "count": count, "type": "market",
            })
            if data is None:
                return None
            logger.info(f"Market order placed: {side} {count}x {ticker}")
            return data.get("order", {})
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            return None

    def place_limit_order(self, ticker: str, side: str, price_cents: int, count: int):
        try:
            data = self._post("/trade-api/v2/portfolio/orders", {
                "ticker": ticker, "action": "buy", "side": side,
                "count": count, "type": "limit", "yes_price": price_cents,
            })
            if data is None:
                return None
            logger.info(f"Limit order placed: {side} {count}x {ticker} @ {price_cents}c")
            return data.get("order", {})
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._delete(f"/trade-api/v2/portfolio/orders/{order_id}")
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Cancel failed for {order_id}: {e}")
            return False

    def get_positions(self):
        try:
            data = self._get("/trade-api/v2/portfolio/positions")
            if data is None:
                return []
            return data.get("market_positions", [])
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_balance(self) -> Optional[float]:
        try:
            data = self._get("/trade-api/v2/portfolio/balance")
            if data is None:
                return None
            return data.get("balance", 0) / 100  # cents to dollars
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return None

    def preflight_check(self) -> bool:
        """Verify API connectivity before live trading."""
        try:
            balance = self.get_balance()
            if balance is None:
                logger.error("Preflight FAILED: could not get balance")
                return False
            logger.info(f"Preflight: balance ${balance:.2f}")
            markets = self.get_active_markets()
            logger.info(f"Preflight: API connected, {len(markets)} crypto markets available")
            return True
        except Exception as e:
            logger.error(f"Preflight FAILED: {e}")
            return False
