# Kalshi Auto-Trading Bot — Handover Document

_Last updated: March 24, 2026_

---

## Project Overview

An automated trading bot for [Kalshi](https://kalshi.com) prediction markets, implementing 4 strategies reverse-engineered from the platform's most profitable traders. Starts on Kalshi's **Demo API** (paper trading) with **aggressive sizing ($50–200/trade)**, switchable to live with one config change.

---

## Top Trader Research Summary

| Trader | Profit | Strategy | Key Insight |
|--------|--------|----------|-------------|
| **@Domahhhh** | $1.2M | Research + contrarian conviction | Reads resolution rules obsessively; buys when consensus is wrong |
| **@cobybets1** | $640K | Dual-domain (sports + politics) | Rare cross-category edge; active high-frequency |
| **@GaetenD** | $530K | Data modeling in culture markets | Spreadsheet models for Billboard/RT; fades gov shutdowns |
| **@aenews_KT** | $450K | Patient accumulation | Holds positions for months across Kalshi + Polymarket |
| **@Foster** | $390K | Mentions market specialist | Exploits transcript vs. broadcast differences; 100x plays |
| **@theduckguesses** | $240K | Small-bankroll compounding | $100 → $240K in mentions + culture markets |
| **@debl00b** | $160M vol | Algorithmic market making | Automated two-sided quoting at massive scale |

---

## 4 Strategies to Automate

### Strategy 1 — Market Making (inspired by @debl00b)
- **File:** `kalshi_bot/strategies/market_maker.py`
- **What:** Post two-sided limit orders (bid YES + bid NO) on liquid markets, profit from the spread
- **Logic:**
  1. Scan open markets → filter by volume & open interest thresholds
  2. Fetch orderbook → calculate mid price
  3. Place YES limit buy at `mid - spread/2`, NO limit buy at `mid + spread/2`
  4. When one side fills, let the other side fill or cancel
  5. Cancel & requote every 30 seconds
- **Params:** `min_spread_cents: 3`, `requote_interval_sec: 30`, `min_volume: 1000`
- **Risk:** Inventory risk if one side fills and event resolves — capped by position limits

### Strategy 2 — Government Shutdown Fade (inspired by @GaetenD)
- **File:** `kalshi_bot/strategies/shutdown_fade.py`
- **What:** When shutdown markets appear and YES > 25c, buy NO (bet it won't happen)
- **Logic:**
  1. Scan markets for keywords: "government shutdown", "shutdown", "funding"
  2. If YES price > threshold → buy NO contracts
  3. Scale in: buy more as YES price rises (3 levels)
  4. Hold until resolution
- **Edge:** Historical base rate — Congress almost always deals last-minute
- **Params:** `entry_threshold_yes_cents: 25`, `max_position_cents: 50000`, `scale_in_levels: 3`

### Strategy 3 — Value Spread Scanner (inspired by @Foster / @Domahhhh)
- **File:** `kalshi_bot/strategies/value_scanner.py`
- **What:** Scan all markets for statistical mispricings where the spread is abnormally wide
- **Logic:**
  1. Fetch all open markets
  2. For each: get orderbook, calculate spread, volume, recent price history
  3. Score by: `spread_width x volume x time_to_close`
  4. If bid+ask deviates from recent VWAP → take the underpriced side
  5. Place limit order at fair value estimate
- **Params:** `min_spread_cents: 5`, `min_edge_pct: 10`, `scan_interval_sec: 300`

### Strategy 4 — Mentions Market Trader (inspired by @Foster & @theduckguesses)
- **File:** `kalshi_bot/strategies/mentions_trader.py`
- **What:** Trade "Will X say Y?" markets using historical speech frequency analysis
- **Logic:**
  1. Filter markets matching mentions patterns ("Will * mention", "Will * say")
  2. Extract speaker name + target word from market title
  3. Query transcript/news data for historical word frequency
  4. Calculate probability estimate from frequency
  5. If market price diverges by >15% → trade
- **Params:** `min_edge_pct: 15`, `scan_interval_sec: 600`

---

## Architecture

```
kalshi-bot/
├── main.py                          # CLI entry point
├── config.yaml                      # All user-configurable settings
├── requirements.txt
├── kalshi_bot/
│   ├── __init__.py
│   ├── client.py                    # REST API client (RSA-PSS auth)
│   ├── websocket_client.py          # WebSocket client (real-time prices)
│   ├── engine.py                    # Trading engine — orchestrates strategies
│   ├── risk.py                      # Risk manager — position limits, daily loss cap
│   ├── models.py                    # Dataclasses: Order, Position, Market, etc.
│   ├── logger.py                    # Structured logging + trade journal
│   └── strategies/
│       ├── __init__.py
│       ├── base.py                  # Abstract base strategy class
│       ├── market_maker.py          # Strategy 1: Market Making
│       ├── shutdown_fade.py         # Strategy 2: Gov Shutdown Fade
│       ├── value_scanner.py         # Strategy 3: Value Spread Scanner
│       └── mentions_trader.py       # Strategy 4: Mentions Market
```

---

## Core Components

### API Client (`client.py`)
- RSA-PSS signature authentication (KALSHI-ACCESS-KEY, TIMESTAMP, SIGNATURE headers)
- Methods: `get_markets()`, `get_orderbook()`, `place_order()`, `cancel_order()`, `batch_cancel()`, `get_balance()`, `get_positions()`, `get_fills()`
- Automatic rate limiting (20 reads/sec, 10 writes/sec for Basic tier)
- Retry logic with exponential backoff
- Demo vs Production base URL switch via `config.yaml -> mode`

### WebSocket Client (`websocket_client.py`)
- Authenticated WSS connection to `wss://demo-api.kalshi.co/trade-api/ws/v2`
- Subscribes to `ticker`, `orderbook_delta`, `fill`, `trade` channels
- Auto-reconnect on disconnect
- Callbacks push data into strategy engines in real-time

### Risk Manager (`risk.py`)
- **Per-trade limit:** $50-200 (configurable)
- **Daily loss cap:** $500 — bot pauses all strategies when hit
- **Max open positions:** 20 markets simultaneously
- **Max position per market:** $500
- **Category concentration check:** prevents over-exposure to one event type
- All orders pass through risk manager before submission to API

### Trading Engine (`engine.py`)
- Async event loop running all enabled strategies on their own intervals
- Schedule: Market Maker every 30s, Shutdown Fade every 1hr, Value Scanner every 5min, Mentions every 10min
- Graceful shutdown: batch-cancels all open orders on SIGINT/SIGTERM
- State persistence: positions and P&L saved to local JSON between restarts

---

## Config (`config.yaml`)

```yaml
mode: "demo"                    # "demo" or "live" — single flag to switch
api_key_id: "YOUR_KEY_ID"
private_key_path: "./kalshi-key.key"

risk:
  max_per_trade_cents: 20000    # $200
  min_per_trade_cents: 5000     # $50
  daily_loss_cap_cents: 50000   # $500
  max_open_positions: 20
  max_per_market_cents: 50000   # $500

strategies:
  market_maker:
    enabled: true
    min_spread_cents: 3
    requote_interval_sec: 30
    min_volume: 1000
  shutdown_fade:
    enabled: true
    entry_threshold_yes_cents: 25
    max_position_cents: 50000
    keywords: ["government shutdown", "shutdown", "funding deadline"]
  value_scanner:
    enabled: true
    min_spread_cents: 5
    min_edge_pct: 10
    scan_interval_sec: 300
  mentions_trader:
    enabled: true
    min_edge_pct: 15
    scan_interval_sec: 600

logging:
  level: "INFO"
  trade_journal: "./trades.jsonl"
```

---

## Dependencies

```
cryptography>=41.0.0       # RSA-PSS signing
requests>=2.31.0           # REST API calls
websockets>=12.0           # WebSocket connection
pyyaml>=6.0                # Config parsing
aiohttp>=3.9.0             # Async HTTP (for mentions data)
```

---

## Implementation Order

| Step | What | Files | Status |
|------|------|-------|--------|
| **1** | Project scaffolding + config + models | `config.yaml`, `models.py`, `requirements.txt`, `__init__.py` | Not started |
| **2** | REST API client with RSA-PSS auth | `client.py` | Not started |
| **3** | WebSocket client for real-time data | `websocket_client.py` | Not started |
| **4** | Risk manager | `risk.py` | Not started |
| **5** | Base strategy class + Market Maker | `strategies/base.py`, `strategies/market_maker.py` | Not started |
| **6** | Shutdown Fade + Value Scanner | `strategies/shutdown_fade.py`, `strategies/value_scanner.py` | Not started |
| **7** | Mentions Market Trader | `strategies/mentions_trader.py` | Not started |
| **8** | Trading engine + CLI + logger | `engine.py`, `main.py`, `logger.py` | Not started |

---

## How to Run (after build)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add your Kalshi Demo API key
#    - Get one at https://kalshi.com/settings/api
#    - Save private key as ./kalshi-key.key
#    - Set api_key_id in config.yaml

# 3. Test authentication
python main.py --test-auth

# 4. Scan markets (read-only)
python main.py --scan

# 5. Dry run (simulates orders, no execution)
python main.py --dry-run

# 6. Run bot on Demo API
python main.py

# 7. Switch to live (when ready)
#    Change config.yaml -> mode: "live"
```

---

## Verification Checklist

- [ ] `--test-auth` prints demo account balance
- [ ] `--scan` lists open markets with prices and spreads
- [ ] `--dry-run` logs simulated orders to console without API calls
- [ ] Live demo run executes trades, logged in `trades.jsonl`
- [ ] Daily loss cap triggers bot pause (test with $5 cap)
- [ ] SIGINT cancels all open orders before exit
- [ ] Each strategy can be individually enabled/disabled via config

---

## Key Design Decisions

1. **Demo-first:** All development and testing against `demo-api.kalshi.co` — zero risk of losing real money
2. **Strategy pattern:** Each strategy extends `BaseStrategy` with `analyze()` and `execute()` methods — easy to add new strategies later
3. **Risk as gatekeeper:** Every order goes through `RiskManager.approve()` before hitting the API — no strategy can bypass limits
4. **JSONL trade journal:** Every trade logged as a JSON line for later analysis and backtesting
5. **Async engine:** Using `asyncio` for the main loop allows the WebSocket client and strategies to run concurrently without threads

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| API key compromise | Private key stored locally, never committed; `.gitignore` includes `*.key` and `config.yaml` |
| Runaway losses | Daily loss cap auto-pauses; per-trade and per-market limits |
| Rate limiting | Built-in rate limiter respects Kalshi's 20 reads/sec, 10 writes/sec |
| Market making inventory risk | Position limits per market; auto-cancel on exit |
| Stale quotes | Requote interval ensures orders stay current; WebSocket for real-time data |
| Demo -> Live mismatch | Same codebase, same logic — only the base URL changes |

---

## What's Next

Once the bot is built and validated on Demo API:
1. Run paper trading for 1-2 weeks, review `trades.jsonl` for P&L
2. Tune strategy parameters based on paper results
3. Flip `mode: "live"` and start with minimum position sizes
4. Gradually increase sizing as confidence in each strategy grows
5. Consider adding new strategies (Billboard modeling, Rotten Tomatoes front-running)
