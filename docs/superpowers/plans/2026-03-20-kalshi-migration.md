# Polymarket → Kalshi Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all Polymarket CLOB API integration with Kalshi REST API v2, keeping the same two arbitrage strategies and paper/live trading modes.

**Architecture:** In-place replacement — delete `polymarket_client.py`, create `kalshi_client.py` using `httpx` + RSA signing. All internal prices remain as decimals (0.00–1.00); cent conversion happens at the API boundary. Market identification switches from `conditionId` + `token_id` pairs to single `ticker` with `side=yes/no`.

**Tech Stack:** Python 3.11, httpx (HTTP client), cryptography (RSA signing), FastAPI, aiosqlite, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-kalshi-migration-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `core/kalshi_client.py` | **Create** | Kalshi REST API v2 client with RSA auth, rate limiting |
| `core/polymarket_client.py` | **Delete** | Replaced by kalshi_client.py |
| `core/config.py` | **Edit** | Swap env vars (POLY_* → KALSHI_*), new fee formula |
| `core/order_manager.py` | **Edit** | Update param names (token_id → ticker), fee calls, response parsing |
| `core/market_scanner.py` | **Edit** | Use `ticker` instead of `conditionId` |
| `strategies/sum_arb.py` | **Edit** | Use `get_market_prices(ticker)` instead of separate token lookups |
| `strategies/maker_preorder.py` | **Edit** | Time-based filtering instead of regex, ticker-based orders |
| `main.py` | **Edit** | Import KalshiClient, update preflight |
| `monitoring/dashboard/api.py` | **Edit** | Update title string |
| `monitoring/dashboard/static/index.html` | **Edit** | Update title string |
| `.env.example` | **Edit** | New Kalshi env vars |
| `requirements.txt` | **Edit** | Swap py-clob-client → cryptography, add fastapi/uvicorn |
| `tests/test_kalshi_client.py` | **Create** | Tests for new Kalshi client |
| `tests/test_polymarket_client.py` | **Delete** | Replaced |
| `tests/test_config.py` | **Edit** | New fee tests |
| `tests/test_order_manager.py` | **Edit** | Update param names |
| `tests/test_market_scanner.py` | **Edit** | Use ticker instead of conditionId |
| `tests/test_sum_arb.py` | **Edit** | New fee formula in evaluate() |
| `tests/test_maker_preorder.py` | **Edit** | Time-based filtering tests |
| `tests/test_main.py` | **Edit** | Import swap |
| `tests/test_integration.py` | **Edit** | FakeKalshiClient replacing FakePolymarketClient |
| `tests/test_preflight.py` | **Edit** | Kalshi preflight tests |
| `tests/test_dashboard_api.py` | No change | No Polymarket references in test assertions |
| `docs/setup-guide.md` | **Rewrite** | Kalshi account setup instructions |

---

### Task 1: Config — swap env vars and fee formula

**Files:**
- Modify: `core/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for new Kalshi config**

Replace fee tests and add Kalshi env var tests in `tests/test_config.py`:

```python
# Replace test_dynamic_fee_calculation with:

def test_kalshi_fee_calculation():
    from core.config import Config
    cfg = Config()
    # Standard tier: $0.07/contract
    fee = cfg.estimate_fee(10, 50)
    assert fee == pytest.approx(0.70, abs=0.01)


def test_kalshi_fee_capped():
    from core.config import Config
    cfg = Config()
    # At price 5 cents, fee should be capped at 0.05 (not 0.07)
    fee = cfg.estimate_fee(1, 5)
    assert fee == pytest.approx(0.05, abs=0.01)


def test_kalshi_fee_per_contract():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee_per_contract(50)
    assert fee == pytest.approx(0.07, abs=0.01)


def test_kalshi_fee_per_contract_capped():
    from core.config import Config
    cfg = Config()
    fee = cfg.estimate_fee_per_contract(3)
    assert fee == pytest.approx(0.03, abs=0.01)


# Update test_config_loads_defaults to check KALSHI_API_KEY_ID instead of POLY vars
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `estimate_fee` and `estimate_fee_per_contract` don't exist yet

- [ ] **Step 3: Implement config changes**

Edit `core/config.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        # Kalshi API
        self.KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
        self.KALSHI_RSA_PRIVATE_KEY_PATH = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH", "")
        self.KALSHI_API_BASE_URL = os.getenv("KALSHI_API_BASE_URL", "https://trading-api.kalshi.com")
        self.KALSHI_FEE_PER_CONTRACT = float(os.getenv("KALSHI_FEE_PER_CONTRACT", "0.07"))

        # Trading mode
        self.TRADING_MODE = os.getenv("TRADING_MODE", "paper")
        self.INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))

        # Strategy parameters (unchanged)
        self.SUM_ARB_MIN_GAP = float(os.getenv("SUM_ARB_MIN_GAP", "0.03"))
        self.MAKER_TARGET_SUM = float(os.getenv("MAKER_TARGET_SUM", "0.96"))
        self.KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
        self.MAX_SINGLE_TRADE_PCT = float(os.getenv("MAX_SINGLE_TRADE_PCT", "0.10"))
        self.MAX_DIRECTIONAL_LOSS = float(os.getenv("MAX_DIRECTIONAL_LOSS", "50"))
        self.MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "200"))
        self.MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5"))

        # Monitoring
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        # Binance WebSocket URLs
        self.BINANCE_WS_URLS = {
            "BTC": "wss://stream.binance.com:9443/ws/btcusdt@aggTrade",
            "ETH": "wss://stream.binance.com:9443/ws/ethusdt@aggTrade",
            "SOL": "wss://stream.binance.com:9443/ws/solusdt@aggTrade",
        }

    @property
    def is_paper_mode(self) -> bool:
        return self.TRADING_MODE == "paper"

    def estimate_fee(self, num_contracts: int, price_cents: int) -> float:
        """Kalshi fee: flat rate per contract, capped so fee never exceeds potential profit."""
        raw_fee = self.KALSHI_FEE_PER_CONTRACT * num_contracts
        max_fee_yes = (price_cents / 100) * num_contracts
        max_fee_no = ((100 - price_cents) / 100) * num_contracts
        return min(raw_fee, max_fee_yes, max_fee_no)

    def estimate_fee_per_contract(self, price_cents: int) -> float:
        """Fee for a single contract — used by strategies to evaluate signals before sizing."""
        return min(self.KALSHI_FEE_PER_CONTRACT, price_cents / 100, (100 - price_cents) / 100)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/test_config.py
git commit -m "feat: replace Polymarket config with Kalshi env vars and fee formula"
```

---

### Task 2: Kalshi client — create core/kalshi_client.py

**Files:**
- Create: `core/kalshi_client.py`
- Create: `tests/test_kalshi_client.py`
- Delete: `core/polymarket_client.py`
- Delete: `tests/test_polymarket_client.py`

- [ ] **Step 1: Write failing tests for KalshiClient**

Create `tests/test_kalshi_client.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, mock_open
from core.kalshi_client import KalshiClient, CRYPTO_SERIES_PREFIXES


def _make_client():
    """Create a KalshiClient with mocked HTTP."""
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test_key"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    client._http = MagicMock()
    client._private_key = MagicMock()
    return client


def test_filter_crypto_markets():
    client = _make_client()
    markets = [
        {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
        {"ticker": "TRUMP-APPROVAL", "status": "open"},
        {"ticker": "KXETH-25MAR20-T4000", "status": "open"},
    ]
    filtered = client._filter_crypto_markets(markets)
    assert len(filtered) == 2
    assert filtered[0]["ticker"] == "KXBTC-25MAR20-T70000"
    assert filtered[1]["ticker"] == "KXETH-25MAR20-T4000"


def test_get_market_prices():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"market": {"yes_ask": 45, "no_ask": 48}}
    )
    yes_price, no_price = client.get_market_prices("KXBTC-25MAR20-T70000")
    assert yes_price == pytest.approx(0.45)
    assert no_price == pytest.approx(0.48)


def test_get_order_book_depth():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"orderbook": {
            "yes": [[45, 100], [44, 200]],
            "no": [[48, 150], [47, 50]],
        }}
    )
    yes_depth, no_depth = client.get_order_book_depth("KXBTC-25MAR20-T70000")
    # yes: (100*0.45) + (200*0.44) = 45+88 = 133
    assert yes_depth == pytest.approx(133.0, abs=1.0)
    # no: (150*0.48) + (50*0.47) = 72+23.5 = 95.5
    assert no_depth == pytest.approx(95.5, abs=1.0)


def test_preflight_check_success():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 500.0}
    )
    assert client.preflight_check() is True


def test_preflight_check_failure():
    client = _make_client()
    client._http.get.side_effect = Exception("Connection error")
    assert client.preflight_check() is False


def test_place_market_order():
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order": {"id": "ord_123", "status": "executed"}},
        raise_for_status=lambda: None,
    )
    resp = client.place_market_order("KXBTC-25MAR20-T70000", "yes", 10)
    assert resp["id"] == "ord_123"


def test_place_limit_order():
    client = _make_client()
    client._http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"order": {"id": "ord_456", "status": "resting"}},
        raise_for_status=lambda: None,
    )
    resp = client.place_limit_order("KXBTC-25MAR20-T70000", "yes", 48, 5)
    assert resp["id"] == "ord_456"


def test_cancel_order():
    client = _make_client()
    client._http.delete.return_value = MagicMock(
        status_code=200,
        json=lambda: {},
        raise_for_status=lambda: None,
    )
    assert client.cancel_order("ord_123") is True


def test_cancel_order_failure():
    client = _make_client()
    client._http.delete.side_effect = Exception("Not found")
    assert client.cancel_order("ord_123") is False


def test_get_balance():
    client = _make_client()
    client._http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 50000},  # 50000 cents = $500
        raise_for_status=lambda: None,
    )
    balance = client.get_balance()
    assert balance == pytest.approx(500.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_kalshi_client.py -v`
Expected: FAIL — `core.kalshi_client` does not exist

- [ ] **Step 3: Implement KalshiClient**

Create `core/kalshi_client.py`:

```python
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
        self._min_request_interval = 0.1  # 100ms — stays under 10 req/s

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

    # ── Public API ──

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
                "ticker": ticker,
                "action": "buy",
                "side": side,
                "count": count,
                "type": "market",
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
                "ticker": ticker,
                "action": "buy",
                "side": side,
                "count": count,
                "type": "limit",
                "yes_price": price_cents,
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
            return data.get("balance", 0) / 100  # cents → dollars
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_kalshi_client.py -v`
Expected: All PASS

- [ ] **Step 5: Delete old Polymarket client and tests**

```bash
git rm core/polymarket_client.py tests/test_polymarket_client.py
```

- [ ] **Step 6: Commit**

```bash
git add core/kalshi_client.py tests/test_kalshi_client.py
git commit -m "feat: add KalshiClient replacing PolymarketClient"
```

---

### Task 3: Market Scanner — switch from conditionId to ticker

**Files:**
- Modify: `core/market_scanner.py`
- Modify: `tests/test_market_scanner.py`

- [ ] **Step 1: Update tests to use ticker**

In `tests/test_market_scanner.py`, replace all `conditionId` references with `ticker`:

```python
import pytest
from core.market_scanner import MarketScanner


def test_cache_and_get_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    markets = [
        {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
    ]
    scanner.update_markets(markets)
    assert len(scanner.get_active_markets()) == 1
    assert scanner.get_market("KXBTC-25MAR20-T70000")["ticker"] == "KXBTC-25MAR20-T70000"


def test_capital_lock():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is False
    scanner.lock_market("KXBTC-25MAR20-T70000")
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is True
    scanner.unlock_market("KXBTC-25MAR20-T70000")
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is False


def test_get_unlocked_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {
        "KXBTC-25MAR20-T70000": {"ticker": "KXBTC-25MAR20-T70000"},
        "KXETH-25MAR20-T4000": {"ticker": "KXETH-25MAR20-T4000"},
    }
    scanner._locked_markets = {"KXBTC-25MAR20-T70000"}
    unlocked = scanner.get_unlocked_markets()
    assert len(unlocked) == 1
    assert unlocked[0]["ticker"] == "KXETH-25MAR20-T4000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_market_scanner.py -v`
Expected: FAIL — `update_markets` still keys by `conditionId`

- [ ] **Step 3: Update market_scanner.py**

```python
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("market_scanner")


class MarketScanner:
    def __init__(self, kalshi_client):
        self._client = kalshi_client
        self._markets: dict[str, dict] = {}
        self._locked_markets: set[str] = set()
        self._poll_interval = 5.0

    def update_markets(self, markets: list[dict]):
        self._markets = {m["ticker"]: m for m in markets}

    def get_active_markets(self) -> list[dict]:
        return list(self._markets.values())

    def get_unlocked_markets(self) -> list[dict]:
        return [m for ticker, m in self._markets.items() if ticker not in self._locked_markets]

    def get_market(self, ticker: str) -> Optional[dict]:
        return self._markets.get(ticker)

    def lock_market(self, ticker: str):
        self._locked_markets.add(ticker)
        logger.debug(f"Market locked: {ticker}")

    def unlock_market(self, ticker: str):
        self._locked_markets.discard(ticker)
        logger.debug(f"Market unlocked: {ticker}")

    def is_locked(self, ticker: str) -> bool:
        return ticker in self._locked_markets

    async def poll_loop(self):
        while True:
            try:
                markets = self._client.get_active_markets()
                self.update_markets(markets)
                logger.debug(f"Scanned {len(markets)} active crypto markets")
            except Exception as e:
                logger.error(f"Market scan failed: {e}")
            await asyncio.sleep(self._poll_interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_market_scanner.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/market_scanner.py tests/test_market_scanner.py
git commit -m "refactor: market scanner uses ticker instead of conditionId"
```

---

### Task 4: Order Manager — adapt to Kalshi client API

**Files:**
- Modify: `core/order_manager.py`
- Modify: `tests/test_order_manager.py`

- [ ] **Step 1: Rewrite test file with ticker params and new fee logic**

Replace `tests/test_order_manager.py` entirely:

```python
import pytest
import pytest_asyncio
from core.order_manager import OrderManager
from core.config import Config
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def paper_manager(db):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    return OrderManager(config, kalshi_client=None, database=db)


def test_initial_bankroll(paper_manager):
    assert paper_manager.bankroll == 1000.0


@pytest.mark.asyncio
async def test_paper_market_order(paper_manager):
    result = await paper_manager.market_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", size_usdc=100.0,
        price=0.50, strategy="sum_arb", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_filled"
    assert result["size_usdc"] == 100.0
    assert paper_manager.bankroll == 900.0


@pytest.mark.asyncio
async def test_paper_limit_order(paper_manager):
    result = await paper_manager.limit_order(
        ticker="KXBTC-25MAR20-T70000", side="BUY", price=0.48,
        size_usdc=50.0, strategy="maker_preorder", market_id="KXBTC-25MAR20-T70000",
    )
    assert result["status"] == "paper_open"
    assert result["price"] == 0.48


@pytest.mark.asyncio
async def test_position_sizing(paper_manager):
    size = paper_manager.calculate_position_size(net_edge=0.05)
    assert size == 12.5  # min(1000*0.25*0.05, 1000*0.10)


@pytest.mark.asyncio
async def test_circuit_breaker(paper_manager):
    paper_manager._daily_loss = 201.0
    assert paper_manager.is_circuit_breaker_active() is True
    paper_manager._daily_loss = 50.0
    assert paper_manager.is_circuit_breaker_active() is False


@pytest.mark.asyncio
async def test_consecutive_loss_tracking(paper_manager):
    paper_manager.record_loss()
    paper_manager.record_loss()
    assert paper_manager._consecutive_losses == 2
    paper_manager.record_win()
    assert paper_manager._consecutive_losses == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_order_manager.py -v`
Expected: FAIL — `market_order()` doesn't accept `ticker` param

- [ ] **Step 3: Update order_manager.py**

Key changes:
- Rename `token_id` → `ticker` in method signatures
- In `_live_market_order`: `count = int(size_usdc / price)`, call `self._client.place_market_order(ticker, side, count)`, extract `resp.get("id", "unknown")`
- In `_live_limit_order`: call `self._client.place_limit_order(ticker, side, int(price * 100), count)`
- Fee: `self.config.estimate_fee(count, int(price * 100))` in live mode, `self.config.estimate_fee(1, int(price * 100)) * int(size_usdc / price)` in paper mode
- Constructor param: `kalshi_client` instead of `polymarket_client`

```python
import logging
import time
import uuid

logger = logging.getLogger("order_manager")


class OrderManager:
    def __init__(self, config, kalshi_client, database):
        self.config = config
        self._client = kalshi_client
        self._db = database
        self.bankroll = config.INITIAL_BANKROLL
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._pause_until = 0.0

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

    async def market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        if self.config.is_paper_mode:
            return await self._paper_market_order(ticker, side, size_usdc, price, strategy, market_id)
        return await self._live_market_order(ticker, side, size_usdc, price, strategy, market_id)

    async def limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        if self.config.is_paper_mode:
            return await self._paper_limit_order(ticker, side, price, size_usdc, strategy, market_id)
        return await self._live_limit_order(ticker, side, price, size_usdc, strategy, market_id)

    async def cancel_order(self, order_id):
        if self.config.is_paper_mode:
            await self._db.update_order_status(order_id, "cancelled")
            return True
        return self._client.cancel_order(order_id)

    async def _paper_market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.estimate_fee(count, int(price * 100))
        self.bankroll -= size_usdc
        await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="paper_filled",
        )
        logger.info(f"[PAPER] Market {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_filled", "size_usdc": size_usdc, "price": price}

    async def _paper_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="paper_open",
        )
        logger.info(f"[PAPER] Limit {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f}")
        return {"order_id": order_id, "status": "paper_open", "price": price, "size_usdc": size_usdc}

    async def _live_market_order(self, ticker, side, size_usdc, price, strategy, market_id):
        count = max(1, int(size_usdc / price))
        resp = self._client.place_market_order(ticker, side, count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        fee = self.config.estimate_fee(count, int(price * 100))
        await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="filled",
        )
        self.bankroll -= size_usdc
        return {"order_id": order_id, "status": "filled", "size_usdc": size_usdc, "price": price}

    async def _live_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        count = max(1, int(size_usdc / price))
        resp = self._client.place_limit_order(ticker, side, int(price * 100), count)
        if resp is None:
            return {"status": "failed"}
        order_id = resp.get("id", "unknown")
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="open",
        )
        return {"order_id": order_id, "status": "open", "price": price, "size_usdc": size_usdc}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_order_manager.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/order_manager.py tests/test_order_manager.py
git commit -m "refactor: order manager adapted to Kalshi client API"
```

---

### Task 5: Sum Arb Strategy — adapt to Kalshi market structure

**Files:**
- Modify: `strategies/sum_arb.py`
- Modify: `tests/test_sum_arb.py`

- [ ] **Step 1: Update sum arb tests for new fee formula**

The `evaluate()` method now uses `config.estimate_fee_per_contract(price_cents)` instead of `config.estimate_taker_fee(price)`. Update tests:

```python
import pytest
from strategies.sum_arb import SumArbStrategy
from core.config import Config


def test_detect_opportunity():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    # yes=0.45, no=0.48 → gap=0.07
    # fee per contract at 45c = min(0.07, 0.45, 0.55) = 0.07
    # fee per contract at 48c = min(0.07, 0.48, 0.52) = 0.07
    # net_edge = 0.07 - 0.07 - 0.07 = -0.07 → no signal with standard fee
    # Use wider gap for test:
    signal = strategy.evaluate(yes_price=0.40, no_price=0.40, yes_depth=100.0, no_depth=100.0)
    assert signal is not None
    assert signal["gap"] == pytest.approx(0.20, abs=0.001)
    assert signal["net_edge"] > 0


def test_no_opportunity_when_sum_too_high():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.50, no_price=0.49, yes_depth=100.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_low_liquidity():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    signal = strategy.evaluate(yes_price=0.40, no_price=0.40, yes_depth=30.0, no_depth=100.0)
    assert signal is None


def test_no_opportunity_when_fees_eat_edge():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg
    # gap=0.02, fees=0.14 → net_edge negative
    signal = strategy.evaluate(yes_price=0.49, no_price=0.49, yes_depth=100.0, no_depth=100.0)
    assert signal is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sum_arb.py -v`

- [ ] **Step 3: Update sum_arb.py**

```python
import logging
from typing import Optional

logger = logging.getLogger("sum_arb")

MIN_DEPTH_USDC = 50.0


class SumArbStrategy:
    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    def evaluate(self, yes_price, no_price, yes_depth, no_depth) -> Optional[dict]:
        token_sum = yes_price + no_price
        gap = 1.0 - token_sum
        if gap < self.config.SUM_ARB_MIN_GAP:
            return None
        if yes_depth < MIN_DEPTH_USDC or no_depth < MIN_DEPTH_USDC:
            return None
        fee_yes = self.config.estimate_fee_per_contract(int(yes_price * 100))
        fee_no = self.config.estimate_fee_per_contract(int(no_price * 100))
        total_fee = fee_yes + fee_no
        net_edge = gap - total_fee
        if net_edge <= 0:
            return None
        return {
            "yes_price": yes_price, "no_price": no_price,
            "gap": gap, "total_fee": total_fee, "net_edge": net_edge,
        }

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active():
            return
        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                await self._check_market(market)
            except Exception as e:
                logger.error(f"Sum arb error on {market.get('ticker')}: {e}")

    async def _check_market(self, market):
        ticker = market["ticker"]
        yes_price, no_price = self._client.get_market_prices(ticker)
        if yes_price is None or no_price is None:
            return
        yes_depth, no_depth = self._client.get_order_book_depth(ticker)
        signal = self.evaluate(yes_price, no_price, yes_depth, no_depth)
        if signal is None:
            return
        size_usdc = self._order_manager.calculate_position_size(signal["net_edge"])
        if size_usdc < 1.0:
            return
        self._scanner.lock_market(ticker)
        logger.info(
            f"[SUM ARB] {ticker}: YES={signal['yes_price']:.3f} NO={signal['no_price']:.3f} "
            f"gap={signal['gap']:.4f} net_edge={signal['net_edge']:.4f} size=${size_usdc:.2f}"
        )
        yes_half = size_usdc / 2
        no_half = size_usdc / 2
        await self._order_manager.market_order(
            ticker=ticker, side="yes", size_usdc=yes_half,
            price=yes_price, strategy="sum_arb", market_id=ticker,
        )
        await self._order_manager.market_order(
            ticker=ticker, side="no", size_usdc=no_half,
            price=no_price, strategy="sum_arb", market_id=ticker,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sum_arb.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/sum_arb.py tests/test_sum_arb.py
git commit -m "refactor: sum arb strategy adapted to Kalshi market structure"
```

---

### Task 6: Maker Pre-Order Strategy — time-based filtering

**Files:**
- Modify: `strategies/maker_preorder.py`
- Modify: `tests/test_maker_preorder.py`

- [ ] **Step 1: Update tests for time-based filtering**

```python
import pytest
import time
from datetime import datetime, timezone, timedelta
from strategies.maker_preorder import MakerPreorderStrategy
from core.config import Config


def test_calculate_order_prices():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    yes_price, no_price = strategy.calculate_order_prices()
    assert yes_price + no_price == pytest.approx(0.96)
    assert yes_price == pytest.approx(0.48)
    assert no_price == pytest.approx(0.48)


def test_expected_profit():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 0.96
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    assert profit == pytest.approx(0.04)


def test_is_short_duration_market():
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    # Market closing in 5 minutes → should match
    close_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time}) is True

    # Market closing in 30 minutes → should NOT match
    close_time_far = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time_far}) is False

    # Market already closed → should NOT match
    close_time_past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert strategy._is_short_duration_market({"close_time": close_time_past}) is False


def test_should_not_trade_if_profit_negative():
    cfg = Config()
    cfg.MAKER_TARGET_SUM = 1.01
    strategy = MakerPreorderStrategy.__new__(MakerPreorderStrategy)
    strategy.config = cfg
    profit = strategy.expected_profit_per_dollar()
    assert profit < 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_maker_preorder.py -v`
Expected: FAIL — `_is_short_duration_market` doesn't exist

- [ ] **Step 3: Update maker_preorder.py**

```python
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("maker_preorder")

# Markets closing within this window are eligible
MIN_CLOSE_MINUTES = 2
MAX_CLOSE_MINUTES = 10


class MakerPreorderStrategy:
    """Maker Pre-Order: place limit orders on both sides of short-duration markets with sum < $1.00."""

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._active_orders: dict[str, dict] = {}  # ticker -> {yes_order, no_order, placed_at}

    def calculate_order_prices(self) -> tuple[float, float]:
        """Split MAKER_TARGET_SUM evenly between YES and NO."""
        half = self.config.MAKER_TARGET_SUM / 2
        return round(half, 4), round(half, 4)

    def expected_profit_per_dollar(self) -> float:
        """Expected profit per $1 deployed if both sides fill."""
        return 1.0 - self.config.MAKER_TARGET_SUM

    def _is_short_duration_market(self, market: dict) -> bool:
        """Check if market closes within MIN_CLOSE_MINUTES to MAX_CLOSE_MINUTES."""
        close_time_str = market.get("close_time")
        if not close_time_str:
            return False
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_until_close = (close_time - now).total_seconds() / 60
            return MIN_CLOSE_MINUTES <= minutes_until_close <= MAX_CLOSE_MINUTES
        except (ValueError, TypeError):
            return False

    async def scan_and_execute(self):
        """Scan for short-duration markets, place orders, monitor fills."""
        if self._order_manager.is_circuit_breaker_active():
            return

        await self._monitor_active_orders()

        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                if self._is_short_duration_market(market):
                    await self._place_orders(market)
            except Exception as e:
                logger.error(f"Maker pre-order error on {market.get('ticker')}: {e}")

    async def _place_orders(self, market: dict):
        ticker = market["ticker"]

        if ticker in self._active_orders:
            return

        if self.expected_profit_per_dollar() <= 0:
            return

        yes_price, no_price = self.calculate_order_prices()
        size_usdc = self._order_manager.calculate_position_size(self.expected_profit_per_dollar())
        if size_usdc < 1.0:
            return

        half_size = size_usdc / 2

        self._scanner.lock_market(ticker)

        logger.info(
            f"[MAKER] Placing orders on {ticker}: "
            f"YES@{yes_price:.3f} NO@{no_price:.3f} size=${size_usdc:.2f}"
        )

        yes_result = await self._order_manager.limit_order(
            ticker=ticker, side="yes", price=yes_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=ticker,
        )

        no_result = await self._order_manager.limit_order(
            ticker=ticker, side="no", price=no_price,
            size_usdc=half_size, strategy="maker_preorder", market_id=ticker,
        )

        self._active_orders[ticker] = {
            "yes_order_id": yes_result.get("order_id"),
            "no_order_id": no_result.get("order_id"),
            "yes_status": yes_result.get("status"),
            "no_status": no_result.get("status"),
            "placed_at": time.time(),
            "size_usdc": half_size,
        }

    async def _monitor_active_orders(self):
        """Check active orders and manage risk for one-sided fills."""
        to_remove = []
        for ticker, info in self._active_orders.items():
            if info["yes_status"].startswith("paper") and info["no_status"].startswith("paper"):
                logger.info(f"[MAKER] Both sides filled (paper) on {ticker}")
                to_remove.append(ticker)
                continue

            elapsed = time.time() - info["placed_at"]
            if elapsed > 240:  # 4 min — cancel with 60s remaining
                logger.warning(f"[MAKER] Auto-cancelling unfilled orders on {ticker}")
                await self._order_manager.cancel_order(info["yes_order_id"])
                await self._order_manager.cancel_order(info["no_order_id"])
                self._scanner.unlock_market(ticker)
                to_remove.append(ticker)

        for t in to_remove:
            del self._active_orders[t]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_maker_preorder.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/maker_preorder.py tests/test_maker_preorder.py
git commit -m "refactor: maker pre-order uses time-based filtering for Kalshi"
```

---

### Task 7: Main orchestrator — import swap and preflight

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Update test_main.py**

Replace `PolymarketClient` mock with `KalshiClient`:

```python
import pytest
from unittest.mock import MagicMock, patch


def test_bot_initializes():
    with patch("main.Config") as MockConfig, \
         patch("main.Database") as MockDB, \
         patch("main.PriceFeed") as MockFeed, \
         patch("main.KalshiClient") as MockClient, \
         patch("main.MarketScanner") as MockScanner, \
         patch("main.OrderManager") as MockOM, \
         patch("main.SumArbStrategy") as MockSumArb, \
         patch("main.MakerPreorderStrategy") as MockMaker, \
         patch("main.TradeLogger") as MockLogger, \
         patch("main.TelegramNotifier") as MockNotifier, \
         patch("main.create_app") as MockCreateApp:

        cfg = MockConfig.return_value
        cfg.is_paper_mode = True
        cfg.TRADING_MODE = "paper"
        cfg.BINANCE_WS_URLS = {"BTC": "wss://fake"}
        cfg.TELEGRAM_BOT_TOKEN = ""
        cfg.TELEGRAM_CHAT_ID = ""

        from main import Bot
        b = Bot()
        assert b.config.is_paper_mode is True
        assert b.sum_arb is not None
        assert b.maker_preorder is not None
        assert b.notifier is not None
```

- [ ] **Step 2: Update main.py**

Replace `PolymarketClient` import and usage with `KalshiClient`:

```python
# Line 9: change import
from core.kalshi_client import KalshiClient

# Line 26: change instantiation
self.kalshi_client = KalshiClient(self.config)
self.scanner = MarketScanner(self.kalshi_client)
self.order_manager = OrderManager(self.config, self.kalshi_client, self.db)
self.sum_arb = SumArbStrategy(
    self.config, self.order_manager, self.scanner, self.kalshi_client
)
self.maker_preorder = MakerPreorderStrategy(
    self.config, self.order_manager, self.scanner, self.kalshi_client
)

# Line 51: change preflight
if not self.kalshi_client.preflight_check():
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "refactor: main orchestrator uses KalshiClient"
```

---

### Task 8: Integration tests — FakeKalshiClient

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Rewrite integration tests with FakeKalshiClient**

```python
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from core.config import Config
from core.database import Database
from core.order_manager import OrderManager
from core.market_scanner import MarketScanner
from strategies.sum_arb import SumArbStrategy
from strategies.maker_preorder import MakerPreorderStrategy


class FakeKalshiClient:
    def __init__(self):
        self.markets = [
            {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
        ]
        self._prices = {"KXBTC-25MAR20-T70000": (0.40, 0.40)}  # (yes, no) — wide gap
        self._depths = {"KXBTC-25MAR20-T70000": (200.0, 150.0)}

    def get_active_markets(self):
        return self.markets

    def get_market_prices(self, ticker):
        return self._prices.get(ticker, (None, None))

    def get_order_book_depth(self, ticker):
        return self._depths.get(ticker, (0.0, 0.0))


@pytest_asyncio.fixture
async def setup(tmp_path):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    config.SUM_ARB_MIN_GAP = 0.03
    config.KALSHI_FEE_PER_CONTRACT = 0.07

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    client = FakeKalshiClient()
    scanner = MarketScanner(client)
    scanner.update_markets(client.get_active_markets())

    order_mgr = OrderManager(config, client, db)
    sum_arb = SumArbStrategy(config, order_mgr, scanner, client)
    maker = MakerPreorderStrategy(config, order_mgr, scanner, client)

    return {"config": config, "db": db, "client": client, "scanner": scanner, "order_mgr": order_mgr, "sum_arb": sum_arb, "maker": maker}


@pytest.mark.asyncio
async def test_full_sum_arb_paper_flow(setup):
    s = setup
    await s["sum_arb"].scan_and_execute()
    trades = await s["db"].get_trades()
    assert len(trades) == 2
    assert s["scanner"].is_locked("KXBTC-25MAR20-T70000")
    assert s["order_mgr"].bankroll < 1000.0


@pytest.mark.asyncio
async def test_no_trade_when_no_opportunity(setup):
    s = setup
    s["client"]._prices = {"KXBTC-25MAR20-T70000": (0.50, 0.49)}
    await s["sum_arb"].scan_and_execute()
    trades = await s["db"].get_trades()
    assert len(trades) == 0
    assert s["order_mgr"].bankroll == 1000.0


@pytest.mark.asyncio
async def test_maker_preorder_places_orders_on_short_market(setup):
    """Maker Pre-Order should place limit orders on markets closing soon."""
    s = setup
    close_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    s["client"].markets.append({
        "ticker": "KXBTC-25MAR20-T71000",
        "status": "open",
        "close_time": close_time,
    })
    s["scanner"].update_markets(s["client"].get_active_markets())

    await s["maker"].scan_and_execute()

    assert "KXBTC-25MAR20-T71000" in s["maker"]._active_orders
    assert s["scanner"].is_locked("KXBTC-25MAR20-T71000")


@pytest.mark.asyncio
async def test_maker_preorder_ignores_far_market(setup):
    """Maker Pre-Order should NOT place orders on markets closing far in the future."""
    s = setup
    # Default market has no close_time → should be ignored
    await s["maker"].scan_and_execute()
    assert len(s["maker"]._active_orders) == 0
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest tests/test_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: update integration tests for Kalshi client"
```

---

### Task 9: Preflight tests — Kalshi version

**Files:**
- Modify: `tests/test_preflight.py`

- [ ] **Step 1: Rewrite preflight tests for KalshiClient**

```python
import pytest
from unittest.mock import MagicMock, patch
from core.kalshi_client import KalshiClient


def test_preflight_passes_with_valid_connection():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    mock_http = MagicMock()
    # get_balance call
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"balance": 50000},  # $500 in cents
        raise_for_status=lambda: None,
    )
    client._http = mock_http
    client._private_key = MagicMock()
    assert client.preflight_check() is True


def test_preflight_fails_without_http():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = ""
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    client._http = None
    client._init_http = lambda: None
    assert client.preflight_check() is False


def test_preflight_fails_on_api_error():
    config = MagicMock()
    config.KALSHI_API_KEY_ID = "test"
    config.KALSHI_RSA_PRIVATE_KEY_PATH = ""
    config.KALSHI_API_BASE_URL = "https://trading-api.kalshi.com"
    client = KalshiClient(config)
    mock_http = MagicMock()
    mock_http.get.side_effect = Exception("API error")
    client._http = mock_http
    client._private_key = MagicMock()
    assert client.preflight_check() is False
```

- [ ] **Step 2: Run preflight tests**

Run: `python -m pytest tests/test_preflight.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_preflight.py
git commit -m "test: update preflight tests for Kalshi"
```

---

### Task 10: Dashboard, .env, requirements, docs — update references

**Files:**
- Modify: `monitoring/dashboard/api.py`
- Modify: `monitoring/dashboard/static/index.html`
- Modify: `.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Update dashboard title**

In `monitoring/dashboard/api.py` line 9:
```python
app = FastAPI(title="Kalshi Arb Bot Dashboard")
```

In `monitoring/dashboard/static/index.html` line 6:
```html
<title>Kalshi Arb Bot — Dashboard</title>
```

- [ ] **Step 2: Update .env.example**

```
# Kalshi API
KALSHI_API_KEY_ID=
KALSHI_RSA_PRIVATE_KEY_PATH=
KALSHI_API_BASE_URL=https://trading-api.kalshi.com
KALSHI_FEE_PER_CONTRACT=0.07

# Trading
TRADING_MODE=paper
INITIAL_BANKROLL=100

# Strategy Parameters
SUM_ARB_MIN_GAP=0.03
MAKER_TARGET_SUM=0.96
KELLY_FRACTION=0.25
MAX_SINGLE_TRADE_PCT=0.10
MAX_DIRECTIONAL_LOSS=50
MAX_DAILY_LOSS=200
MAX_CONSECUTIVE_LOSSES=5

# Monitoring
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 3: Update requirements.txt**

```
cryptography>=41.0.0
websockets>=12.0
python-dotenv>=1.0.0
httpx>=0.27.0
aiosqlite>=0.20.0
fastapi>=0.104.0
uvicorn>=0.24.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add monitoring/dashboard/api.py monitoring/dashboard/static/index.html .env.example requirements.txt
git commit -m "chore: update dashboard, env, and dependencies for Kalshi"
```

---

### Task 11: Update documentation

**Files:**
- Modify: `handover.md`
- Modify: `docs/setup-guide.md`

- [ ] **Step 1: Rewrite handover.md**

Replace all Polymarket references with Kalshi equivalents:
- "Polymarket" → "Kalshi" throughout
- CLOB references → REST API v2
- token_id → ticker
- py-clob-client → cryptography + httpx
- conditionId → ticker
- proxy wallet → Kalshi account
- Polygon chain → removed (no blockchain)
- Update architecture tree: `polymarket_client.py` → `kalshi_client.py`
- Update config reference table with new env vars

- [ ] **Step 2: Rewrite docs/setup-guide.md for Kalshi**

Replace Polymarket account setup with Kalshi:
- Create Kalshi account at kalshi.com
- Generate API key in Account → API Keys
- Download RSA private key PEM file
- Set `KALSHI_API_KEY_ID` and `KALSHI_RSA_PRIVATE_KEY_PATH` in `.env`
- Deposit funds via bank transfer (no crypto/wallet needed)
- Remove all Polygon/wallet/proxy wallet references

- [ ] **Step 3: Commit**

```bash
git add handover.md docs/setup-guide.md
git commit -m "docs: update handover and setup guide for Kalshi migration"
```

---

### Task 12: Final verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (should be ~53 tests, same coverage)

- [ ] **Step 2: Verify no Polymarket references remain**

Run: `grep -ri "polymarket\|poly_api\|clob_endpoint\|polygon_chain\|py.clob.client\|conditionId" --include="*.py" --include="*.html" --include="*.txt" --include="*.md" .`
Expected: Only matches in `docs/superpowers/` (spec/plan docs) and git history references

- [ ] **Step 3: Verify imports work**

Run: `python -c "from core.kalshi_client import KalshiClient; from core.config import Config; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore: final cleanup for Polymarket to Kalshi migration"
```
