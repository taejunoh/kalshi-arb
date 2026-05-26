# C1: On-chain Stablecoin Flow Collector Design

**Created:** 2026-05-26
**Status:** DESIGN — not yet built
**Estimated effort:** 6-10h (collector) + 4-6h (backtest analysis)

## Motivation

[[crypto_phase0_phase1_audit_2026-05-26]] showed all 9 Crypto Phase 1 traders -EV (net -$5,350 / 30d). The pattern that survives: **external-data-fed fair value vs Kalshi divergence**. We need *more* external data sources beyond Deribit IV + CME basis + Polymarket.

On-chain stablecoin flows are a **lead indicator** with academic + practitioner backing:
- Large USDT/USDC mints typically precede spot rallies by 6-24h (capital deployment)
- Large net inflows to exchanges (CEX deposits) precede selling pressure
- Net outflows from exchanges (CEX withdrawals to cold storage) signal accumulation

If we can detect these flows in near-real-time, we gain a directional signal for Kalshi crypto markets that very few other Kalshi participants are watching.

## Hypothesis

Three potentially +EV signals:

1. **USDT/USDC large mint** (>$50M in single block): bullish bias next 24h.
2. **Large net inflow to top-5 CEX wallets**: bearish 12-48h (selling pressure).
3. **Large net outflow from CEX to unknown / cold wallets**: bullish 24-72h (accumulation).

For each signal type → compute fair adjustment to Kalshi crypto market price → trade divergence.

## Data sources

| Source | Endpoint | Rate limit | Cost |
|---|---|---|---|
| Etherscan v2 (USDT/USDC ERC-20 transfers) | `api.etherscan.io/api?module=account&action=tokentx` | 5/sec free, 100/sec paid | $200/mo paid tier needed |
| Tronscan (USDT TRC-20 transfers — ~50% of USDT supply) | `apilist.tronscanapi.com/api/transfer` | 5/sec free | $0 free |
| Coin Metrics / Glassnode (exchange flow aggregates) | various | varies | $0-500/mo |
| WhaleAlert API | `api.whale-alert.io` | 60/min on $30/mo | $30/mo |

**Decision: Start with WhaleAlert ($30/mo)** — pre-aggregated whale-sized stablecoin flows, less noise than raw chain queries. Validate +EV before paying for Etherscan paid tier.

## Schema

```sql
CREATE TABLE stablecoin_flow_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,         -- when our collector saw it
    on_chain_ts TEXT NOT NULL,         -- block timestamp from chain
    chain TEXT NOT NULL,               -- 'eth' or 'tron'
    asset TEXT NOT NULL,               -- 'USDT' or 'USDC'
    amount_usd REAL NOT NULL,
    direction TEXT NOT NULL,           -- 'mint', 'burn', 'to_cex', 'from_cex'
    from_address TEXT,
    to_address TEXT,
    from_label TEXT,                   -- 'Binance', 'Coinbase', 'Tether Treasury', 'Unknown' etc
    to_label TEXT,
    tx_hash TEXT,
    UNIQUE(chain, tx_hash)
);
CREATE INDEX idx_sf_detected ON stablecoin_flow_observations(detected_at);
CREATE INDEX idx_sf_direction_amount ON stablecoin_flow_observations(direction, amount_usd);
```

## Collector module

`data_collectors/stablecoin_flow.py`:

```python
"""Poll WhaleAlert API for large stablecoin transactions.
Threshold: >= $10M USD-equivalent.
Cadence: every 10 minutes (WhaleAlert allows free up to 10 req/min).
"""
import os, httpx, sqlite3
from datetime import datetime, timezone

API_KEY = os.getenv("WHALEALERT_API_KEY")
THRESHOLD_USD = 10_000_000

def poll_and_store():
    # 1. Call WhaleAlert /v1/transactions?api_key=...&min_value=10000000&currency=usdt
    # 2. Parse from_label, to_label
    # 3. Classify direction: mint/burn/to_cex/from_cex
    # 4. Upsert into stablecoin_flow_observations (dedupe by chain+tx_hash)
    # 5. Optional: emit synthetic signal event for an observer to consume
    pass
```

Add systemd timer: `kalshi-stablecoin-flow.timer` @ 10min cadence.

## Observer + signal

After 14d of data collected, build observer `stablecoin_flow_lead`:

```python
# In strategies/crypto_observer.py
class StablecoinFlowLeadObserver(_ObserverBase):
    """Fires when a large stablecoin flow is detected, predicting Kalshi
    crypto market direction over 6-48h horizon."""
    name = "stablecoin_flow_lead"
    def __init__(self, ...):
        ...
    def scan_for_signals(self):
        # 1. Query recent stablecoin_flow_observations
        # 2. Rolling 4h net flow: net_mint - net_burn, net_in_cex - net_out_cex
        # 3. If above threshold (e.g., $200M net mint in 4h, or $300M net in_cex):
        #    For each open Kalshi crypto market within 24h close:
        #      emit signal with predicted_direction based on flow direction
        ...
```

## Validation plan

Pre-deploy:
1. Build collector + run for 14d (5/26 → 6/09) → accumulate flow data
2. Backtest: for each historical flow event, compute Kalshi market reaction at 6h/12h/24h horizons
3. Compute EV at each horizon, by flow magnitude bucket
4. Identify +EV cohort if any

If +EV identified (target EV ≥ +$0.05/ct):
5. Build observer (`stablecoin_flow_lead`) — paper-only at launch
6. Build trader (`stablecoin_flow_trader_v1`) — paper-only
7. 14d paper test, promote LIVE only if EV ≥ +5% over n ≥ 50

## Risk + alternative paths

**Risk:** WhaleAlert may have <100ms latency; pro participants likely also watching. Edge may be milliseconds.

**Mitigation:** Focus on Kalshi markets close to settlement (≥30min remaining). Even 5-10min latency vs aggressive HFTs can capture lag if Kalshi MMs are slower.

**Fallback if WhaleAlert+raw chain doesn't work:** consider **Tether Treasury direct watch** (Tether's mint contract is public, mint events are deterministic). Less noisy, more deterministic timing.

## Linked

- [[crypto_phase0_phase1_audit_2026-05-26]] — context
- [[2026-05-26-c2-multi-source-fv-engine.md]] — sibling design, complements
- [[deribit_tail_fade_v1]] precedent: existing external-data strategy that's +EV
