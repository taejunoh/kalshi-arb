# ETF Flow Lag Observer — Phase 0 Spec (deferred build)

Sibling to `funding_rate_fade` / `vol_skew_fade` (built 2026-05-19). Same Phase 0
pattern: observer-only, record signals + actual_outcomes, evaluate WR after 1-2 weeks.

## Thesis

Spot BTC/ETH ETFs (IBIT, FBTC, ETHA, etc.) publish daily creation/redemption flows
at ~4pm ET. Large positive net flow → next-day demand pressure → BTC/ETH biased up.
Kalshi next-day binary markets *price the open quickly* (within ~30 min of US session
start) but may lag the ETF flow signal during the overnight/Asia window.

**Trade hypothesis:** Buy YES on Kalshi "BTC > X next 24h" if ETF inflow > threshold
and Kalshi binary hasn't repriced yet.

## Data source

- **Free**: [farside.co.uk/btc-etf-flow-all-data](https://farside.co.uk/btc-etf-flow-all-data/)
  HTML table, daily totals, published ~4-6pm ET.
- **Paid alt**: Bloomberg Terminal FLOW function, CoinShares weekly digest.

## Required infrastructure (NEW — not yet built)

1. **Scraper** (`scripts/etf_flow_cron.py`): hourly HTTPx GET, parse HTML table,
   compute daily net flow per asset. Write to new table `etf_flows`.
2. **DB table** (`etf_flows`): `date, asset, flow_usdc, fetched_at`.
3. **Observer hook in `crypto_observer.py`**: read latest flow per asset, populate
   `EventContext.etf_flow_usdc` (new field).
4. **Observer class** `EtfFlowLagObserver`: fires when `|flow| > threshold` AND
   Kalshi market is BTC/ETH next-24h binary with `mts_s < 30000`. Predicts "yes"
   on flow-aligned strikes (e.g., flow > 0 → fade NO side / predict YES on upside).

## Threshold calibration (research, day 1)

Look at Farside historical to find:
- p95 of daily |flow| → that's our `MIN_ABS` threshold
- Lag time: when does Kalshi binary catch up? (compare flow publish time to
  Kalshi YES price within ±1h)

## Phase 0 plan

- Day 1: build scraper + cron + table
- Day 2: build observer + tests + deploy
- Days 3-14: collect signals + actual_outcomes (Kalshi resolution)
- Day 14: prevalence + WR analysis. Promote to Phase 1 trader if WR ≥ 60%.

## Estimated effort

~3-4 hours of new code (scraper is the bulk). Defer to next session per
2026-05-19 conversation — funding_rate_fade + vol_skew_fade built first because
they share existing Deribit pipeline.

## Related

- [[funding_rate_fade]] — same cross-venue pattern, Deribit perp funding
- [[vol_skew_fade]] — same cross-venue pattern, Deribit option chain skew
- [[crypto_observer_phase1_promotion_2026-05-14]] — Phase 0 → Phase 1 promotion criteria
