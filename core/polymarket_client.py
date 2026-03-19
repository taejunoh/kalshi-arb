import logging
import time
from typing import Optional

logger = logging.getLogger("polymarket_client")

CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XRP"}
TIME_KEYWORDS = {"15 minute", "5 minute", "15-minute", "5-minute", "15min", "5min"}


class PolymarketClient:
    def __init__(self, config):
        self.config = config
        self._clob = None
        self._last_request_time = 0.0
        self._min_request_interval = 0.1

    def _init_clob(self):
        if self._clob is not None:
            return
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            creds = ApiCreds(
                api_key=self.config.POLY_API_KEY,
                api_secret=self.config.POLY_API_SECRET,
                api_passphrase=self.config.POLY_API_PASSPHRASE,
            )
            self._clob = ClobClient(
                self.config.CLOB_ENDPOINT, key=self.config.POLY_PRIVATE_KEY,
                creds=creds, chain_id=self.config.POLYGON_CHAIN_ID,
            )
            logger.info("CLOB client initialized")
        except ImportError:
            logger.warning("py-clob-client not available — running in offline mode")
        except Exception as e:
            logger.error(f"Failed to init CLOB client: {e}")

    def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def get_active_markets(self) -> list[dict]:
        self._init_clob()
        if self._clob is None:
            return []
        self._rate_limit()
        try:
            markets = self._clob.get_markets(active=True, closed=False)
            return self._filter_crypto_markets(markets)
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def _filter_crypto_markets(self, markets: list[dict]) -> list[dict]:
        result = []
        for m in markets:
            q = m.get("question", "").lower()
            has_time = any(kw in q for kw in TIME_KEYWORDS)
            has_asset = any(asset.lower() in q for asset in CRYPTO_ASSETS)
            if has_time and has_asset:
                result.append(m)
        return result

    def _extract_asset(self, market: dict) -> Optional[str]:
        q = market.get("question", "").upper()
        for asset in CRYPTO_ASSETS:
            if asset in q:
                return asset
        return None

    def get_token_ids(self, market: dict) -> tuple[str, str]:
        tokens = market.get("tokens", [])
        yes_id = no_id = None
        for t in tokens:
            outcome = t.get("outcome", "").lower()
            if outcome in ("yes", "up"):
                yes_id = t["token_id"]
            elif outcome in ("no", "down"):
                no_id = t["token_id"]
        return yes_id, no_id

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            resp = self._clob.get_last_trade_price(token_id)
            return float(resp.get("price", 0))
        except Exception as e:
            logger.error(f"Failed to get price for {token_id}: {e}")
            return None

    def get_order_book_depth(self, token_id: str) -> float:
        self._init_clob()
        if self._clob is None:
            return 0.0
        self._rate_limit()
        try:
            book = self._clob.get_order_book(token_id)
            bids = book.get("bids", [])
            return sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids)
        except Exception as e:
            logger.error(f"Failed to get order book for {token_id}: {e}")
            return 0.0

    def place_market_order(self, token_id, size, side="BUY"):
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            order_args = OrderArgs(token_id=token_id, price=0.99, size=size, side=side)
            signed = self._clob.create_order(order_args)
            resp = self._clob.post_order(signed, OrderType.FOK)
            logger.info(f"Market order placed: {side} {size} of {token_id}")
            return resp
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            return None

    def place_limit_order(self, token_id, price, size, side="BUY"):
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
            signed = self._clob.create_order(order_args)
            resp = self._clob.post_order(signed, OrderType.GTC)
            logger.info(f"Limit order placed: {side} {size}@{price} of {token_id}")
            return resp
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return None

    def cancel_order(self, order_id):
        self._init_clob()
        if self._clob is None:
            return False
        self._rate_limit()
        try:
            self._clob.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Cancel failed for {order_id}: {e}")
            return False

    def get_positions(self):
        self._init_clob()
        if self._clob is None:
            return []
        self._rate_limit()
        try:
            return self._clob.get_positions()
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def preflight_check(self) -> bool:
        """Verify API connectivity and credentials before live trading."""
        self._init_clob()
        if self._clob is None:
            logger.error("Preflight FAILED: CLOB client not initialized")
            return False
        try:
            self._rate_limit()
            markets = self._clob.get_markets(active=True, closed=False)
            logger.info(f"Preflight: API connected, {len(markets)} markets available")
            return True
        except Exception as e:
            logger.error(f"Preflight FAILED: {e}")
            return False

    def get_usdc_balance(self) -> Optional[float]:
        """Get USDC balance from Polymarket. Returns None on error."""
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            # Try to get balance via positions or account endpoint
            positions = self._clob.get_positions()
            # In practice, balance comes from the proxy wallet
            # For now, return None to indicate we need manual verification
            logger.info("Balance check: use Polymarket UI to verify USDC balance")
            return None
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return None
