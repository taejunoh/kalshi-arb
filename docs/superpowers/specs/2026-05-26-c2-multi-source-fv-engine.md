# C2: Multi-source Fair Value Engine Design

**Created:** 2026-05-26
**Status:** DESIGN — not yet built
**Estimated effort:** 8-12h (engine) + 4-6h (initial backtest)

## Motivation

[[crypto_phase0_phase1_audit_2026-05-26]] showed:
- 9 Crypto Phase 1 traders → only `deribit_tail_fade_v1` PAPER +EV (+$531/30d)
- The common pattern in the +EV one: **fair value from external source vs Kalshi divergence**
- Other -EV strategies relied on Kalshi-internal microstructure (orderbook, bid imbalance) — Kalshi MM absorbs these

We have 3 external fair value sources but they're siloed in separate observers:
1. **Deribit IV-lognormal**: `lognormal_fair_dev` — uses BS-style stochastic model on Deribit IV
2. **CME basis**: `cme_basis_fade` — uses CME futures spread
3. **Polymarket**: `cross_venue_signals` — uses Polymarket implied probability

Each fires independently. If 2-3 sources AGREE on fair value, the divergence signal is much higher confidence than any single source. Currently we don't combine them.

## Hypothesis

A **consensus fair value** combining 2-3 external sources will:
1. Reduce noise from any single source's idiosyncrasies (e.g., Polymarket low liquidity, CME basis tracking error)
2. Boost confidence in divergence detection — fire only when 2+ sources agree Kalshi is mispriced
3. Improve signal-to-trader translation rate (current pain point: observer +EV cohort doesn't survive gate alignment)

## Architecture

```
                   ┌─────────────────────────┐
External APIs ──→  │  Source-specific FV     │
(Deribit/CME/Poly) │  computation modules    │
                   └────────┬────────────────┘
                            │ fv_deribit, fv_cme, fv_polymarket per market+time
                            ▼
                   ┌─────────────────────────┐
                   │  Consensus FV engine    │
                   │  (weighted average +    │
                   │   reliability tracking) │
                   └────────┬────────────────┘
                            │ consensus_fv per market+time
                            ▼
                   ┌─────────────────────────┐
                   │  consensus_fv_divergence │
                   │  observer (new)         │
                   └────────┬────────────────┘
                            │ signal when |consensus_fv - kalshi| > threshold
                            ▼
                   ┌─────────────────────────┐
                   │  consensus_fv_trader_v1  │
                   │  (new Phase 1 paper)    │
                   └─────────────────────────┘
```

## Components

### `core/multi_source_fv.py` (new module)

```python
"""Consensus fair value engine. Combines per-source FV estimates with
reliability-weighted average."""

SOURCES = ("deribit_lognormal", "cme_basis", "polymarket")

class MultiSourceFV:
    def __init__(self, db):
        self._db = db
        self._reliability = {}  # source -> rolling 7d accuracy score

    def compute_fv(self, market_id, ts):
        """Returns dict source -> (fv, confidence). Or None if no data."""
        # 1. Query each source's most recent FV estimate for this market
        # 2. Reject estimates older than 60s
        # 3. Return per-source FV + confidence

    def consensus(self, fvs):
        """Weighted average. Weights = reliability × confidence × inverse-staleness."""
        # If only 1 source: pass through (low confidence)
        # If 2-3 sources: weighted average + confidence boost on agreement

    def update_reliability(self, settled_signals):
        """After settlement, update each source's reliability score based
        on how close its FV was to outcome."""
```

### Reliability tracking

Each source's recent 7d accuracy:
- `accuracy(source) = 1 - mean(|fv_source - actual_outcome|)`
- For `actual_outcome ∈ {0, 1}` (binary settlement), source's fv ∈ [0, 1]
- High-accuracy source gets more weight in consensus

### `consensus_fv_divergence` observer

```python
# strategies/crypto_observer.py
class ConsensusFvDivergenceObserver(_ObserverBase):
    """Fires when consensus fair value diverges from Kalshi price by >=
    threshold AND at least 2 sources agree on direction."""
    name = "consensus_fv_divergence"

    def __init__(self, db, multi_fv: MultiSourceFV, threshold=0.05):
        self._fv = multi_fv
        self._threshold = threshold

    def scan_market(self, market_id, ts):
        fvs = self._fv.compute_fv(market_id, ts)
        if not fvs or len(fvs) < 2:
            return None  # need 2+ sources
        consensus, confidence = self._fv.consensus(fvs)
        kalshi_price = ...  # get from scanner
        deviation = consensus - kalshi_price
        if abs(deviation) < self._threshold:
            return None
        # Check that sources agree on direction
        directions = [1 if fv > kalshi_price else -1 for fv, _ in fvs.values()]
        if abs(sum(directions)) < len(fvs) * 0.6:  # 60% directional agreement
            return None
        # Emit signal
        pred = "yes" if consensus > kalshi_price else "no"
        return Signal(
            market_id=market_id,
            predicted_outcome=pred,
            features={"consensus_fv": consensus, "deviation": deviation,
                      "n_sources": len(fvs), "confidence": confidence, ...},
        )
```

### `consensus_fv_trader_v1` Phase 1 trader

Standard `_Phase1Base` subclass. Gate by:
- Confidence ≥ threshold (e.g., 0.6)
- Number of agreeing sources ≥ 2
- |deviation| ≥ 5% (configurable)

## Validation plan

Pre-build:
1. Backfill exercise — compute what `consensus_fv` would have been for all settled `lognormal_fair_dev` signals in 30d
2. For each market+time where ≥2 sources have FV, compute consensus + see if divergence trades would have been +EV
3. If yes, proceed to build engine

Build sequence:
1. `core/multi_source_fv.py` (4-6h)
2. `consensus_fv_divergence` observer (2-3h)
3. Run observer in shadow mode 7-14d, accumulate settlement data
4. `consensus_fv_trader_v1` paper trader (2h)
5. 14d paper test, promote LIVE if EV ≥ +5% over n ≥ 50

## Open questions

1. **How to handle FV time alignment** — Deribit IV updates continuously, CME basis daily, Polymarket sporadically. Choose 5-min snapshot window?
2. **How to score Polymarket reliability** — it's not always-on-book. May need volume-weighted confidence.
3. **What if 2 sources agree but the 3rd strongly disagrees?** Likely better to skip than to overweight the agreers.

## Linked

- [[crypto_phase0_phase1_audit_2026-05-26]] — pattern observation
- [[deribit_tail_fade_v1]] — single-source +EV precedent
- [[2026-05-26-c1-stablecoin-flow-collector.md]] — adds 4th source candidate
