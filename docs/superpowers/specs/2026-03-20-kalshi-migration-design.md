# Polymarket → Kalshi Migration Design

## Context

Polymarket is not available in New York. This project migrates the arbitrage bot from Polymarket's CLOB API to Kalshi's REST API v2. The bot keeps the same two strategies (Sum Arb, Maker Pre-Order) and paper/live trading modes.

## Decision: In-Place Replacement

No abstraction layer. No multi-exchange support. Direct swap — Polymarket references removed, Kalshi references added.

---

## 1. Kalshi Client (`core/kalshi_client.py`)

Replaces `core/polymarket_client.py`.

### Authentication

Kalshi uses **API key + RSA private key** signing. Each request is signed with the RSA key using the timestamp + method + path as the message. No blockchain wallet needed.

### API Mapping

| Method | Kalshi Endpoint | Notes |
|--------|----------------|-------|
| `get_active_markets()` | `GET /trade-api/v2/markets?status=open` | Filterable by `series_ticker`, `event_ticker`. Returns markets with `ticker`, `yes_ask`, `no_ask` |
| `get_market(ticker)` | `GET /trade-api/v2/markets/{ticker}` | Returns `last_price`, `yes_ask`, `no_ask`, `close_time`, `expiration_time` |
| `get_market_prices(ticker)` | Calls `get_market(ticker)` | Returns `(yes_price, no_price)` in cents from a single call — no separate token lookups |
| `get_order_book(ticker)` | `GET /trade-api/v2/markets/{ticker}/orderbook` | Returns `{"yes": [...], "no": [...]}` — both sides in one call. Depth = sum of quantities at each level |
| `place_market_order(ticker, side, count)` | `POST /trade-api/v2/portfolio/orders` | `type=market`, `action=buy`, `side=yes/no` |
| `place_limit_order(ticker, side, price, count)` | `POST /trade-api/v2/portfolio/orders` | `type=limit`, `action=buy`, `side=yes/no` |
| `cancel_order(order_id)` | `DELETE /trade-api/v2/portfolio/orders/{order_id}` | |
| `get_positions()` | `GET /trade-api/v2/portfolio/positions` | |
| `get_balance()` | `GET /trade-api/v2/portfolio/balance` | Returns USD balance (replaces USDC balance check) |
| `preflight_check()` | Calls `get_balance()` + `get_active_markets()` | Verifies API connectivity |

### Rate Limiting

Kalshi rate limit: 10 requests/second. Client implements 100ms minimum interval between requests (same as current Polymarket client).

### Client Design

Synchronous client using `httpx.Client` (same pattern as current `PolymarketClient`). The `OrderManager` wraps sync calls in async context — no change to that pattern.

### Market Structure Differences

| Polymarket | Kalshi |
|-----------|--------|
| `conditionId` (market identifier) | `ticker` (e.g., `KXBTC-25MAR20-T70000`) |
| `token_id` for YES, separate `token_id` for NO | Single `ticker`, specify `side=yes` or `side=no` |
| Prices as decimals (0.01–0.99) | Prices in **cents** (1–99) |
| `py-clob-client` SDK | Raw HTTP via `httpx` with RSA signing |

### Request Signing

```python
import base64
from cryptography.hazmat.primitives.asymmetric import padding, utils
from cryptography.hazmat.primitives import hashes

def sign_request(private_key, timestamp_ms, method, path):
    message = f"{timestamp_ms}{method}{path}"
    signature = private_key.sign(
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode()
```

Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`.

---

## 2. Config (`core/config.py`)

### Removed Env Vars

- `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`
- `POLY_PRIVATE_KEY`
- `CLOB_ENDPOINT`
- `POLYGON_CHAIN_ID`

### New Env Vars

| Variable | Description | Default |
|----------|-------------|---------|
| `KALSHI_API_KEY_ID` | API key identifier | — |
| `KALSHI_RSA_PRIVATE_KEY_PATH` | Path to RSA private key PEM file | — |
| `KALSHI_API_BASE_URL` | API base URL | `https://trading-api.kalshi.com` |
| `KALSHI_FEE_PER_CONTRACT` | Fee per contract in USD | `0.07` |

### Fee Calculation

Replace Polymarket's proximity-based formula with Kalshi's flat per-contract fee with cap:

```python
def estimate_fee(self, num_contracts: int, price_cents: int) -> float:
    """Kalshi fee per contract, capped so fee never exceeds potential profit."""
    raw_fee = self.kalshi_fee_per_contract * num_contracts
    # Fee cannot exceed what you'd win on either outcome
    max_fee_yes = (price_cents / 100) * num_contracts   # cost of yes side
    max_fee_no = ((100 - price_cents) / 100) * num_contracts  # cost of no side
    return min(raw_fee, max_fee_yes, max_fee_no)

def estimate_fee_per_contract(self, price_cents: int) -> float:
    """Fee for a single contract — used by strategies to evaluate signals before sizing."""
    return min(self.kalshi_fee_per_contract, price_cents / 100, (100 - price_cents) / 100)
```

### Unit Conventions

All internal prices remain as **decimals** (0.01–0.99, representing USD). The Kalshi client converts cents ↔ decimals at the API boundary:
- API returns cents → client divides by 100 → strategies see decimals
- Strategies pass decimals → client multiplies by 100 → API receives cents

This means `SUM_ARB_MIN_GAP` (0.03), `MAKER_TARGET_SUM` (0.96), and all other config parameters stay unchanged.

---

## 3. Order Manager (`core/order_manager.py`)

### Changes

- Market orders: call `kalshi_client.place_market_order(ticker, side, count)` — `count` is number of contracts (integer), not token quantity
- Limit orders: call `kalshi_client.place_limit_order(ticker, side, yes_price, count)`
- Response parsing: Kalshi returns `{"order": {"id": "...", "status": "..."}}` — extract `order["id"]`
- Fee calculation: `config.estimate_fee(count)` instead of `config.estimate_taker_fee(price)`
- Size conversion: `count = int(size_usdc / (price_cents / 100))` — contracts are integers

### Unchanged

- Circuit breaker logic (daily loss limit, consecutive losses)
- Paper mode simulation
- Kelly fraction calculation
- Database recording

---

## 4. Market Scanner (`core/market_scanner.py`)

- Cache key: `ticker` instead of `conditionId`
- Lock key: `ticker` instead of `conditionId`
- Calls `kalshi_client.get_active_markets()` instead of `polymarket_client.get_active_markets()`

---

## 5. Strategies

### Sum Arb (`strategies/sum_arb.py`)

Core logic unchanged: if `yes_ask + no_ask < $1.00 - fees`, buy both sides.

**Fee evaluation without knowing position size**: The strategy evaluates per-contract edge first:
```
edge_per_contract = 1.00 - yes_price - no_price - 2 * fee_per_contract
```
If `edge_per_contract > MIN_PROFIT_THRESHOLD`, the signal fires. Position sizing (Kelly) then determines `count`, and total fee is `count * fee_per_contract`.

Changes:
- Replace `get_token_ids(market)` → use `market["ticker"]` directly
- Replace `get_last_trade_price(token_id)` → `get_market_prices(ticker)` returns `(yes_price, no_price)` in decimals from a single API call
- Replace `get_order_book_depth(token_id)` → `get_order_book(ticker)` returns both sides; extract depth per side
- Order placement: `place_market_order(ticker, "yes", count)` and `place_market_order(ticker, "no", count)`
- Fee: `config.estimate_fee_per_contract(price_cents)` for signal evaluation, `config.estimate_fee(count, price_cents)` for execution

### Maker Pre-Order (`strategies/maker_preorder.py`)

Core logic unchanged: place limit orders on both sides before resolution.

Changes:
- Market filtering: use Kalshi's `close_time` field (ISO 8601 timestamp). Filter for markets where `close_time` is 2–10 minutes from now. This replaces the regex match on question text.
- Filter by `series_ticker` prefix for crypto markets (e.g., `KXBTC`, `KXETH`, `KXSOL`) instead of keyword matching
- Use `ticker` for order placement
- Prices in decimals (client handles cent conversion)

---

## 6. Main Orchestrator (`main.py`)

- Import `KalshiClient` instead of `PolymarketClient`
- Preflight checks: verify Kalshi API key exists, test `get_balance()`, test `get_active_markets()`
- No wallet address or private key validation (no blockchain)
- Remove Polygon chain references

---

## 7. Dependencies (`requirements.txt`)

- Remove: `py-clob-client>=0.1.0`
- Add: `cryptography>=41.0.0` (for RSA signing)
- Add: `fastapi>=0.104.0` and `uvicorn>=0.24.0` (already imported but missing from requirements — pre-existing fix)
- Keep: `httpx>=0.27.0` (already present, used for Kalshi HTTP calls)

## 7.5 Unchanged Files

These core files need **no changes**:
- `core/price_feed.py` — Binance WebSocket, exchange-agnostic
- `core/database.py` — generic SQLite schema, no Polymarket references

---

## 8. Files Changed

| File | Action |
|------|--------|
| `core/polymarket_client.py` | **Delete** |
| `core/kalshi_client.py` | **Create** — new exchange client |
| `core/config.py` | **Edit** — swap env vars, fee formula |
| `core/order_manager.py` | **Edit** — update client calls, response parsing |
| `core/market_scanner.py` | **Edit** — ticker instead of conditionId |
| `strategies/sum_arb.py` | **Edit** — adapt to Kalshi market structure |
| `strategies/maker_preorder.py` | **Edit** — adapt filtering, order calls |
| `main.py` | **Edit** — import swap, preflight updates |
| `.env.example` | **Edit** — new Kalshi env vars |
| `docs/setup-guide.md` | **Rewrite** — Kalshi account setup |
| `requirements.txt` | **Edit** — swap dependencies |
| `handover.md` | **Rewrite** — update all references |
| `monitoring/dashboard/static/index.html` | **Edit** — update any Polymarket branding |
| `monitoring/dashboard/api.py` | **Edit** — update title from "Polymarket Arb Bot" to "Kalshi Arb Bot" |
| `tests/test_polymarket_client.py` | **Delete** → create `tests/test_kalshi_client.py` |
| `tests/test_config.py` | **Edit** — new fee tests |
| `tests/test_order_manager.py` | **Edit** — mock Kalshi responses |
| `tests/test_market_scanner.py` | **Edit** — ticker-based mocks |
| `tests/test_sum_arb.py` | **Edit** — Kalshi market structure |
| `tests/test_maker_preorder.py` | **Edit** — Kalshi market filtering |
| `tests/test_main.py` | **Edit** — import updates |
| `tests/test_integration.py` | **Edit** — Kalshi-based integration |
| `tests/test_preflight.py` | **Edit** — Kalshi preflight |
| `tests/test_dashboard_api.py` | **Edit** — if any Polymarket refs |

---

## 9. Testing Strategy

All 53 existing tests adapted to mock Kalshi API responses. Same test structure, same coverage targets. Key mock changes:

- Market responses use `ticker` + cent-based pricing
- Order responses use `{"order": {"id": "..."}}`
- Auth mocking: skip RSA signing in tests
- Fee tests: flat $0.07/contract instead of proximity formula
