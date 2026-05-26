"""C1 Stablecoin Flow Observer + Trader — SKELETON (not yet wired).

Status (2026-05-26 EoD): collector live, accumulating mints/burns since
17:55 UTC. This file holds the scaffold for observer + paper trader.

⚠️ DO NOT IMPORT INTO PRODUCTION until:
  1. 6/09 backtest (`scripts/stablecoin_flow_backtest.py`) confirms
     ≥1 cohort with signed_return > +0.3% AND hit_rate ≥ 55% AND n ≥ 30.
  2. Gate constants below replaced with backtest-derived values
     (marked TODO_6_09).
  3. Observer added to `OBSERVER_CLASSES` in crypto_observer.py.
  4. EventContext extended with `stablecoin_net_flow_*` fields and
     harness pre-populates them from sqlite.

Wiring checklist on 6/09 (if +EV confirmed):
  - [ ] Replace TODO_6_09_* constants with backtest values
  - [ ] Add to crypto_observer.py OBSERVER_CLASSES tuple
  - [ ] Add fields to EventContext: `stablecoin_net_flow_4h_usd`,
        `stablecoin_net_flow_1h_usd` (Optional[float])
  - [ ] In CryptoObserverStrategy harness: add cached query per scan
        tick (5s TTL ok) that fetches rolling 1h/4h net flow from
        stablecoin_mint_observations
  - [ ] Register `stablecoin_flow_trader_v1` as a Phase 1 paper trader
        in main.py
  - [ ] systemd: no new timer needed (lives inside kalshi-bot loop)
"""
from __future__ import annotations

from typing import Optional

# Imports use TYPE_CHECKING to avoid loading crypto_observer in skeleton mode.
# When wiring on 6/09, change to direct imports.
try:
    from strategies.crypto_observer import BaseObserver, EventContext, Signal  # type: ignore
except ImportError:
    BaseObserver = object  # type: ignore
    EventContext = object  # type: ignore
    Signal = object  # type: ignore


# ── Gate constants — TO FILL IN AFTER 6/09 BACKTEST ────────────────

# Magnitude thresholds (USD) — the rolling-window net flow that triggers signal
TODO_6_09_MIN_NET_FLOW_4H_USD = 200_000_000     # placeholder: $200M net mint over 4h
TODO_6_09_MIN_NET_FLOW_1H_USD = 100_000_000     # placeholder: $100M net mint over 1h

# Direction → predicted Kalshi market outcome
# Mint-dominant flow → BTC/ETH expected to rally → fade "below strike X"
# bets and follow "above strike X" bets. Concrete mapping requires
# knowing whether 6h/12h/24h is the +EV horizon.
TODO_6_09_PRED_HORIZON_H = 12

# Optional: gate which crypto markets to fire on. Hourly markets close fast;
# daily markets give more time for signal to play out.
TODO_6_09_FIRE_ON_SERIES = ("KXBTCD", "KXETHD")    # daily markets only at first

# Minimum mts-to-close on target market (signal needs time to play out)
TODO_6_09_MIN_MTS_TO_CLOSE_S = 6 * 3600


# ── Observer class ─────────────────────────────────────────────────


class StablecoinFlowLeadObserver(BaseObserver):
    """Fires when rolling stablecoin net flow exceeds magnitude threshold,
    predicting Kalshi BTC/ETH market direction over 6-24h horizon.

    Features published in signal:
      - net_flow_4h_usd
      - net_flow_1h_usd
      - direction: 'mint_dominant' | 'burn_dominant'

    Wired-up gate (PLACEHOLDER): fires if |net_flow_4h_usd| > $200M and
    market series is KXBTCD or KXETHD with ≥6h to close. Mint-dominant
    → predict YES on ATM/above-spot ladders. Burn-dominant → predict NO.
    """
    name = "stablecoin_flow_lead"

    def evaluate(self, ctx) -> list:
        # SKELETON — actual logic written on 6/09 after backtest gates known.
        # Stub: never fires until production-wired.
        return []

        # Reference implementation (commented out until 6/09):
        # if ctx.series not in TODO_6_09_FIRE_ON_SERIES:
        #     return []
        # if ctx.close_time_secs is None or ctx.close_time_secs < TODO_6_09_MIN_MTS_TO_CLOSE_S:
        #     return []
        # flow_4h = getattr(ctx, "stablecoin_net_flow_4h_usd", None)
        # if flow_4h is None or abs(flow_4h) < TODO_6_09_MIN_NET_FLOW_4H_USD:
        #     return []
        # direction = "mint_dominant" if flow_4h > 0 else "burn_dominant"
        # # Predict direction → for above-spot strikes:
        # #   mint_dominant → predict YES (spot rising)
        # #   burn_dominant → predict NO  (spot falling)
        # out = []
        # for leg in ctx.b_legs:
        #     above_spot = leg.threshold > ctx.spot
        #     if direction == "mint_dominant":
        #         pred = "yes" if above_spot else "no"
        #     else:
        #         pred = "no" if above_spot else "yes"
        #     out.append(Signal(
        #         observer=self.name, series=ctx.series,
        #         event_key=ctx.event_key, market_id=leg.ticker,
        #         predicted_outcome=pred,
        #         features={
        #             "flow_4h_usd": round(flow_4h, 0),
        #             "flow_1h_usd": round(getattr(ctx, "stablecoin_net_flow_1h_usd", 0) or 0, 0),
        #             "direction": direction,
        #             "above_spot": above_spot,
        #             "dist_pct": round(abs(leg.threshold - ctx.spot) / ctx.spot, 5),
        #         },
        #     ))
        # return out


# ── Paper trader skeleton ──────────────────────────────────────────


# Phase 1 paper trader to be added to crypto_observer_phase1.py on 6/09.
# Reference shape — copy + adjust against backtest-confirmed gates.
"""
class StablecoinFlowTraderV1(_Phase1BaseTrader):
    STRATEGY_NAME = "crypto_stablecoin_flow_v1"
    OBSERVER_NAME = "stablecoin_flow_lead"
    BASE_SIZE_USDC = 5.0
    MIN_YA = 0.20       # FILL FROM BACKTEST
    MAX_YA = 0.80
    # Promo gate (paper → live): n ≥ 50, EV ≥ +$0.05/ct, hit_rate ≥ 55%, 14d
"""
