# Polymarket Arbitrage Bot — Design Spec

**Date:** 2026-03-19
**Status:** Approved

---

## 1. Overview

Polymarket의 15분/5분 크립토 Up/Down 마켓에서 두 가지 차익거래 전략을 자동 실행하는 봇.

### Strategies
1. **Yes/No Sum Arbitrage** — YES + NO 토큰 합계가 $1.00 미만일 때 양쪽 동시 매수하여 해상 시 무위험 수익
2. **Maker Pre-Order Arbitrage** — 5분 마켓에 양쪽 리밋 오더를 합계 < $1.00으로 배치하여 양쪽 체결 시 무위험 수익

### Key Decisions
- **Language:** Python (프로토타입) → Rust (프로덕션 포팅)
- **Architecture:** 전략별 분리 모듈 + 공유 인프라 레이어
- **Operation:** 완전 자동 (시그널 감지 → 즉시 실행)
- **Launch:** 페이퍼 트레이딩 모드 먼저 → 검증 후 라이브 전환
- **Monitoring:** 풀스택 (로그 + 텔레그램 알림 + 웹 대시보드)

---

## 2. Project Structure

```
polymarket-arb/
├── core/
│   ├── polymarket_client.py   # Polymarket CLOB API wrapper
│   ├── price_feed.py          # Binance/Coinbase WebSocket price feeds
│   ├── market_scanner.py      # Active 15min/5min market scanning
│   ├── order_manager.py       # Order execution + paper/live mode
│   └── config.py              # Environment vars, constants
│
├── strategies/
│   ├── sum_arb.py             # Yes/No Sum Arbitrage
│   └── maker_preorder.py      # Maker Pre-Order Arbitrage
│
├── monitoring/
│   ├── logger.py              # File/console logging
│   ├── notifier.py            # Telegram notifications
│   └── dashboard/             # Web dashboard (후순위)
│
├── data/                      # SQLite DB + persistent state
├── logs/                      # Log files + trades.csv
├── tests/
├── main.py                    # Entry point — strategy orchestration
├── requirements.txt
├── .env.example
└── docs/
    └── setup-guide.md         # Polymarket account/API setup guide
```

---

## 3. Core Infrastructure

### 3.1 Price Feed (`price_feed.py`)
- Binance WebSocket (`aggTrade` stream) for BTC, ETH, SOL real-time prices
- Coinbase WebSocket as backup/cross-validation
- Tracks 15-minute candle open prices (UTC-aligned: 0, 15, 30, 45 min)
- Distributes price data to strategy modules via asyncio Queue
- **Reconnection:** Auto-reconnect on disconnect with exponential backoff (1s, 2s, 4s, max 30s). Strategies pause when price feed is stale (no update > 5s)
- **Role in arb strategies:** External prices are used for market context and future latency arb extension. Sum Arb and Maker Pre-Order primarily use Polymarket token prices.

### 3.2 Polymarket Client (`polymarket_client.py`)
- Wraps `py-clob-client` library
- Active 15min/5min crypto market queries
- YES/NO token price retrieval
- Order book depth queries
- Order create/cancel/query
- Position query and redemption

### 3.3 Market Scanner (`market_scanner.py`)
- Polls active markets every 5 seconds
- Filters BTC/ETH/SOL markets
- Tracks remaining time, token IDs, current prices per market
- Caches market metadata to reduce API calls
- **API rate limiting:** Respects Polymarket rate limits. Implements retry with backoff on 429 responses. Caps total API calls to safe threshold (~10 req/s)
- **Capital lock:** Maintains a set of market IDs with active positions to prevent Sum Arb and Maker Pre-Order from double-deploying on the same market

### 3.4 Order Manager (`order_manager.py`)
- **Paper mode:** Logs signals and simulated fills. Tracks virtual balance/PnL.
- **Live mode:** Executes real CLOB orders.
- Mode switch via `TRADING_MODE=paper|live` env var
- Bankroll management: tracks balance, computes position sizes

### 3.5 Config (`config.py`)
- Loads API keys, secrets, private key from `.env`
- Defines strategy parameter constants (thresholds, Kelly fraction, etc.)

---

## 4. Strategy Logic

### 4.1 Yes/No Sum Arbitrage (`sum_arb.py`)

**Principle:** When YES + NO < $1.00, buy both sides. At resolution, one side pays $1.00. Difference minus fees = profit.

**Target markets:** Both 15-minute and 5-minute crypto Up/Down markets (any timeframe where YES+NO sum dips below threshold).

**Entry conditions:**
- `YES_price + NO_price < 1.00 - SUM_ARB_MIN_GAP` (derived from config, default 3¢ gap → threshold 0.97)
- Minimum $50 depth on both sides of the order book
- Net profit positive after dynamic fee deduction: `1.00 - (YES + NO) - (fee_YES + fee_NO) > 0`
- Dynamic taker fee estimate: ~3.15% at 50¢ odds, tapering to 0 near 0¢/100¢ (formula: `0.0315 * (1 - abs(price - 0.5) * 2)`)
- Position size: `min(bankroll * KELLY_FRACTION * net_edge, bankroll * MAX_SINGLE_TRADE_PCT)`

**Execution flow:**
1. Simultaneous YES/NO price query
2. Net profit calculation including fees
3. Simultaneous market orders on both sides (speed critical — gap closes fast)
4. Fill confirmation → wait for resolution → auto-redeem

**Risk management:**
- If only one side fills, attempt to sell the filled side at market (stop-loss). If sell also fails (no liquidity), hold to resolution and accept directional risk. Log as a warning.
- Maximum single trade size capped at bankroll percentage
- Markets resolving as "Invalid" — positions are returned at cost basis; no action needed beyond logging

### 4.2 Maker Pre-Order Arbitrage (`maker_preorder.py`)

**Principle:** Place limit orders on both UP and DOWN sides of 5-minute markets with combined price < $1.00. If both fill, guaranteed profit regardless of outcome.

**Entry conditions:**
- Place orders 5–15 seconds after market opens (wait for initial order book to form)
- Target prices: combined total at `MAKER_TARGET_SUM` (default 0.96), split evenly (e.g., UP 0.48, DOWN 0.48)
- Maker rebate adds ~0.5–1¢ per fill on top

**Execution flow:**
1. Detect next 5-minute market
2. Place limit orders on both UP/DOWN sides (sum < $1.00)
3. Monitor fill status
4. **Both filled** → wait for resolution → redeem (profit locked)
5. **One side only** → risk management: wait, then cancel unfilled side; liquidate filled side at market or hold to resolution

**Risk management:**
- Maximum directional loss limit (`MAX_DIRECTIONAL_LOSS`)
- Auto-cancel unfilled orders when < 60 seconds remaining

---

## 5. Monitoring & Dashboard

### 5.1 Logger (`logger.py`)
- Python `logging` module
- Console output + daily rotation file logs (`logs/YYYY-MM-DD.log`)
- Log levels: signal detection (DEBUG), order execution (INFO), errors (ERROR)
- Separate CSV trade log (`logs/trades.csv`) for analysis

### 5.2 Notifier (`notifier.py`)
- Telegram Bot API
- Alert triggers:
  - Trade execution (direction, price, size, expected profit)
  - Errors/exceptions
  - Daily PnL summary (UTC 00:00)
  - Bot start/stop
- Rate limiting: suppress duplicate alert types within 60 seconds

### 5.3 Dashboard (`monitoring/dashboard/`) — Phase 2
- FastAPI backend + simple frontend (React or vanilla HTML/JS)
- Screens:
  - Real-time positions and PnL
  - Trade history (filter/search)
  - Per-strategy performance (Sum Arb vs Maker Pre-Order)
  - Current signals/market state
- Data sources: `trades.csv` + real-time bot state

---

## 6. Execution Flow & Orchestration

### 6.1 Main Loop (`main.py`)

```
Startup → Load .env → Init client → Start price feeds
                          │
                          ▼
                  ┌─── Main Async Loop ───┐
                  │                       │
                  │  1. Market Scanner     │
                  │     update markets     │
                  │                       │
                  │  2. Sum Arb Strategy   │
                  │     check YES+NO gaps  │
                  │     execute if found   │
                  │                       │
                  │  3. Maker Pre-Order    │
                  │     detect 5min mkts   │
                  │     place/monitor LOs  │
                  │                       │
                  │  4. Position Mgmt      │
                  │     redeem resolved    │
                  │     manage one-sided   │
                  │                       │
                  │  5. Monitoring         │
                  │     log + notify       │
                  │                       │
                  │  Sleep 200ms → repeat  │
                  └───────────────────────┘
```

### 6.2 Concurrency Model
- `asyncio`-based
- Price feed: separate task (persistent WebSocket connection)
- Market scanner: separate task (5s polling)
- Strategy loop: main task (200ms interval)
- Each strategy isolated with try/except — Sum Arb errors don't affect Maker Pre-Order

### 6.3 Paper → Live Transition
- Change `.env`: `TRADING_MODE=paper` → `TRADING_MODE=live`
- Recommended minimum 48 hours of paper trading before going live
- **Live mode pre-flight checks:** On startup in live mode, verify API connectivity, verify USDC balance on Polymarket, verify credentials have trading permissions. Fail fast if any check fails.

### 6.4 Graceful Shutdown
- SIGTERM/SIGINT handler: cancel all open orders, log outstanding positions, flush trade log, send Telegram notification
- On restart: query Polymarket for existing positions and open orders to recover state

### 6.5 Circuit Breaker
- `MAX_DAILY_LOSS` config (default $200): halt all trading if cumulative daily loss exceeds threshold
- `MAX_CONSECUTIVE_LOSSES` (default 5): pause trading for 15 minutes if hit
- Send Telegram alert when circuit breaker triggers

### 6.6 Data Persistence
- SQLite database (`data/bot.db`) for positions, orders, and bot state
- Enables crash recovery and accurate state reconstruction on restart
- `trades.csv` remains as a secondary export for analysis

---

## 7. Configuration & Setup

### 7.1 Environment Variables (`.env`)
```
# Polymarket
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=
POLY_PRIVATE_KEY=

# Trading
TRADING_MODE=paper          # paper | live
INITIAL_BANKROLL=100        # USD for paper mode

# Strategy Parameters
SUM_ARB_MIN_GAP=0.03        # minimum YES+NO gap (3¢)
MAKER_TARGET_SUM=0.96       # target combined limit order price
KELLY_FRACTION=0.25         # quarter-Kelly sizing
MAX_SINGLE_TRADE_PCT=0.10   # max 10% bankroll per trade
MAX_DIRECTIONAL_LOSS=50     # max USD loss on one-sided fill
MAX_DAILY_LOSS=200          # circuit breaker: halt if daily loss exceeds
MAX_CONSECUTIVE_LOSSES=5    # pause 15min after N consecutive losses

# Monitoring
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### 7.2 Dependencies
- `py-clob-client` — Polymarket CLOB API
- `websockets` — Binance/Coinbase price feeds
- `python-dotenv` — env var loading
- `httpx` — async HTTP (Telegram API)
- `aiosqlite` — async SQLite for state persistence
- `fastapi` + `uvicorn` — dashboard (Phase 2)

---

## 8. Implementation Phases

1. **Phase 1:** Core infrastructure (price feed, polymarket client, market scanner, order manager, config) + Sum Arb strategy + paper mode + basic logging
2. **Phase 2:** Maker Pre-Order strategy + Telegram notifications
3. **Phase 3:** Web dashboard
4. **Phase 4:** Live mode activation + production hardening
5. **Future:** Rust port
