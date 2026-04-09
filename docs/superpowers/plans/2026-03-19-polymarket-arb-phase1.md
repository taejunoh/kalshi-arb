# Polymarket Arbitrage Bot — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build core infrastructure + Yes/No Sum Arb strategy in paper trading mode with basic logging.

**Architecture:** Modular Python async bot. Shared core layer (config, price feed, polymarket client, market scanner, order manager) with pluggable strategy modules. Paper mode simulates fills without real orders. SQLite for state persistence.

**Tech Stack:** Python 3.11+, asyncio, websockets, py-clob-client, aiosqlite, python-dotenv, pytest, pytest-asyncio

---

## File Map

| File | Responsibility |
|---|---|
| `core/config.py` | Load .env, define all constants and thresholds |
| `core/database.py` | SQLite schema, CRUD for trades/positions/state |
| `core/price_feed.py` | Binance WebSocket price stream with reconnection |
| `core/polymarket_client.py` | Wrap py-clob-client for market/order/position ops |
| `core/market_scanner.py` | Poll active markets, filter, cache, capital lock |
| `core/order_manager.py` | Paper/live order execution, bankroll tracking |
| `strategies/sum_arb.py` | Yes/No Sum Arb signal detection + execution |
| `monitoring/logger.py` | Console + file + CSV trade logging |
| `main.py` | Async orchestration, shutdown handler, circuit breaker |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template env vars |
| `.gitignore` | Ignore .env, venv, data/, logs/, __pycache__ |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `core/__init__.py`
- Create: `strategies/__init__.py`
- Create: `monitoring/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
py-clob-client>=0.1.0
websockets>=12.0
python-dotenv>=1.0.0
httpx>=0.27.0
aiosqlite>=0.20.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create .env.example**

```
# Polymarket
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=
POLY_PRIVATE_KEY=

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

- [ ] **Step 3: Create .gitignore**

```
.env
venv/
__pycache__/
*.pyc
data/
logs/
.pytest_cache/
```

- [ ] **Step 4: Create __init__.py files for all packages**

Empty files: `core/__init__.py`, `strategies/__init__.py`, `monitoring/__init__.py`, `tests/__init__.py`

- [ ] **Step 5: Install dependencies and verify**

```bash
cd /Users/taejunoh/Desktop/LFG/polymarket-arb
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding with dependencies and config templates"
```

---

### Task 2: Config Module

**Files:**
- Create: `core/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
import pytest


def test_config_loads_defaults():
    """Config should provide default values when env vars are not set."""
    # Clear any existing env vars
    for key in ["TRADING_MODE", "INITIAL_BANKROLL", "SUM_ARB_MIN_GAP"]:
        os.environ.pop(key, None)

    from core.config import Config
    cfg = Config()

    assert cfg.TRADING_MODE == "paper"
    assert cfg.INITIAL_BANKROLL == 100.0
    assert cfg.SUM_ARB_MIN_GAP == 0.03
    assert cfg.MAKER_TARGET_SUM == 0.96
    assert cfg.KELLY_FRACTION == 0.25
    assert cfg.MAX_SINGLE_TRADE_PCT == 0.10
    assert cfg.MAX_DIRECTIONAL_LOSS == 50.0
    assert cfg.MAX_DAILY_LOSS == 200.0
    assert cfg.MAX_CONSECUTIVE_LOSSES == 5


def test_config_is_paper_mode():
    cfg = Config()
    assert cfg.is_paper_mode is True


def test_config_env_override():
    os.environ["TRADING_MODE"] = "live"
    os.environ["INITIAL_BANKROLL"] = "500"
    from core.config import Config
    cfg = Config()
    assert cfg.TRADING_MODE == "live"
    assert cfg.INITIAL_BANKROLL == 500.0
    assert cfg.is_paper_mode is False
    # Cleanup
    os.environ.pop("TRADING_MODE", None)
    os.environ.pop("INITIAL_BANKROLL", None)


def test_dynamic_fee_calculation():
    from core.config import Config
    cfg = Config()
    # At 50¢ — max fee ~3.15%
    fee_50 = cfg.estimate_taker_fee(0.50)
    assert abs(fee_50 - 0.0315) < 0.001
    # At 0¢ or 100¢ — fee ~0
    fee_0 = cfg.estimate_taker_fee(0.0)
    assert fee_0 < 0.001
    fee_100 = cfg.estimate_taker_fee(1.0)
    assert fee_100 < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.config'`

- [ ] **Step 3: Write implementation**

```python
# core/config.py
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        # Polymarket API
        self.POLY_API_KEY = os.getenv("POLY_API_KEY", "")
        self.POLY_API_SECRET = os.getenv("POLY_API_SECRET", "")
        self.POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")
        self.POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")

        # Trading mode
        self.TRADING_MODE = os.getenv("TRADING_MODE", "paper")
        self.INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))

        # Strategy parameters
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

        # Polymarket
        self.CLOB_ENDPOINT = "https://clob.polymarket.com"
        self.POLYGON_CHAIN_ID = 137

    @property
    def is_paper_mode(self) -> bool:
        return self.TRADING_MODE == "paper"

    def estimate_taker_fee(self, price: float) -> float:
        """
        Dynamic taker fee for 15-min crypto markets.
        ~3.15% at 50¢, tapering to 0 near 0¢ and 100¢.
        """
        proximity_to_50 = 1 - abs(price - 0.5) * 2
        return 0.0315 * max(proximity_to_50, 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/test_config.py
git commit -m "feat: config module with env loading and fee calculation"
```

---

### Task 3: Database Module

**Files:**
- Create: `core/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py
import pytest
import pytest_asyncio
import aiosqlite
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_initialize_creates_tables(db):
    """Database should create trades and orders tables on init."""
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert "trades" in tables
    assert "orders" in tables


@pytest.mark.asyncio
async def test_record_trade(db):
    await db.record_trade(
        strategy="sum_arb",
        market_id="market_123",
        direction="YES",
        price=0.48,
        size=100.0,
        fee=1.51,
        status="filled",
    )
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["strategy"] == "sum_arb"
    assert trades[0]["price"] == 0.48


@pytest.mark.asyncio
async def test_record_order(db):
    await db.record_order(
        order_id="order_abc",
        market_id="market_123",
        strategy="maker_preorder",
        side="BUY",
        price=0.47,
        size=50.0,
        order_type="limit",
        status="open",
    )
    orders = await db.get_open_orders()
    assert len(orders) == 1
    assert orders[0]["order_id"] == "order_abc"


@pytest.mark.asyncio
async def test_update_order_status(db):
    await db.record_order(
        order_id="order_abc",
        market_id="market_123",
        strategy="maker_preorder",
        side="BUY",
        price=0.47,
        size=50.0,
        order_type="limit",
        status="open",
    )
    await db.update_order_status("order_abc", "filled")
    orders = await db.get_open_orders()
    assert len(orders) == 0


@pytest.mark.asyncio
async def test_get_daily_pnl(db):
    await db.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled", pnl=2.0,
    )
    await db.record_trade(
        strategy="sum_arb", market_id="m2", direction="NO",
        price=0.49, size=50.0, fee=0.75, status="filled", pnl=-1.0,
    )
    daily_pnl = await db.get_daily_pnl()
    assert daily_pnl == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.database'`

- [ ] **Step 3: Write implementation**

```python
# core/database.py
import aiosqlite
from datetime import datetime, timezone
from typing import Optional


class Database:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    pnl REAL DEFAULT 0,
                    status TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    order_id TEXT NOT NULL UNIQUE,
                    market_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            await conn.commit()

    async def close(self):
        pass  # aiosqlite connections are opened per-operation

    async def record_trade(
        self,
        strategy: str,
        market_id: str,
        direction: str,
        price: float,
        size: float,
        fee: float,
        status: str,
        pnl: float = 0.0,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO trades
                   (timestamp, strategy, market_id, direction, price, size, fee, pnl, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, strategy, market_id, direction, price, size, fee, pnl, status),
            )
            await conn.commit()

    async def record_order(
        self,
        order_id: str,
        market_id: str,
        strategy: str,
        side: str,
        price: float,
        size: float,
        order_type: str,
        status: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO orders
                   (timestamp, order_id, market_id, strategy, side, price, size, order_type, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, order_id, market_id, strategy, side, price, size, order_type, status),
            )
            await conn.commit()

    async def update_order_status(self, order_id: str, status: str):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE orders SET status = ? WHERE order_id = ?",
                (status, order_id),
            )
            await conn.commit()

    async def get_trades(self, strategy: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            if strategy:
                cursor = await conn.execute(
                    "SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp DESC",
                    (strategy,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM trades ORDER BY timestamp DESC"
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_open_orders(self):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM orders WHERE status = 'open'"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_daily_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?",
                (f"{today}%",),
            )
            row = await cursor.fetchone()
            return row[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_database.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/database.py tests/test_database.py
git commit -m "feat: SQLite database module for trades and orders"
```

---

### Task 4: Logger Module

**Files:**
- Create: `monitoring/logger.py`
- Create: `tests/test_logger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logger.py
import os
import csv
import pytest
from monitoring.logger import setup_logger, TradeLogger


def test_setup_logger_returns_logger(tmp_path):
    logger = setup_logger("test_bot", log_dir=str(tmp_path))
    assert logger.name == "test_bot"
    # Should have at least console + file handlers
    assert len(logger.handlers) >= 2


def test_trade_logger_writes_csv(tmp_path):
    tl = TradeLogger(log_dir=str(tmp_path))
    tl.log_trade(
        strategy="sum_arb",
        market_id="market_123",
        direction="YES",
        price=0.48,
        size=100.0,
        fee=1.51,
        pnl=2.0,
        status="filled",
    )
    csv_path = tmp_path / "trades.csv"
    assert csv_path.exists()
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["strategy"] == "sum_arb"
    assert rows[0]["price"] == "0.48"


def test_trade_logger_appends(tmp_path):
    tl = TradeLogger(log_dir=str(tmp_path))
    tl.log_trade("sum_arb", "m1", "YES", 0.48, 100, 1.5, 2.0, "filled")
    tl.log_trade("sum_arb", "m2", "NO", 0.49, 50, 0.75, -1.0, "filled")
    csv_path = tmp_path / "trades.csv"
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitoring.logger'`

- [ ] **Step 3: Write implementation**

```python
# monitoring/logger.py
import csv
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler


def setup_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(console)

    # File handler — daily rotation
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        utc=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


class TradeLogger:
    FIELDS = [
        "timestamp", "strategy", "market_id", "direction",
        "price", "size", "fee", "pnl", "status",
    ]

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.csv_path = os.path.join(log_dir, "trades.csv")
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()

    def log_trade(
        self,
        strategy: str,
        market_id: str,
        direction: str,
        price: float,
        size: float,
        fee: float,
        pnl: float,
        status: str,
    ):
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            "market_id": market_id,
            "direction": direction,
            "price": price,
            "size": size,
            "fee": fee,
            "pnl": pnl,
            "status": status,
        }
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow(row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logger.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add monitoring/logger.py tests/test_logger.py
git commit -m "feat: logger with daily rotation and CSV trade log"
```

---

### Task 5: Price Feed Module

**Files:**
- Create: `core/price_feed.py`
- Create: `tests/test_price_feed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_price_feed.py
import asyncio
import json
import time
import pytest
from core.price_feed import PriceFeed


@pytest.mark.asyncio
async def test_process_binance_message():
    """PriceFeed should update price from a Binance aggTrade message."""
    feed = PriceFeed()
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    assert feed.get_price("BTC") == 97400.50


@pytest.mark.asyncio
async def test_price_feed_staleness():
    """Price should be considered stale if no update for > 5 seconds."""
    feed = PriceFeed()
    # No prices set — should be stale
    assert feed.is_stale("BTC") is True
    # Set a price
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    assert feed.is_stale("BTC") is False


@pytest.mark.asyncio
async def test_candle_open_tracking():
    """Should track 15-min candle open prices."""
    feed = PriceFeed()
    msg = json.dumps({"s": "BTCUSDT", "p": "97400.50", "T": int(time.time() * 1000)})
    feed._process_message("BTC", msg)
    # First price becomes candle open if none set
    assert feed.get_candle_open("BTC") == 97400.50


@pytest.mark.asyncio
async def test_get_price_returns_none_for_unknown():
    feed = PriceFeed()
    assert feed.get_price("XRP") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_price_feed.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# core/price_feed.py
import asyncio
import json
import time
import logging
from typing import Optional

import websockets

logger = logging.getLogger("price_feed")


class PriceFeed:
    STALE_THRESHOLD = 5.0  # seconds

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._last_update: dict[str, float] = {}
        self._candle_opens: dict[str, float] = {}
        self._candle_window_start: dict[str, int] = {}

    def get_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def get_candle_open(self, symbol: str) -> Optional[float]:
        return self._candle_opens.get(symbol)

    def is_stale(self, symbol: str) -> bool:
        last = self._last_update.get(symbol)
        if last is None:
            return True
        return (time.time() - last) > self.STALE_THRESHOLD

    def _process_message(self, symbol: str, raw_msg: str):
        data = json.loads(raw_msg)
        price = float(data["p"])
        self._prices[symbol] = price
        self._last_update[symbol] = time.time()

        # Track 15-min candle opens (UTC-aligned)
        current_window = self._get_15min_window()
        prev_window = self._candle_window_start.get(symbol)
        if prev_window is None or current_window != prev_window:
            self._candle_opens[symbol] = price
            self._candle_window_start[symbol] = current_window
            logger.debug(f"{symbol} new candle open: {price} (window {current_window})")

    def _get_15min_window(self) -> int:
        """Returns the start timestamp of the current 15-min window."""
        now = int(time.time())
        return now - (now % 900)  # 900 seconds = 15 minutes

    async def subscribe(self, symbol: str, ws_url: str):
        """Connect to Binance WebSocket and stream prices. Auto-reconnects."""
        backoff = 1
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    logger.info(f"Connected to {symbol} price feed")
                    backoff = 1  # reset on successful connect
                    async for msg in ws:
                        self._process_message(symbol, msg)
            except (
                websockets.ConnectionClosed,
                ConnectionRefusedError,
                OSError,
            ) as e:
                logger.warning(f"{symbol} feed disconnected: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as e:
                logger.error(f"{symbol} feed unexpected error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_price_feed.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/price_feed.py tests/test_price_feed.py
git commit -m "feat: Binance WebSocket price feed with reconnection and candle tracking"
```

---

### Task 6: Polymarket Client Module

**Files:**
- Create: `core/polymarket_client.py`
- Create: `tests/test_polymarket_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_polymarket_client.py
import pytest
from unittest.mock import MagicMock, patch
from core.polymarket_client import PolymarketClient


def test_filter_crypto_markets():
    """Should filter markets to only crypto Up/Down 15min/5min."""
    client = PolymarketClient.__new__(PolymarketClient)
    markets = [
        {"question": "Will BTC go up in the next 15 minutes?", "conditionId": "1",
         "tokens": [{"outcome": "Yes", "token_id": "t1"}, {"outcome": "No", "token_id": "t2"}]},
        {"question": "Will Trump win?", "conditionId": "2",
         "tokens": [{"outcome": "Yes", "token_id": "t3"}, {"outcome": "No", "token_id": "t4"}]},
        {"question": "Will ETH go up in the next 5 minutes?", "conditionId": "3",
         "tokens": [{"outcome": "Yes", "token_id": "t5"}, {"outcome": "No", "token_id": "t6"}]},
    ]
    filtered = client._filter_crypto_markets(markets)
    assert len(filtered) == 2
    assert filtered[0]["conditionId"] == "1"
    assert filtered[1]["conditionId"] == "3"


def test_extract_asset():
    client = PolymarketClient.__new__(PolymarketClient)
    assert client._extract_asset({"question": "Will BTC go up?"}) == "BTC"
    assert client._extract_asset({"question": "Will ETH go down?"}) == "ETH"
    assert client._extract_asset({"question": "Will SOL go up?"}) == "SOL"
    assert client._extract_asset({"question": "Will Trump win?"}) is None


def test_get_token_ids():
    client = PolymarketClient.__new__(PolymarketClient)
    market = {
        "tokens": [
            {"outcome": "Yes", "token_id": "yes_token"},
            {"outcome": "No", "token_id": "no_token"},
        ]
    }
    yes_id, no_id = client.get_token_ids(market)
    assert yes_id == "yes_token"
    assert no_id == "no_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_polymarket_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# core/polymarket_client.py
import logging
import time
from typing import Optional

logger = logging.getLogger("polymarket_client")

# Keywords for filtering crypto Up/Down markets
CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XRP"}
TIME_KEYWORDS = {"15 minute", "5 minute", "15-minute", "5-minute", "15min", "5min"}


class PolymarketClient:
    """Wraps py-clob-client for Polymarket CLOB operations."""

    def __init__(self, config):
        self.config = config
        self._clob = None
        self._last_request_time = 0.0
        self._min_request_interval = 0.1  # 10 req/s max

    def _init_clob(self):
        """Lazy-init the CLOB client (requires py-clob-client)."""
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
                self.config.CLOB_ENDPOINT,
                key=self.config.POLY_PRIVATE_KEY,
                creds=creds,
                chain_id=self.config.POLYGON_CHAIN_ID,
            )
            logger.info("CLOB client initialized")
        except ImportError:
            logger.warning("py-clob-client not available — running in offline mode")
        except Exception as e:
            logger.error(f"Failed to init CLOB client: {e}")

    def _rate_limit(self):
        """Simple rate limiter."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def get_active_markets(self) -> list[dict]:
        """Fetch active crypto Up/Down markets."""
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
        """Filter to only crypto Up/Down markets with time-based resolution."""
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
        """Return (yes_token_id, no_token_id) for a market."""
        tokens = market.get("tokens", [])
        yes_id = None
        no_id = None
        for t in tokens:
            outcome = t.get("outcome", "").lower()
            if outcome in ("yes", "up"):
                yes_id = t["token_id"]
            elif outcome in ("no", "down"):
                no_id = t["token_id"]
        return yes_id, no_id

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """Get last trade price for a token."""
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
        """Get total available depth (in USDC) for a token. Returns 0 on error."""
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

    def place_market_order(self, token_id: str, size: float, side: str = "BUY") -> Optional[dict]:
        """Place a market order. Returns order response or None."""
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            order_args = OrderArgs(
                token_id=token_id,
                price=0.99,  # high price for market-like fill
                size=size,
                side=side,
            )
            signed = self._clob.create_order(order_args)
            resp = self._clob.post_order(signed, OrderType.FOK)
            logger.info(f"Market order placed: {side} {size} of {token_id}")
            return resp
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            return None

    def place_limit_order(self, token_id: str, price: float, size: float, side: str = "BUY") -> Optional[dict]:
        """Place a limit order. Returns order response or None."""
        self._init_clob()
        if self._clob is None:
            return None
        self._rate_limit()
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            )
            signed = self._clob.create_order(order_args)
            resp = self._clob.post_order(signed, OrderType.GTC)
            logger.info(f"Limit order placed: {side} {size}@{price} of {token_id}")
            return resp
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""
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

    def get_positions(self) -> list[dict]:
        """Get all current positions."""
        self._init_clob()
        if self._clob is None:
            return []
        self._rate_limit()
        try:
            return self._clob.get_positions()
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_polymarket_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/polymarket_client.py tests/test_polymarket_client.py
git commit -m "feat: Polymarket CLOB client with rate limiting and market filtering"
```

---

### Task 7: Market Scanner Module

**Files:**
- Create: `core/market_scanner.py`
- Create: `tests/test_market_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_scanner.py
import pytest
import time
from core.market_scanner import MarketScanner


def test_cache_and_get_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()

    markets = [
        {"conditionId": "m1", "question": "BTC up 15 min?", "endDate": "2026-03-19T12:15:00Z",
         "tokens": [{"outcome": "Yes", "token_id": "t1"}, {"outcome": "No", "token_id": "t2"}]},
    ]
    scanner.update_markets(markets)
    assert len(scanner.get_active_markets()) == 1
    assert scanner.get_market("m1")["conditionId"] == "m1"


def test_capital_lock():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()

    assert scanner.is_locked("m1") is False
    scanner.lock_market("m1")
    assert scanner.is_locked("m1") is True
    scanner.unlock_market("m1")
    assert scanner.is_locked("m1") is False


def test_get_unlocked_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {
        "m1": {"conditionId": "m1"},
        "m2": {"conditionId": "m2"},
    }
    scanner._locked_markets = {"m1"}

    unlocked = scanner.get_unlocked_markets()
    assert len(unlocked) == 1
    assert unlocked[0]["conditionId"] == "m2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_market_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# core/market_scanner.py
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("market_scanner")


class MarketScanner:
    """Polls and caches active Polymarket crypto markets. Manages capital locks."""

    def __init__(self, polymarket_client):
        self._client = polymarket_client
        self._markets: dict[str, dict] = {}
        self._locked_markets: set[str] = set()
        self._poll_interval = 5.0  # seconds

    def update_markets(self, markets: list[dict]):
        """Update the cached market list."""
        self._markets = {m["conditionId"]: m for m in markets}

    def get_active_markets(self) -> list[dict]:
        return list(self._markets.values())

    def get_unlocked_markets(self) -> list[dict]:
        """Return markets that don't have an active capital lock."""
        return [
            m for cid, m in self._markets.items()
            if cid not in self._locked_markets
        ]

    def get_market(self, condition_id: str) -> Optional[dict]:
        return self._markets.get(condition_id)

    def lock_market(self, condition_id: str):
        self._locked_markets.add(condition_id)
        logger.debug(f"Market locked: {condition_id}")

    def unlock_market(self, condition_id: str):
        self._locked_markets.discard(condition_id)
        logger.debug(f"Market unlocked: {condition_id}")

    def is_locked(self, condition_id: str) -> bool:
        return condition_id in self._locked_markets

    async def poll_loop(self):
        """Continuously poll for active markets."""
        while True:
            try:
                markets = self._client.get_active_markets()
                self.update_markets(markets)
                logger.debug(f"Scanned {len(markets)} active crypto markets")
            except Exception as e:
                logger.error(f"Market scan failed: {e}")
            await asyncio.sleep(self._poll_interval)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_market_scanner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/market_scanner.py tests/test_market_scanner.py
git commit -m "feat: market scanner with caching and capital lock"
```

---

### Task 8: Order Manager Module

**Files:**
- Create: `core/order_manager.py`
- Create: `tests/test_order_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_manager.py
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
    return OrderManager(config, polymarket_client=None, database=db)


def test_initial_bankroll(paper_manager):
    assert paper_manager.bankroll == 1000.0


@pytest.mark.asyncio
async def test_paper_market_order(paper_manager):
    """Paper mode should simulate fill without hitting API."""
    result = await paper_manager.market_order(
        token_id="test_token",
        side="BUY",
        size_usdc=100.0,
        price=0.50,
        strategy="sum_arb",
        market_id="m1",
    )
    assert result["status"] == "paper_filled"
    assert result["size_usdc"] == 100.0
    # Bankroll should decrease
    assert paper_manager.bankroll == 900.0


@pytest.mark.asyncio
async def test_paper_limit_order(paper_manager):
    result = await paper_manager.limit_order(
        token_id="test_token",
        side="BUY",
        price=0.48,
        size_usdc=50.0,
        strategy="maker_preorder",
        market_id="m1",
    )
    assert result["status"] == "paper_open"
    assert result["price"] == 0.48


@pytest.mark.asyncio
async def test_position_sizing(paper_manager):
    """Position size should respect Kelly and max trade pct."""
    size = paper_manager.calculate_position_size(net_edge=0.05)
    # quarter-Kelly: 1000 * 0.25 * 0.05 = 12.5
    # max 10% bankroll: 1000 * 0.10 = 100
    # min(12.5, 100) = 12.5
    assert size == 12.5


@pytest.mark.asyncio
async def test_circuit_breaker(paper_manager):
    """Should halt trading when daily loss exceeds threshold."""
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

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_order_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# core/order_manager.py
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger("order_manager")


class OrderManager:
    """Handles order execution in paper or live mode. Tracks bankroll and circuit breaker."""

    def __init__(self, config, polymarket_client, database):
        self.config = config
        self._client = polymarket_client
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
            # Reset after pause period
            self._consecutive_losses = 0
        return False

    def record_loss(self, amount: float = 0.0):
        self._consecutive_losses += 1
        self._daily_loss += amount
        if self._consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
            self._pause_until = time.time() + 900  # 15 min pause
            logger.warning(
                f"Circuit breaker: {self._consecutive_losses} consecutive losses. "
                f"Pausing until {self._pause_until}"
            )

    def record_win(self, amount: float = 0.0):
        self._consecutive_losses = 0

    def reset_daily(self):
        self._daily_loss = 0.0

    async def market_order(
        self,
        token_id: str,
        side: str,
        size_usdc: float,
        price: float,
        strategy: str,
        market_id: str,
    ) -> dict:
        if self.config.is_paper_mode:
            return await self._paper_market_order(
                token_id, side, size_usdc, price, strategy, market_id
            )
        return await self._live_market_order(
            token_id, side, size_usdc, price, strategy, market_id
        )

    async def limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size_usdc: float,
        strategy: str,
        market_id: str,
    ) -> dict:
        if self.config.is_paper_mode:
            return await self._paper_limit_order(
                token_id, side, price, size_usdc, strategy, market_id
            )
        return await self._live_limit_order(
            token_id, side, price, size_usdc, strategy, market_id
        )

    async def cancel_order(self, order_id: str) -> bool:
        if self.config.is_paper_mode:
            await self._db.update_order_status(order_id, "cancelled")
            return True
        return self._client.cancel_order(order_id)

    # ── Paper mode ──────────────────────────────────────────────

    async def _paper_market_order(
        self, token_id, side, size_usdc, price, strategy, market_id
    ) -> dict:
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        self.bankroll -= size_usdc

        await self._db.record_trade(
            strategy=strategy,
            market_id=market_id,
            direction=side,
            price=price,
            size=size_usdc,
            fee=self.config.estimate_taker_fee(price) * size_usdc,
            status="paper_filled",
        )

        logger.info(
            f"[PAPER] Market {side} | {strategy} | "
            f"${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}"
        )
        return {
            "order_id": order_id,
            "status": "paper_filled",
            "size_usdc": size_usdc,
            "price": price,
        }

    async def _paper_limit_order(
        self, token_id, side, price, size_usdc, strategy, market_id
    ) -> dict:
        order_id = f"paper_{uuid.uuid4().hex[:8]}"

        await self._db.record_order(
            order_id=order_id,
            market_id=market_id,
            strategy=strategy,
            side=side,
            price=price,
            size=size_usdc,
            order_type="limit",
            status="paper_open",
        )

        logger.info(
            f"[PAPER] Limit {side} | {strategy} | "
            f"${size_usdc:.2f} @ {price:.3f}"
        )
        return {
            "order_id": order_id,
            "status": "paper_open",
            "price": price,
            "size_usdc": size_usdc,
        }

    # ── Live mode ───────────────────────────────────────────────

    async def _live_market_order(
        self, token_id, side, size_usdc, price, strategy, market_id
    ) -> dict:
        size_tokens = size_usdc / price
        resp = self._client.place_market_order(token_id, size_tokens, side)
        if resp is None:
            return {"status": "failed"}

        order_id = resp.get("orderID", "unknown")
        await self._db.record_trade(
            strategy=strategy,
            market_id=market_id,
            direction=side,
            price=price,
            size=size_usdc,
            fee=self.config.estimate_taker_fee(price) * size_usdc,
            status="filled",
        )
        self.bankroll -= size_usdc
        return {"order_id": order_id, "status": "filled", "size_usdc": size_usdc, "price": price}

    async def _live_limit_order(
        self, token_id, side, price, size_usdc, strategy, market_id
    ) -> dict:
        size_tokens = size_usdc / price
        resp = self._client.place_limit_order(token_id, price, size_tokens, side)
        if resp is None:
            return {"status": "failed"}

        order_id = resp.get("orderID", "unknown")
        await self._db.record_order(
            order_id=order_id,
            market_id=market_id,
            strategy=strategy,
            side=side,
            price=price,
            size=size_usdc,
            order_type="limit",
            status="open",
        )
        return {"order_id": order_id, "status": "open", "price": price, "size_usdc": size_usdc}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_order_manager.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add core/order_manager.py tests/test_order_manager.py
git commit -m "feat: order manager with paper/live modes and circuit breaker"
```

---

### Task 9: Sum Arb Strategy

**Files:**
- Create: `strategies/sum_arb.py`
- Create: `tests/test_sum_arb.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sum_arb.py
import pytest
from strategies.sum_arb import SumArbStrategy
from core.config import Config


@pytest.fixture
def config():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    return cfg


def test_detect_opportunity_yes():
    """Should detect arb when YES+NO sum is below threshold."""
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg

    signal = strategy.evaluate(
        yes_price=0.45,
        no_price=0.48,
        yes_depth=100.0,
        no_depth=100.0,
    )
    # sum = 0.93, gap = 0.07 > 0.03 → should signal
    assert signal is not None
    assert signal["gap"] == pytest.approx(0.07, abs=0.001)
    assert signal["net_edge"] > 0


def test_no_opportunity_when_sum_too_high():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg

    signal = strategy.evaluate(
        yes_price=0.50,
        no_price=0.49,
        yes_depth=100.0,
        no_depth=100.0,
    )
    # sum = 0.99, gap = 0.01 < 0.03 → no signal
    assert signal is None


def test_no_opportunity_when_low_liquidity():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg

    signal = strategy.evaluate(
        yes_price=0.45,
        no_price=0.48,
        yes_depth=30.0,  # below $50 threshold
        no_depth=100.0,
    )
    assert signal is None


def test_no_opportunity_when_fees_eat_edge():
    cfg = Config()
    cfg.SUM_ARB_MIN_GAP = 0.03
    strategy = SumArbStrategy.__new__(SumArbStrategy)
    strategy.config = cfg

    # Prices near 50¢ have highest fees (~3.15% each side)
    # sum = 0.97, gap = 0.03, but fees ~0.031 + 0.031 = 0.062 > 0.03
    signal = strategy.evaluate(
        yes_price=0.49,
        no_price=0.48,
        yes_depth=100.0,
        no_depth=100.0,
    )
    assert signal is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sum_arb.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# strategies/sum_arb.py
import logging
from typing import Optional

logger = logging.getLogger("sum_arb")

MIN_DEPTH_USDC = 50.0  # minimum order book depth per side


class SumArbStrategy:
    """Yes/No Sum Arbitrage: buy both sides when YES + NO < $1.00 minus fees."""

    def __init__(self, config, order_manager, market_scanner, polymarket_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = polymarket_client

    def evaluate(
        self,
        yes_price: float,
        no_price: float,
        yes_depth: float,
        no_depth: float,
    ) -> Optional[dict]:
        """
        Check if a sum arb opportunity exists.
        Returns signal dict or None.
        """
        token_sum = yes_price + no_price
        gap = 1.0 - token_sum

        if gap < self.config.SUM_ARB_MIN_GAP:
            return None

        if yes_depth < MIN_DEPTH_USDC or no_depth < MIN_DEPTH_USDC:
            return None

        # Calculate fees for both sides
        fee_yes = self.config.estimate_taker_fee(yes_price)
        fee_no = self.config.estimate_taker_fee(no_price)
        total_fee = fee_yes + fee_no

        net_edge = gap - total_fee
        if net_edge <= 0:
            return None

        return {
            "yes_price": yes_price,
            "no_price": no_price,
            "gap": gap,
            "total_fee": total_fee,
            "net_edge": net_edge,
        }

    async def scan_and_execute(self):
        """Scan all unlocked markets for sum arb opportunities."""
        if self._order_manager.is_circuit_breaker_active():
            return

        markets = self._scanner.get_unlocked_markets()
        for market in markets:
            try:
                await self._check_market(market)
            except Exception as e:
                logger.error(f"Sum arb error on {market.get('conditionId')}: {e}")

    async def _check_market(self, market: dict):
        condition_id = market["conditionId"]
        yes_token, no_token = self._client.get_token_ids(market)
        if not yes_token or not no_token:
            return

        yes_price = self._client.get_last_trade_price(yes_token)
        no_price = self._client.get_last_trade_price(no_token)
        if yes_price is None or no_price is None:
            return

        yes_depth = self._client.get_order_book_depth(yes_token)
        no_depth = self._client.get_order_book_depth(no_token)

        signal = self.evaluate(yes_price, no_price, yes_depth, no_depth)
        if signal is None:
            return

        # Calculate position size
        size_usdc = self._order_manager.calculate_position_size(signal["net_edge"])
        if size_usdc < 1.0:
            return

        # Lock market to prevent double-deploy
        self._scanner.lock_market(condition_id)

        logger.info(
            f"[SUM ARB] Opportunity on {condition_id}: "
            f"YES={signal['yes_price']:.3f} NO={signal['no_price']:.3f} "
            f"gap={signal['gap']:.4f} net_edge={signal['net_edge']:.4f} "
            f"size=${size_usdc:.2f}"
        )

        # Execute both sides
        yes_half = size_usdc / 2
        no_half = size_usdc / 2

        yes_result = await self._order_manager.market_order(
            token_id=yes_token,
            side="BUY",
            size_usdc=yes_half,
            price=yes_price,
            strategy="sum_arb",
            market_id=condition_id,
        )

        no_result = await self._order_manager.market_order(
            token_id=no_token,
            side="BUY",
            size_usdc=no_half,
            price=no_price,
            strategy="sum_arb",
            market_id=condition_id,
        )

        # Check for partial fill
        if yes_result["status"] == "failed" or no_result["status"] == "failed":
            logger.warning(f"[SUM ARB] Partial fill on {condition_id} — risk management needed")
            # TODO: implement stop-loss for partial fill in Phase 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sum_arb.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/sum_arb.py tests/test_sum_arb.py
git commit -m "feat: Yes/No Sum Arb strategy with signal detection and execution"
```

---

### Task 10: Main Orchestrator

**Files:**
- Create: `main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from main import Bot


@pytest.fixture
def bot():
    with patch("main.Config") as MockConfig, \
         patch("main.Database") as MockDB, \
         patch("main.PriceFeed") as MockFeed, \
         patch("main.PolymarketClient") as MockClient, \
         patch("main.MarketScanner") as MockScanner, \
         patch("main.OrderManager") as MockOM, \
         patch("main.SumArbStrategy") as MockSumArb, \
         patch("main.TradeLogger") as MockLogger:

        cfg = MockConfig.return_value
        cfg.is_paper_mode = True
        cfg.TRADING_MODE = "paper"
        cfg.BINANCE_WS_URLS = {"BTC": "wss://fake"}

        b = Bot()
        yield b


def test_bot_initializes_in_paper_mode(bot):
    assert bot.config.is_paper_mode is True


def test_bot_has_strategies(bot):
    assert bot.sum_arb is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# main.py
import asyncio
import logging
import signal
import sys

from core.config import Config
from core.database import Database
from core.price_feed import PriceFeed
from core.polymarket_client import PolymarketClient
from core.market_scanner import MarketScanner
from core.order_manager import OrderManager
from strategies.sum_arb import SumArbStrategy
from monitoring.logger import setup_logger, TradeLogger

logger = logging.getLogger("bot")


class Bot:
    def __init__(self):
        self.config = Config()
        self.db = Database()
        self.price_feed = PriceFeed()
        self.polymarket_client = PolymarketClient(self.config)
        self.scanner = MarketScanner(self.polymarket_client)
        self.order_manager = OrderManager(self.config, self.polymarket_client, self.db)
        self.sum_arb = SumArbStrategy(
            self.config, self.order_manager, self.scanner, self.polymarket_client
        )
        self.trade_logger = TradeLogger()
        self._running = True

    async def start(self):
        setup_logger("bot")
        await self.db.initialize()

        mode = "PAPER" if self.config.is_paper_mode else "LIVE"
        logger.info(f"Bot starting in {mode} mode | Bankroll: ${self.order_manager.bankroll:.2f}")

        # Register shutdown handler
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start background tasks
        tasks = [
            asyncio.create_task(self._price_feed_task()),
            asyncio.create_task(self.scanner.poll_loop()),
            asyncio.create_task(self._strategy_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")

    async def _price_feed_task(self):
        """Start all price feed subscriptions."""
        tasks = []
        for symbol, url in self.config.BINANCE_WS_URLS.items():
            tasks.append(asyncio.create_task(self.price_feed.subscribe(symbol, url)))
        await asyncio.gather(*tasks)

    async def _strategy_loop(self):
        """Main strategy loop — runs every 200ms."""
        while self._running:
            try:
                await self.sum_arb.scan_and_execute()
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")
            await asyncio.sleep(0.2)

    async def shutdown(self):
        """Graceful shutdown: cancel orders, log state, exit."""
        logger.info("Shutting down...")
        self._running = False

        # Cancel open orders
        open_orders = await self.db.get_open_orders()
        for order in open_orders:
            await self.order_manager.cancel_order(order["order_id"])
            logger.info(f"Cancelled order: {order['order_id']}")

        # Log final state
        daily_pnl = await self.db.get_daily_pnl()
        logger.info(f"Final bankroll: ${self.order_manager.bankroll:.2f} | Daily PnL: ${daily_pnl:.2f}")

        logger.info("Shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    bot = Bot()
    asyncio.run(bot.start())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_main.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: main orchestrator with async loop, shutdown handler, and strategy dispatch"
```

---

### Task 11: Integration Test & Final Verification

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
import pytest
import pytest_asyncio
from core.config import Config
from core.database import Database
from core.order_manager import OrderManager
from core.market_scanner import MarketScanner
from strategies.sum_arb import SumArbStrategy


class FakePolymarketClient:
    """Fake client for integration testing."""

    def __init__(self):
        self.markets = [
            {
                "conditionId": "test_market_1",
                "question": "Will BTC go up in the next 15 minutes?",
                "tokens": [
                    {"outcome": "Yes", "token_id": "yes_1"},
                    {"outcome": "No", "token_id": "no_1"},
                ],
            }
        ]
        self._prices = {"yes_1": 0.45, "no_1": 0.48}
        self._depths = {"yes_1": 200.0, "no_1": 150.0}

    def get_active_markets(self):
        return self.markets

    def get_token_ids(self, market):
        tokens = market.get("tokens", [])
        yes_id = no_id = None
        for t in tokens:
            if t["outcome"].lower() in ("yes", "up"):
                yes_id = t["token_id"]
            elif t["outcome"].lower() in ("no", "down"):
                no_id = t["token_id"]
        return yes_id, no_id

    def get_last_trade_price(self, token_id):
        return self._prices.get(token_id)

    def get_order_book_depth(self, token_id):
        return self._depths.get(token_id, 0)


@pytest_asyncio.fixture
async def setup(tmp_path):
    config = Config()
    config.TRADING_MODE = "paper"
    config.INITIAL_BANKROLL = 1000.0
    config.SUM_ARB_MIN_GAP = 0.03

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    client = FakePolymarketClient()
    scanner = MarketScanner(client)
    scanner.update_markets(client.get_active_markets())

    order_mgr = OrderManager(config, client, db)
    sum_arb = SumArbStrategy(config, order_mgr, scanner, client)

    return {
        "config": config,
        "db": db,
        "client": client,
        "scanner": scanner,
        "order_mgr": order_mgr,
        "sum_arb": sum_arb,
    }


@pytest.mark.asyncio
async def test_full_sum_arb_paper_flow(setup):
    """End-to-end: detect opportunity → paper execute → verify state."""
    s = setup

    # Market has YES=0.45, NO=0.48, sum=0.93, gap=0.07
    await s["sum_arb"].scan_and_execute()

    # Should have executed trades
    trades = await s["db"].get_trades()
    assert len(trades) == 2  # YES + NO

    # Market should be locked
    assert s["scanner"].is_locked("test_market_1")

    # Bankroll should have decreased
    assert s["order_mgr"].bankroll < 1000.0


@pytest.mark.asyncio
async def test_no_trade_when_no_opportunity(setup):
    """Should not trade when prices are too close to $1.00."""
    s = setup
    s["client"]._prices = {"yes_1": 0.50, "no_1": 0.49}

    await s["sum_arb"].scan_and_execute()

    trades = await s["db"].get_trades()
    assert len(trades) == 0
    assert s["order_mgr"].bankroll == 1000.0
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest tests/test_integration.py -v`
Expected: 2 passed

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test for sum arb paper trading flow"
```

---

### Task 12: Setup Guide & Final Documentation

**Files:**
- Create: `docs/setup-guide.md`

- [ ] **Step 1: Write setup guide**

```markdown
# Polymarket Arb Bot — Setup Guide

## Prerequisites
- Python 3.11+
- A Polymarket account with API access
- USDC on Polygon network (for live trading)

## 1. Polymarket Account Setup

1. Go to https://polymarket.com and create an account
2. Navigate to Settings → API Keys
3. Generate an API key — save the key, secret, and passphrase
4. Your private key is your wallet's private key (the one used to sign transactions)

## 2. Proxy Wallet

Polymarket uses proxy wallets on Polygon. After creating your account:
1. Deposit USDC via the Polymarket bridge
2. Your proxy wallet address is visible in your account settings
3. This is the wallet that will execute trades

## 3. Installation

```bash
cd polymarket-arb
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Configuration

```bash
cp .env.example .env
# Edit .env with your API credentials
```

## 5. Running (Paper Mode)

```bash
python main.py
```

The bot starts in paper mode by default. It will:
- Connect to Binance price feeds
- Scan Polymarket for crypto markets
- Detect sum arb opportunities
- Log simulated trades (no real orders)

## 6. Switching to Live Mode

After validating in paper mode (48+ hours recommended):
1. Edit `.env`: change `TRADING_MODE=paper` to `TRADING_MODE=live`
2. Ensure USDC is funded in your Polymarket proxy wallet
3. Restart the bot

## 7. Monitoring

- Console output: real-time trade logs
- File logs: `logs/bot.log` (daily rotation)
- Trade history: `logs/trades.csv`
- Database: `data/bot.db` (SQLite)
```

- [ ] **Step 2: Commit**

```bash
git add docs/setup-guide.md
git commit -m "docs: setup guide for Polymarket account, installation, and running"
```

- [ ] **Step 3: Final commit — verify all files present**

```bash
git status
git log --oneline
```

Expected: clean working tree, all tasks committed.
