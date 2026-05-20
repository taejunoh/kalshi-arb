"""Crypto observer harness — 8 parallel hypothesis-based observers.

Replaces the retired `ladder_crypto` strategy (n=54 / -77.5% ROI). Each
observer encodes a distinct hypothesis about KXBTC/KXETH market
inefficiencies. All run in observer-only mode (no trades) — signals
are recorded to `crypto_observer_signals`, and the settlement backfill
cron fills in `actual_outcome` once Kalshi settles each ticker.

After ~1 week of data, per-observer WR + 95% CI determines promotion
candidates. This is the Phase 0 prevalence scanner pattern used by the
A1 ladder portfolio (see [[portfolio_2026-05_non_sports_strategies]]).

Observers:
  1. tail_decay          — far-OTM B near close → predict no-touch
  2. atm_fade            — ATM B near close   → predict no-touch
  3. cluster_sum         — ATM±5 sum-to-1 violation → predict directional
  4. bvt_consistency     — T vs Σ B(≥X) misprice → predict T outcome
  5. spot_drift_momentum — near-close spot momentum → predict touch
  6. open_widening       — event-open spread evolution → predict ATM touch
  7. deribit_tail_skew   — tail vs lognormal misprice → predict no-touch
  8. bid_imbalance       — top-of-book size asymmetry → predict imbalance side

All observers use scanner-cached market data (no per-ticker API calls);
the Kalshi /markets endpoint already returns yes_ask_dollars, sizes,
and liquidity_dollars in the cached dict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("crypto_observer")

TARGET_SERIES = frozenset({"KXBTC", "KXETH", "KXBTCD", "KXETHD", "KXXRP"})
SERIES_ASSET = {
    "KXBTC": "BTC", "KXBTCD": "BTC",
    "KXETH": "ETH", "KXETHD": "ETH",
    "KXXRP": "XRP",
}

# Tunables — all observers use the same trigger thresholds for the
# initial 1-week sweep. Post-data they're independently re-tuned.
TAIL_DECAY_MTS_MAX_S = 300            # ≤5 min to close
TAIL_DECAY_DIST_MIN_PCT = 0.010       # ≥1.0% from spot
TAIL_DECAY_YA_MAX = 0.05              # yes_ask ≤ $0.05
TAIL_DECAY_YB_SIZE_MIN = 50           # active bid present

# late_close_tail (2026-05-18): broader sibling of tail_decay that covers
# the mid-OTM band (ya $0.05-$0.30) ignored by tail_decay (≤$0.05) and by
# deribit_tail_fade (which uses a lognormal-ratio gate, not a ya band).
# Trigger window extended to 10 min to close + dist relaxed to 0.5% from
# spot to compensate for the tighter ya floor. Predicts "no" on the same
# theory as tail_decay — OTM strikes don't get touched late in the event.
LATE_CLOSE_TAIL_MTS_MAX_S = 600
LATE_CLOSE_TAIL_DIST_MIN_PCT = 0.005
LATE_CLOSE_TAIL_YA_MIN = 0.05
LATE_CLOSE_TAIL_YA_MAX = 0.30
LATE_CLOSE_TAIL_YB_SIZE_MIN = 30

# mid_otm_tail (2026-05-20): wider-mts sibling of late_close_tail.
# Tests the same OTM-no-touch thesis at 10 min - 2 hr to close instead
# of inside 10 min. Motivation: late_close_tail's natural fire zone is
# na ~0.95-0.98 (market has converged), where Kalshi fee = (1-na) per
# contract eats 100% of gross profit. Same thesis fired earlier should
# see na in the 0.85-0.93 fee-positive zone where (1-na) > $0.07 fee
# floor. Phase 0 data-collection only — no Phase 1 trader until WR is
# measured per (mts, na) bucket.
MID_OTM_TAIL_MTS_MIN_S = 600          # exclude late_close_tail window
MID_OTM_TAIL_MTS_MAX_S = 7200         # ≤2 hr to close
MID_OTM_TAIL_DIST_MIN_PCT = 0.005
MID_OTM_TAIL_YA_MIN = 0.05
MID_OTM_TAIL_YA_MAX = 0.30
MID_OTM_TAIL_YB_SIZE_MIN = 30

ATM_FADE_MTS_MAX_S = 300
ATM_FADE_BUCKET_RADIUS = 0.25         # |bucket - spot| ≤ 0.25 × grid
ATM_FADE_YA_MIN = 0.40
ATM_FADE_YA_MAX = 0.65                # MM already pricing 99%+ correctly isn't
                                       # alpha — restrict to mid-range overpricing.

CLUSTER_RADIUS = 5                    # ATM ± 5 buckets
CLUSTER_SUM_UNDERPRICED_MAX = 0.95    # Σ ya ≤ 0.95 → buy-yes arb
CLUSTER_SUM_OVERPRICED_MIN = 1.05     # Σ yb ≥ 1.05 → buy-no arb
CLUSTER_ATM_COVERAGE_MAX_PCT = 0.005  # ATM bucket must be within 0.5% of spot —
                                       # otherwise the event's bucket range
                                       # doesn't cover spot and the cluster is
                                       # an OTM zone with meaningless math.

BVT_MISALIGN_MIN = 0.05               # |Σya(B≥X) - ya(T_X)| ≥ $0.05
BVT_T_YA_MIN = 0.05                   # skip T markets quoted at extreme prices —
BVT_T_YA_MAX = 0.95                   # these are usually phantom-quoted illiquids
                                       # whose resolution is trivially predictable.

SPOT_DRIFT_LOOKBACK_S = 60
SPOT_DRIFT_PCT_MIN = 0.003            # ≥ 0.3% move in 60s
SPOT_DRIFT_MTS_MAX_S = 300

OPEN_WIDENING_MAX_AGE_S = 600         # within 10 min of open
OPEN_WIDENING_BUCKET_RADIUS_PCT = 0.010  # ATM ± 1% of spot

DERIBIT_TAIL_RATIO = 3.0              # ya ≥ 3 × P_LN
DERIBIT_TAIL_MIN_YA = 0.03            # ya ≥ $0.03 (don't fire on $0.01 noise)
DERIBIT_TAIL_MIN_YB_SIZE = 30         # active bid present

# deribit_atm_skew (2026-05-18): ATM-bucket parallel of deribit_tail_skew.
# Tests whether Kalshi mid-range ATM prices are also above the lognormal
# fair value (most existing alpha sits in tails). RATIO floor 1.5 (vs 3.0
# for tails) — ATM has high P_LN so 3.0× overshoot would require ya>1.
# ya band keeps fires in a mid-range zone where overpricing is plausible
# and entry economics are favourable.
DERIBIT_ATM_RATIO = 1.5
DERIBIT_ATM_MIN_YA = 0.30
DERIBIT_ATM_MAX_YA = 0.70
DERIBIT_ATM_BUCKET_RADIUS = 0.25      # |bucket-spot| ≤ 0.25 × grid
DERIBIT_ATM_MTS_MAX_S = 600           # ≤10 min to close
DERIBIT_ATM_MIN_YB_SIZE = 30
# 2026-05-15: ratio > 50 → p_ln ≈ 0 degenerate. Phase 0 n=22 cohort
# above this gate had WR 86% (vs 97-98% for ratio 3-50) — lognormal
# model breaks down on deep-OTM where fat-tail probability >> ATM-IV
# lognormal. Skip rather than trade noise.
DERIBIT_TAIL_RATIO_CAP = 50.0

BID_IMBALANCE_RATIO_MIN = 3.0         # (yb+nb) / (ya+na) sizes ≥ 3
BID_IMBALANCE_MTS_MAX_S = 600         # ≤ 10 min to close
BID_IMBALANCE_FADE_MAX_YA = 0.60      # flipped sibling only fires below this ya —
                                       # Phase 0 cohort 2026-05-15 (n=266) showed
                                       # full-flip 63.9% WR but ya<0.60 sub-cohort
                                       # 83% WR (n=141). Above 0.60 the original
                                       # direction still wins, so the flipped
                                       # observer stays out of that zone.

# ETF_FLOW_LAG: spot ETF daily volume z-score with sign of price change.
# Big +ve → bullish creation activity → fade DOWN-strike Kalshi binaries
# (predict "no" on strikes below spot). Big -ve → bearish redemption.
ETF_FLOW_MIN_ABS_Z = 1.5         # ≥1.5 sigma above 20-day vol baseline
ETF_FLOW_MTS_MAX_S = 86400        # within 24h of close
ETF_FLOW_MIN_YA = 0.05
ETF_FLOW_MIN_YB_SIZE = 30
#
# CME_BASIS_FADE: CME front-month futures vs spot basis. Sustained
# positive basis (contango) → fade DOWN-strikes. Negative basis
# (backwardation) → fade UP-strikes.
CME_BASIS_MIN_ABS = 0.002        # ≥0.2% basis (typical noise level ~0.05%)
CME_BASIS_MTS_MAX_S = 86400
CME_BASIS_MIN_YA = 0.05
CME_BASIS_MIN_YB_SIZE = 30

# 2026-05-19 Phase 0 additions — observers that exploit cross-venue
# disagreement beyond the Deribit option-chain lognormal model.
#
# FUNDING_RATE_FADE: Deribit BTC/ETH perp funding rate elevated
# (longs paying shorts heavily) historically precedes mean reversion.
# Phase 0 thesis: when |funding_8h| > threshold, fade the direction the
# crowd is positioned in. Predict the SAME direction Kalshi over-prices
# (no fancy mean-reversion call — observer just records the disagreement
# magnitude; predicted_outcome="no" by convention, like tail_skew).
FUNDING_RATE_MIN_ABS = 0.0003     # ≥0.03% / 8h ≈ ~33% APR (elevated)
FUNDING_RATE_MTS_MAX_S = 28800    # within 8h of close (one funding cycle)
FUNDING_RATE_MIN_YA = 0.05
FUNDING_RATE_MIN_YB_SIZE = 30
#
# VOL_SKEW_FADE: put-call IV asymmetry. Sharp put skew = downside fear
# priced in options market. Phase 0 thesis: extreme skew (|put_IV - call_IV|
# > 5 pts) signals coming mean reversion. Predict "no" on tail strikes
# in the direction of the priced fear (e.g., put skew → fade BTC down).
VOL_SKEW_MIN_ABS = 0.05           # ≥5 IV points (decimal) asymmetry
VOL_SKEW_MTS_MAX_S = 86400        # within 24h of close
VOL_SKEW_MIN_YA = 0.05
VOL_SKEW_MIN_YB_SIZE = 30

# Shared spread gate for Phase 0 cross-venue observers (vol_skew_fade,
# cme_basis_fade, etf_flow_lag). 5/19 PM ROI audit showed all three had
# 67-90% WR but -7 to -17% pooled ROI because they fired on wide-spread
# markets where bid-ask compression killed edge regardless of side. At
# ya + na - 1.0 ≤ 5c the loss shrinks 2-3× (etf_flow ROI -7→-2%);
# at ≤3c ROI flips positive on small samples. See
# [[vol_skew_fade_roi_audit_2026-05-19]].
CROSS_VENUE_MAX_SPREAD = 0.05

# Cooldown so the same observer × market doesn't fire every 0.2s
SIGNAL_COOLDOWN_S = 60.0


# ── Helpers ────────────────────────────────────────────────────────


def _parse_suffix_threshold(suffix: str) -> Optional[float]:
    """Numeric strike from a B<int> or T<float> suffix."""
    if len(suffix) < 2:
        return None
    i = 0
    while i < len(suffix) and not (suffix[i].isdigit() or suffix[i] == "."):
        i += 1
    if i >= len(suffix):
        return None
    try:
        return float(suffix[i:])
    except ValueError:
        return None


def _suffix_kind(suffix: str) -> Optional[str]:
    if not suffix:
        return None
    head = suffix[0]
    if head == "B":
        return "bucketed"
    if head == "T":
        return "cumulative"
    return None


def _event_key(ticker: str) -> str:
    parts = ticker.split("-")
    return "-".join(parts[1:-1]) if len(parts) >= 3 else ""


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close_time_secs(m: dict, now: datetime) -> Optional[float]:
    ct = m.get("close_time", "")
    if not ct:
        return None
    try:
        dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
    except Exception:
        return None
    return (dt - now).total_seconds()


def _open_age_secs(m: dict, now: datetime) -> Optional[float]:
    ot = m.get("open_time", "")
    if not ot:
        return None
    try:
        dt = datetime.fromisoformat(ot.replace("Z", "+00:00"))
    except Exception:
        return None
    return (now - dt).total_seconds()


def _lognormal_p_above(spot: float, strike: float, vol: float,
                       tau_years: float) -> Optional[float]:
    """P(S_T ≥ K) under lognormal — same formula as B2 strategy."""
    if spot <= 0 or strike <= 0 or vol <= 0 or tau_years <= 0:
        return None
    try:
        d = (math.log(spot / strike) - 0.5 * vol * vol * tau_years) / (
            vol * math.sqrt(tau_years)
        )
        return 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))
    except (ValueError, ZeroDivisionError):
        return None


# ── Data ───────────────────────────────────────────────────────────


@dataclass
class Leg:
    """One enriched market in an event, indexed by strike."""
    ticker: str
    kind: str               # 'bucketed' | 'cumulative'
    threshold: float
    ya: float
    yb: float
    na: float
    nb: float
    ya_size: float
    yb_size: float
    na_size: float
    nb_size: float
    liquidity: float
    raw: dict = field(repr=False)


@dataclass
class EventContext:
    """Per-event view shared across all observers in one scan tick."""
    series: str               # "KXBTC" / "KXETH"
    asset: str                # "BTC" / "ETH"
    event_key: str
    now: datetime
    spot: float
    spot_60s_ago: Optional[float]
    deribit_iv: Optional[float]    # decimal (e.g. 0.50 = 50%)
    b_legs: list[Leg]              # sorted asc by threshold
    t_legs: list[Leg]              # sorted asc by threshold
    open_time_secs: Optional[float]   # seconds since open of representative leg
    close_time_secs: Optional[float]  # seconds until close
    # 2026-05-19 Phase 0 additions (None if Deribit fetch failed this tick)
    funding_8h: Optional[float] = None   # signed decimal, e.g. 0.0005 = +0.05%/8h
    iv_skew: Optional[float] = None      # put_IV - call_IV at ±10% strike, decimal
    etf_flow_z: Optional[float] = None   # signed volume z-score on IBIT/ETHA
    cme_basis: Optional[float] = None    # (futures_close - spot) / spot
    # 2026-05-20 Phase 0 cross-asset (filled identically on every ctx by harness)
    spot_btc: Optional[float] = None
    spot_eth: Optional[float] = None
    spot_btc_30s_ago: Optional[float] = None
    spot_eth_30s_ago: Optional[float] = None
    spot_btc_10s_ago: Optional[float] = None
    spot_eth_10s_ago: Optional[float] = None
    corr_5min: Optional[float] = None
    corr_15min: Optional[float] = None
    book_mid_cache: Optional[object] = None  # _BookMidCache, untyped to avoid circular import

    def atm_bucket_index(self) -> Optional[int]:
        """Index in b_legs of the bucket whose center is closest to spot."""
        if not self.b_legs:
            return None
        return min(range(len(self.b_legs)),
                   key=lambda i: abs(self.b_legs[i].threshold - self.spot))

    def bucket_grid(self) -> Optional[float]:
        """Diff between adjacent bucket thresholds."""
        if len(self.b_legs) < 2:
            return None
        diffs = [self.b_legs[i + 1].threshold - self.b_legs[i].threshold
                 for i in range(len(self.b_legs) - 1)]
        diffs.sort()
        return diffs[len(diffs) // 2]


@dataclass
class Signal:
    observer: str
    series: str
    event_key: str
    market_id: str
    predicted_outcome: str        # 'yes' | 'no'
    features: dict


# ── Base ───────────────────────────────────────────────────────────


class BaseObserver:
    name: str = "base"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        """Return zero or more signals for this event. Pure function — no I/O."""
        raise NotImplementedError


# ── 1. tail_decay ──────────────────────────────────────────────────


class TailDecayObserver(BaseObserver):
    name = "tail_decay"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None or ctx.close_time_secs > TAIL_DECAY_MTS_MAX_S:
            return []
        if ctx.close_time_secs <= 0:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya <= 0 or leg.ya > TAIL_DECAY_YA_MAX:
                continue
            if leg.yb_size < TAIL_DECAY_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct < TAIL_DECAY_DIST_MIN_PCT:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={
                    "ya": leg.ya, "yb": leg.yb,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                },
            ))
        return out


# ── 2. atm_fade ────────────────────────────────────────────────────


class AtmFadeObserver(BaseObserver):
    name = "atm_fade"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None or ctx.close_time_secs > ATM_FADE_MTS_MAX_S:
            return []
        if ctx.close_time_secs <= 0:
            return []
        grid = ctx.bucket_grid()
        if grid is None or grid <= 0:
            return []
        atm_idx = ctx.atm_bucket_index()
        if atm_idx is None:
            return []
        leg = ctx.b_legs[atm_idx]
        if leg.ya < ATM_FADE_YA_MIN or leg.ya > ATM_FADE_YA_MAX:
            return []
        if abs(leg.threshold - ctx.spot) > ATM_FADE_BUCKET_RADIUS * grid:
            return []
        return [Signal(
            observer=self.name, series=ctx.series,
            event_key=ctx.event_key, market_id=leg.ticker,
            predicted_outcome="no",
            features={
                "ya": leg.ya, "yb": leg.yb,
                "spot": ctx.spot, "strike": leg.threshold,
                "grid": grid, "mts_s": round(ctx.close_time_secs, 1),
            },
        )]


# ── 3. cluster_sum ─────────────────────────────────────────────────


class ClusterSumObserver(BaseObserver):
    name = "cluster_sum"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        atm_idx = ctx.atm_bucket_index()
        if atm_idx is None:
            return []
        atm_leg = ctx.b_legs[atm_idx]
        # Coverage gate: if the event's bucket range doesn't cover spot,
        # `atm_idx` resolves to the leg merely closest to spot — which may
        # still be far OTM. The resulting ±5 cluster is then an OTM zone
        # whose sum-to-1 math is meaningless. n=381 / 0% WR observed pre-fix.
        if abs(atm_leg.threshold - ctx.spot) / ctx.spot > CLUSTER_ATM_COVERAGE_MAX_PCT:
            return []
        lo = max(0, atm_idx - CLUSTER_RADIUS)
        hi = min(len(ctx.b_legs), atm_idx + CLUSTER_RADIUS + 1)
        cluster = ctx.b_legs[lo:hi]
        if len(cluster) < 5:
            return []
        sum_ya = sum(l.ya for l in cluster)
        sum_yb = sum(l.yb for l in cluster)

        signals: list[Signal] = []
        if sum_ya <= CLUSTER_SUM_UNDERPRICED_MAX:
            # Buy-yes arb: cluster YES asks sum to less than $1 → at least
            # one leg must resolve YES so the basket should pay ~$1.
            # Tag the lowest-ya leg as the marginal direction (the leg
            # most "free" to underprice).
            target = min(cluster, key=lambda l: l.ya)
            signals.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=target.ticker,
                predicted_outcome="yes",
                features={"sum_ya": round(sum_ya, 3),
                          "sum_yb": round(sum_yb, 3),
                          "kind": "underpriced",
                          "n_cluster": len(cluster),
                          "ya": target.ya, "na": target.na,
                          "spot": ctx.spot,
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        if sum_yb >= CLUSTER_SUM_OVERPRICED_MIN:
            # Buy-no arb: cluster YES bids sum to more than $1 → at most
            # one leg resolves YES, so most legs should resolve NO.
            target = max(cluster, key=lambda l: l.yb)
            signals.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=target.ticker,
                predicted_outcome="no",
                features={"sum_ya": round(sum_ya, 3),
                          "sum_yb": round(sum_yb, 3),
                          "kind": "overpriced",
                          "n_cluster": len(cluster),
                          "ya": target.ya, "na": target.na,
                          "spot": ctx.spot,
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return signals


# ── 4. bvt_consistency ─────────────────────────────────────────────


class BvTConsistencyObserver(BaseObserver):
    """For each T-threshold market (T_X = 'close ≥ X'), compare its ya
    against Σ ya(B_i) over ACTIVELY-QUOTED B-buckets with center ≥ X.
    They should be equal. ≥ $0.05 deviation suggests one side mispriced.

    Phantom-quote filter: Kalshi maintains a default ya≈$0.99/yb=$0 on
    inactive tail buckets. Summing those into Σ B trivially blows past
    $1.0 and yields false positives (same failure mode that retired
    ladder_crypto — see [[ladder-crypto-retired-2026-05-13]]). Require
    yb > 0 to count a leg as active.

    Prediction: if Σ B(≥X) > T_X (B sum says higher prob than T),
    predict T resolves YES (B sum is the more-reliable signal). Vice versa.
    """
    name = "bvt_consistency"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if not ctx.t_legs or not ctx.b_legs:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        out: list[Signal] = []
        for t in ctx.t_legs:
            # Extreme-price T quotes (e.g. T1530 ya=$0.01 with spot $2267)
            # are themselves phantom-quoted illiquids; firing on them
            # yields trivially-predictable signals that don't represent
            # alpha. Restrict to T markets whose ya looks live.
            if t.ya < BVT_T_YA_MIN or t.ya > BVT_T_YA_MAX:
                continue
            active = [l for l in ctx.b_legs
                      if l.threshold >= t.threshold and l.yb > 0]
            if not active:
                continue
            sum_b = sum(l.ya for l in active)
            if sum_b <= 0:
                continue
            dev = sum_b - t.ya
            if abs(dev) < BVT_MISALIGN_MIN:
                continue
            predicted = "yes" if dev > 0 else "no"
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=t.ticker,
                predicted_outcome=predicted,
                features={"t_ya": t.ya, "sum_b_ya": round(sum_b, 3),
                          "deviation": round(dev, 3),
                          "n_active_b": len(active),
                          "t_threshold": t.threshold,
                          "spot": ctx.spot,
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


# ── 5. spot_drift_momentum ─────────────────────────────────────────


class SpotDriftMomentumObserver(BaseObserver):
    """When spot moves ≥ 0.3% in the last 60s near close, predict that
    the most-aligned tail bucket (in drift direction) resolves YES."""
    name = "spot_drift_momentum"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.spot_60s_ago is None or ctx.spot_60s_ago <= 0:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs > SPOT_DRIFT_MTS_MAX_S:
            return []
        if ctx.close_time_secs <= 0:
            return []
        drift_pct = (ctx.spot - ctx.spot_60s_ago) / ctx.spot_60s_ago
        if abs(drift_pct) < SPOT_DRIFT_PCT_MIN:
            return []
        # Pick the closest B-bucket on the drift side.
        if drift_pct > 0:
            candidates = [l for l in ctx.b_legs if l.threshold > ctx.spot]
            target = min(candidates, key=lambda l: l.threshold,
                         default=None)
        else:
            candidates = [l for l in ctx.b_legs if l.threshold < ctx.spot]
            target = max(candidates, key=lambda l: l.threshold,
                         default=None)
        if target is None:
            return []
        # Avoid noise on phantom-quoted tails — require active market.
        if target.yb_size < 10 and target.ya_size < 10:
            return []
        return [Signal(
            observer=self.name, series=ctx.series,
            event_key=ctx.event_key, market_id=target.ticker,
            predicted_outcome="yes",
            features={"drift_pct": round(drift_pct, 5),
                      "spot": ctx.spot, "spot_60s_ago": ctx.spot_60s_ago,
                      "strike": target.threshold, "ya": target.ya,
                      "mts_s": round(ctx.close_time_secs, 1)},
        )]


# ── 6. open_widening ───────────────────────────────────────────────


class OpenWideningObserver(BaseObserver):
    """Within 10min of event open, target the ATM-region buckets and
    predict spot stays near ATM (the bucket touches). Tests the
    well-known event-open spread collapse pattern."""
    name = "open_widening"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.open_time_secs is None or ctx.open_time_secs < 0:
            return []
        if ctx.open_time_secs > OPEN_WIDENING_MAX_AGE_S:
            return []
        radius = OPEN_WIDENING_BUCKET_RADIUS_PCT * ctx.spot
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if abs(leg.threshold - ctx.spot) > radius:
                continue
            if leg.ya <= 0 or leg.ya >= 1:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="yes",
                features={"ya": leg.ya, "yb": leg.yb,
                          "strike": leg.threshold, "spot": ctx.spot,
                          "open_age_s": round(ctx.open_time_secs, 1)},
            ))
        return out


# ── 7. deribit_tail_skew ───────────────────────────────────────────


class DeribitTailSkewObserver(BaseObserver):
    """B-bucket tail where ya ≥ 3 × P_LN. Lognormal P from Deribit IV
    (already cached by the 4h cron). Sharper tail-only variant of B2."""
    name = "deribit_tail_skew"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.deribit_iv is None or ctx.deribit_iv <= 0:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        tau_years = ctx.close_time_secs / (365.25 * 86400)
        grid = ctx.bucket_grid()
        if grid is None or grid <= 0:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya < DERIBIT_TAIL_MIN_YA:
                continue
            if leg.yb_size < DERIBIT_TAIL_MIN_YB_SIZE:
                continue
            # P(S_T ∈ [strike - grid/2, strike + grid/2)) ≈
            # P(S_T ≥ strike - grid/2) - P(S_T ≥ strike + grid/2)
            p_above_lo = _lognormal_p_above(
                spot=ctx.spot, strike=leg.threshold - grid / 2,
                vol=ctx.deribit_iv, tau_years=tau_years,
            )
            p_above_hi = _lognormal_p_above(
                spot=ctx.spot, strike=leg.threshold + grid / 2,
                vol=ctx.deribit_iv, tau_years=tau_years,
            )
            if p_above_lo is None or p_above_hi is None:
                continue
            p_ln = max(0.0, p_above_lo - p_above_hi)
            # When p_ln is effectively zero, ya ≥ MIN_YA already represents
            # severe overpricing — fire without dividing by zero.
            if p_ln > 0 and leg.ya < DERIBIT_TAIL_RATIO * p_ln:
                continue
            ratio = (leg.ya / p_ln) if p_ln > 0 else float("inf")
            if ratio > DERIBIT_TAIL_RATIO_CAP:
                continue
            ratio_display = ratio if math.isfinite(ratio) else 1e6
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={"ya": leg.ya, "p_ln": round(p_ln, 6),
                          "ratio": round(ratio_display, 2),
                          "iv": ctx.deribit_iv,
                          "spot": ctx.spot, "strike": leg.threshold,
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


# ── 8. bid_imbalance ───────────────────────────────────────────────


class BidImbalanceObserver(BaseObserver):
    """Top-of-book size imbalance near close. If (yb_size + nb_size) /
    (ya_size + na_size) ≥ 3, predict YES will resolve YES (assuming
    bids represent stronger conviction than asks — testable hypothesis,
    could go either way)."""
    name = "bid_imbalance"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > BID_IMBALANCE_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            bid_total = leg.yb_size + leg.nb_size
            ask_total = leg.ya_size + leg.na_size
            if bid_total < 50 or ask_total <= 0:
                continue
            ratio = bid_total / ask_total
            if ratio < BID_IMBALANCE_RATIO_MIN:
                continue
            # Side: where do bids cluster? If yb_size > nb_size, bids
            # lean YES → predict YES. Else predict NO.
            predicted = "yes" if leg.yb_size > leg.nb_size else "no"
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome=predicted,
                features={"yb_size": leg.yb_size, "nb_size": leg.nb_size,
                          "ya_size": leg.ya_size, "na_size": leg.na_size,
                          "ratio": round(ratio, 2),
                          "ya": leg.ya, "na": leg.na,
                          "spot": ctx.spot, "strike": leg.threshold,
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


# ── 9. late_close_tail ─────────────────────────────────────────────


class LateCloseTailObserver(BaseObserver):
    """Mid-OTM tail observer for the close window. Broader sibling of
    tail_decay: 10-min window, 0.5% dist floor, ya in [0.05, 0.30].

    Hypothesis: deep-OTM strikes don't get touched in the last 10 min of
    a crypto hourly. tail_decay covers the ≤$0.05 zone; deribit_tail_fade
    covers the lognormal-ratio overpricing zone. This observer covers the
    gap where ya is large enough to have non-trivial payoff geometry but
    still pure-OTM (≥0.5% from spot)."""
    name = "late_close_tail"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None or ctx.close_time_secs > LATE_CLOSE_TAIL_MTS_MAX_S:
            return []
        if ctx.close_time_secs <= 0:
            return []
        out: list[Signal] = []
        # B-bucketed (hourly) AND T-cumulative (daily) markets both express
        # OTM-no-touch in the same way: low ya at a strike far from spot in
        # the close window → BUY NO. Iterate both leg lists with the same
        # gates; leg.kind in features lets us slice the cohort post-hoc.
        for leg in (*ctx.b_legs, *ctx.t_legs):
            if leg.ya < LATE_CLOSE_TAIL_YA_MIN or leg.ya > LATE_CLOSE_TAIL_YA_MAX:
                continue
            if leg.yb_size < LATE_CLOSE_TAIL_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct < LATE_CLOSE_TAIL_DIST_MIN_PCT:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={
                    "ya": leg.ya, "yb": leg.yb,
                    "na": leg.na,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                    "kind": leg.kind,
                },
            ))
        return out


# ── 9b. mid_otm_tail (2026-05-20) ──────────────────────────────────


class MidOtmTailObserver(BaseObserver):
    """Wider-time sibling of late_close_tail covering the fee-positive na band.

    Same OTM-no-touch thesis as late_close_tail, observed earlier in the
    contract (10 min - 2 hr to close) before the market converges to
    na ~0.98. Kalshi fee is capped at (1-na) per contract, so at na ≥0.93
    fees eat 100% of gross profit per win — late_close_tail's natural fire
    zone is structurally net-zero even at 100% WR. By observing the same
    setup earlier, we capture the same time-decay effect at lower na where
    per-win net is materially positive. Phase 0 only — Phase 1 promotion
    requires bucket-level WR analysis after ≥1 week of data."""
    name = "mid_otm_tail"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.close_time_secs is None:
            return []
        if ctx.close_time_secs <= MID_OTM_TAIL_MTS_MIN_S:
            return []
        if ctx.close_time_secs > MID_OTM_TAIL_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in (*ctx.b_legs, *ctx.t_legs):
            if leg.ya < MID_OTM_TAIL_YA_MIN or leg.ya > MID_OTM_TAIL_YA_MAX:
                continue
            if leg.yb_size < MID_OTM_TAIL_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct < MID_OTM_TAIL_DIST_MIN_PCT:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={
                    "ya": leg.ya, "yb": leg.yb,
                    "na": leg.na,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                    "kind": leg.kind,
                },
            ))
        return out


# ── 10. deribit_atm_skew ───────────────────────────────────────────


class DeribitAtmSkewObserver(BaseObserver):
    """ATM-bucket parallel of DeribitTailSkewObserver. Fires when Kalshi
    ATM yes_ask exceeds the Deribit-IV lognormal probability by ≥ 1.5×.

    The tail variant (DeribitTailSkewObserver) gates on ratio ≥ 3 and ya
    ≥ $0.03 — appropriate for deep-OTM where P_LN is small and any
    overshoot signals overpricing. For the ATM bucket P_LN is large
    (often 0.15-0.40), so 3× would require ya > 1 to fire; we use 1.5×.
    Predicts "no": the bet is the ATM bucket does NOT resolve YES (i.e.,
    spot moves out of the ATM range)."""
    name = "deribit_atm_skew"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.deribit_iv is None or ctx.deribit_iv <= 0:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > DERIBIT_ATM_MTS_MAX_S:
            return []
        grid = ctx.bucket_grid()
        if grid is None or grid <= 0:
            return []
        atm_idx = ctx.atm_bucket_index()
        if atm_idx is None:
            return []
        leg = ctx.b_legs[atm_idx]
        if abs(leg.threshold - ctx.spot) > DERIBIT_ATM_BUCKET_RADIUS * grid:
            return []
        if leg.ya < DERIBIT_ATM_MIN_YA or leg.ya > DERIBIT_ATM_MAX_YA:
            return []
        if leg.yb_size < DERIBIT_ATM_MIN_YB_SIZE:
            return []
        tau_years = ctx.close_time_secs / (365.25 * 86400)
        p_above_lo = _lognormal_p_above(
            spot=ctx.spot, strike=leg.threshold - grid / 2,
            vol=ctx.deribit_iv, tau_years=tau_years,
        )
        p_above_hi = _lognormal_p_above(
            spot=ctx.spot, strike=leg.threshold + grid / 2,
            vol=ctx.deribit_iv, tau_years=tau_years,
        )
        if p_above_lo is None or p_above_hi is None:
            return []
        p_ln = max(0.0, p_above_lo - p_above_hi)
        if p_ln <= 0:
            return []
        if leg.ya < DERIBIT_ATM_RATIO * p_ln:
            return []
        ratio = leg.ya / p_ln
        return [Signal(
            observer=self.name, series=ctx.series,
            event_key=ctx.event_key, market_id=leg.ticker,
            predicted_outcome="no",
            features={"ya": leg.ya, "na": leg.na,
                      "p_ln": round(p_ln, 6),
                      "ratio": round(ratio, 2),
                      "iv": ctx.deribit_iv,
                      "spot": ctx.spot, "strike": leg.threshold,
                      "grid": grid,
                      "mts_s": round(ctx.close_time_secs, 1)},
        )]


# ── Flipped-prediction variants (2026-05-14) ───────────────────────
#
# Phase 0 cohort analysis on 24h data revealed that two observers' WR
# was strongly under 50% — meaning the OPPOSITE prediction would have
# been right most of the time. Rather than mutating the originals (which
# would break the WR comparison data) we add flipped sibling observers
# that share the trigger logic via delegation. Each emits its own row
# in `crypto_observer_signals` with `predicted_outcome` flipped.
#
# - open_widening    (orig n=325, 20% WR) → open_no_touch (predict "no")
# - atm_fade         (orig n=10,  0% WR)  → atm_hold      (predict "yes")
#
# Phase 1 paper traders for these live in crypto_observer_phase1.py.


def _flip(outcome: str) -> str:
    return "yes" if outcome == "no" else "no"


class OpenNoTouchObserver(BaseObserver):
    """Predicts no-touch on ATM±1% buckets within 10min of event open.
    Inverse of OpenWideningObserver — 24h cohort showed 80% WR for this
    direction. Reuses OpenWideningObserver's trigger logic via delegation."""
    name = "open_no_touch"

    def __init__(self):
        self._inner = OpenWideningObserver()

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        return [
            Signal(
                observer=self.name, series=s.series,
                event_key=s.event_key, market_id=s.market_id,
                predicted_outcome=_flip(s.predicted_outcome),
                features=s.features,
            )
            for s in self._inner.evaluate(ctx)
        ]


class AtmHoldObserver(BaseObserver):
    """Predicts the ATM bucket holds (resolves YES) when ya is in the mid
    band (0.40-0.65). Inverse of AtmFadeObserver — pre-fix cohort showed
    0% WR for "no-touch", implying the ATM bucket actually does touch."""
    name = "atm_hold"

    def __init__(self):
        self._inner = AtmFadeObserver()

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        return [
            Signal(
                observer=self.name, series=s.series,
                event_key=s.event_key, market_id=s.market_id,
                predicted_outcome=_flip(s.predicted_outcome),
                features=s.features,
            )
            for s in self._inner.evaluate(ctx)
        ]


class BidImbalanceFadeObserver(BaseObserver):
    """Conditional-flip of BidImbalanceObserver for the ya<0.60 fade zone.

    Phase 0 cohort (2026-05-15, n=266 settled bid_imbalance signals)
    revealed the 5/14 promotion cohort (n=26 @ 69% WR) was an ATM-band
    fluke — the real distribution is 36% WR original / 63.9% WR flipped.
    Cheap-YES bid stacking is a retail longshot indicator: above ya 0.60
    the original direction still wins (>72% WR), below it the flip wins
    (~83% WR n=141). This observer fires only in the fade zone and emits
    the opposite predicted_outcome; the rich end is left to the original
    BidImbalanceObserver.
    """
    name = "bid_imbalance_fade"

    def __init__(self):
        self._inner = BidImbalanceObserver()

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        out: list[Signal] = []
        for s in self._inner.evaluate(ctx):
            ya = s.features.get("ya")
            if ya is None or ya >= BID_IMBALANCE_FADE_MAX_YA:
                continue
            out.append(Signal(
                observer=self.name, series=s.series,
                event_key=s.event_key, market_id=s.market_id,
                predicted_outcome=_flip(s.predicted_outcome),
                features=s.features,
            ))
        return out


class EtfFlowLagObserver(BaseObserver):
    """Phase 0: spot ETF volume-z-score as directional signal.

    When |signed volume z| ≥ ETF_FLOW_MIN_ABS_Z (1.5σ over 20-day baseline,
    with sign from same-day price direction), ETF demand is one-sided.
    Phase 0 thesis: this leads Kalshi binary pricing.

      etf_flow_z > 0 (heavy buying)    → fade DOWNSIDE strikes (predict "no")
      etf_flow_z < 0 (heavy selling)   → fade UPSIDE strikes (predict "no")
    """
    name = "etf_flow_lag"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.etf_flow_z is None:
            return []
        if abs(ctx.etf_flow_z) < ETF_FLOW_MIN_ABS_Z:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > ETF_FLOW_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya < ETF_FLOW_MIN_YA:
                continue
            if leg.yb_size < ETF_FLOW_MIN_YB_SIZE:
                continue
            # Spread gate: ya + na - 1.0 = implied half-spread × 2. Without
            # this, observer fires on 11c-spread markets where neither side
            # is tradable.
            if (leg.ya + leg.na - 1.0) > CROSS_VENUE_MAX_SPREAD:
                continue
            # bullish flow (z > 0) → fade strikes BELOW spot
            # bearish flow (z < 0) → fade strikes ABOVE spot
            if ctx.etf_flow_z > 0 and leg.threshold >= ctx.spot:
                continue
            if ctx.etf_flow_z < 0 and leg.threshold <= ctx.spot:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={"ya": leg.ya, "no_ask": leg.na,
                          "etf_flow_z": round(ctx.etf_flow_z, 3),
                          "spot": ctx.spot, "strike": leg.threshold,
                          "side": "fade_downside" if ctx.etf_flow_z > 0 else "fade_upside",
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


class CmeBasisFadeObserver(BaseObserver):
    """Phase 0: CME futures basis (contango/backwardation) as directional.

    When |basis| ≥ CME_BASIS_MIN_ABS (~0.2%), futures pricing is structurally
    skewed. Phase 0 thesis: pro futures market leads Kalshi pricing.

      basis > 0 (contango, futures > spot)        → fade DOWN-strikes
      basis < 0 (backwardation, futures < spot)   → fade UP-strikes
    """
    name = "cme_basis_fade"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.cme_basis is None:
            return []
        if abs(ctx.cme_basis) < CME_BASIS_MIN_ABS:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > CME_BASIS_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya < CME_BASIS_MIN_YA:
                continue
            if leg.yb_size < CME_BASIS_MIN_YB_SIZE:
                continue
            if (leg.ya + leg.na - 1.0) > CROSS_VENUE_MAX_SPREAD:
                continue
            if ctx.cme_basis > 0 and leg.threshold >= ctx.spot:
                continue
            if ctx.cme_basis < 0 and leg.threshold <= ctx.spot:
                continue
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={"ya": leg.ya, "no_ask": leg.na,
                          "cme_basis": round(ctx.cme_basis, 5),
                          "spot": ctx.spot, "strike": leg.threshold,
                          "side": "fade_downside" if ctx.cme_basis > 0 else "fade_upside",
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


class FundingRateFadeObserver(BaseObserver):
    """Phase 0: Deribit perp funding rate signal.

    When |funding_8h| ≥ FUNDING_RATE_MIN_ABS, the perpetual market is
    heavily one-sided (longs paying shorts or vice versa). Historical
    crypto literature suggests this precedes mean reversion. Observer
    just records the signal — predicted_outcome convention:

      funding > 0  (longs paying)  → fade UPSIDE  → for strike > spot,
                                      Kalshi YES bet says "BTC up";
                                      predict "no" (BTC won't go up that far)
      funding < 0  (shorts paying) → fade DOWNSIDE → for strike < spot,
                                      Kalshi YES bet says "BTC down";
                                      predict "no" (BTC won't fall that far)

    Direction may be flipped after 1-2 weeks of Phase 0 data if WR < 50%.
    """
    name = "funding_rate_fade"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.funding_8h is None:
            return []
        if abs(ctx.funding_8h) < FUNDING_RATE_MIN_ABS:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > FUNDING_RATE_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya < FUNDING_RATE_MIN_YA:
                continue
            if leg.yb_size < FUNDING_RATE_MIN_YB_SIZE:
                continue
            # Which side of spot does this strike sit on?
            if ctx.funding_8h > 0 and leg.threshold <= ctx.spot:
                continue  # need upside strike to fade upside
            if ctx.funding_8h < 0 and leg.threshold >= ctx.spot:
                continue  # need downside strike to fade downside
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="no",
                features={"ya": leg.ya, "no_ask": leg.na,
                          "funding_8h": round(ctx.funding_8h, 6),
                          "spot": ctx.spot, "strike": leg.threshold,
                          "side": "fade_upside" if ctx.funding_8h > 0 else "fade_downside",
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


class VolSkewFadeObserver(BaseObserver):
    """Phase 0: option-market put-call IV asymmetry.

    When |put_IV - call_IV| ≥ VOL_SKEW_MIN_ABS (~5 IV pts), the option
    market is pricing strong directional fear (put-skew) or upside
    chase (call-skew). Phase 0 thesis: mean reversion. Observer convention:

      iv_skew > 0  (put-skew, downside fear)  → fade DOWNSIDE
      iv_skew < 0  (call-skew, upside chase) → fade UPSIDE

    Independent from deribit_tail_skew (single-strike lognormal) — this
    looks at the full smile shape.
    """
    name = "vol_skew_fade"

    def evaluate(self, ctx: EventContext) -> list[Signal]:
        if ctx.iv_skew is None:
            return []
        if abs(ctx.iv_skew) < VOL_SKEW_MIN_ABS:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs <= 0:
            return []
        if ctx.close_time_secs > VOL_SKEW_MTS_MAX_S:
            return []
        out: list[Signal] = []
        for leg in ctx.b_legs:
            if leg.ya < VOL_SKEW_MIN_YA:
                continue
            if leg.yb_size < VOL_SKEW_MIN_YB_SIZE:
                continue
            if (leg.ya + leg.na - 1.0) > CROSS_VENUE_MAX_SPREAD:
                continue
            # iv_skew > 0 (put-skew, downside fear) → fade strikes BELOW spot
            # iv_skew < 0 (call-skew, upside chase) → fade strikes ABOVE spot
            if ctx.iv_skew > 0 and leg.threshold >= ctx.spot:
                continue
            if ctx.iv_skew < 0 and leg.threshold <= ctx.spot:
                continue
            # 5/19 PM ROI audit: predicted_outcome was "no" — bet against the
            # OTM bucket, paying ~0.94 to win 0.06. WR 67% with BE ~80% lost
            # money. Flipped to "yes": pays ya~0.25 on the underpriced side.
            # Spread-gated cohort at ≤3c showed +7% sim ROI; gate now tightens
            # to ≤5c so going-forward direction matches docstring "fade" intent
            # (bet against the fear, buy the underpriced upside on put-skew /
            # underpriced downside on call-skew).
            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="yes",
                features={"ya": leg.ya, "no_ask": leg.na,
                          "iv_skew": round(ctx.iv_skew, 6),
                          "spot": ctx.spot, "strike": leg.threshold,
                          "side": "fade_downside" if ctx.iv_skew > 0 else "fade_upside",
                          "mts_s": round(ctx.close_time_secs, 1)},
            ))
        return out


OBSERVER_CLASSES: tuple[type[BaseObserver], ...] = (
    TailDecayObserver, AtmFadeObserver, ClusterSumObserver,
    BvTConsistencyObserver, SpotDriftMomentumObserver,
    OpenWideningObserver, DeribitTailSkewObserver, BidImbalanceObserver,
    # Flipped siblings
    OpenNoTouchObserver, AtmHoldObserver, BidImbalanceFadeObserver,
    # 2026-05-18 additions: gap-fill / new-hypothesis observers
    LateCloseTailObserver, DeribitAtmSkewObserver,
    # 2026-05-20: fee-positive zone explorer (paired with late_close_tail)
    MidOtmTailObserver,
    # 2026-05-19 Phase 0 additions: cross-venue Deribit signals
    FundingRateFadeObserver, VolSkewFadeObserver,
    # 2026-05-19 Phase 0: external-data signals (yfinance ETF + CME)
    EtfFlowLagObserver, CmeBasisFadeObserver,
)


# ── Harness ────────────────────────────────────────────────────────


def _build_leg(m: dict) -> Optional[Leg]:
    ticker = m.get("ticker", "")
    if not ticker:
        return None
    suffix = ticker.rsplit("-", 1)[-1]
    kind = _suffix_kind(suffix)
    if kind is None:
        return None
    threshold = _parse_suffix_threshold(suffix)
    if threshold is None:
        return None
    ya = _safe_float(m.get("yes_ask_dollars"))
    yb = _safe_float(m.get("yes_bid_dollars"))
    na = _safe_float(m.get("no_ask_dollars"))
    nb = _safe_float(m.get("no_bid_dollars"))
    if ya is None or yb is None or na is None or nb is None:
        return None
    return Leg(
        ticker=ticker, kind=kind, threshold=threshold,
        ya=ya, yb=yb, na=na, nb=nb,
        ya_size=_safe_float(m.get("yes_ask_size_fp")) or 0.0,
        yb_size=_safe_float(m.get("yes_bid_size_fp")) or 0.0,
        na_size=_safe_float(m.get("no_ask_size_fp")) or 0.0,
        nb_size=_safe_float(m.get("no_bid_size_fp")) or 0.0,
        liquidity=_safe_float(m.get("liquidity_dollars")) or 0.0,
        raw=m,
    )


class CryptoObserverStrategy:
    """Scan-loop adapter. Runs all 8 observers per event per scan tick;
    records non-duplicate signals to `crypto_observer_signals`.
    """
    STRATEGY_NAME = "crypto_observer"

    def __init__(self, config, order_manager, market_scanner, kalshi_client,
                 *, price_feed):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._price_feed = price_feed
        self._observers = [cls() for cls in OBSERVER_CLASSES]
        # Cooldown: (observer_name, market_id) -> last fire epoch
        self._last_fire: dict[tuple[str, str], float] = {}
        # Per-asset spot history for spot_60s_ago lookups
        # (PriceFeed has its own deque but resolution-time access is awkward.)
        # 2026-05-20: maxlen extended 180 → 1800 to cover 15-min correlation
        # window for cross-asset observers. Memory: ~57 KB total (1800 entries
        # × 16 bytes × 2 assets), negligible.
        self._spot_history: dict[str, deque] = {
            "BTC": deque(maxlen=1800),  # ~30 min at 1Hz
            "ETH": deque(maxlen=1800),
        }
        # 2026-05-20: rolling bucket-mid cache for kalshi_book_lag observer.
        from strategies.crypto_cross_asset_observer import _BookMidCache
        self._book_mid_cache = _BookMidCache()
        self._last_book_prune_ts = 0.0

    def _update_spot_history(self, now: float):
        for asset in ("BTC", "ETH"):
            p = self._price_feed.get_fresh_spot(asset)
            if p is not None and p > 0:
                self._spot_history[asset].append((now, p))

    def _spot_n_seconds_ago(self, asset: str, secs: float, now: float
                            ) -> Optional[float]:
        hist = self._spot_history.get(asset)
        if not hist:
            return None
        target = now - secs
        # Find the most recent entry with ts <= target
        best = None
        for ts, p in hist:
            if ts <= target:
                best = p
            else:
                break
        return best

    def _correlation_lookback(self, window_secs: float, now_ts: float
                              ) -> Optional[float]:
        """BTC-ETH Pearson correlation over the last `window_secs` seconds.

        Joins BTC and ETH spot streams on nearest timestamp (within 5s
        tolerance). Returns None on sparse data (< CORR_MIN_SAMPLES) per
        _pearson_correlation contract.
        """
        from strategies.crypto_cross_asset_observer import _pearson_correlation
        btc_hist = self._spot_history.get("BTC")
        eth_hist = self._spot_history.get("ETH")
        if not btc_hist or not eth_hist:
            return None
        cutoff = now_ts - window_secs
        btc_window = [(ts, p) for ts, p in btc_hist if ts >= cutoff]
        eth_window = [(ts, p) for ts, p in eth_hist if ts >= cutoff]
        # Join on nearest ts (5s tolerance). Walk both sorted lists.
        btc_window.sort(key=lambda x: x[0])
        eth_window.sort(key=lambda x: x[0])
        # One-to-one timestamp join: each ETH sample can match at most one BTC
        # sample. Tolerance: 5s (within-cadence noise on binance.us 60s kline).
        # j is the next-available ETH index — never goes backward.
        xs: list[float] = []
        ys: list[float] = []
        j = 0
        for bts, bp in btc_window:
            # Advance j past ETH samples too old to match this BTC's window
            # (5s tolerance) — they can't be closer than the next sample.
            while j < len(eth_window) and (bts - eth_window[j][0]) > 5.0:
                j += 1
            if j >= len(eth_window):
                break
            # If next ETH is closer (forward in time), prefer it
            while j + 1 < len(eth_window) and abs(eth_window[j + 1][0] - bts) < abs(eth_window[j][0] - bts):
                j += 1
            if abs(eth_window[j][0] - bts) <= 5.0:
                xs.append(bp)
                ys.append(eth_window[j][1])
                j += 1  # Consume — each ETH sample used at most once
        return _pearson_correlation(xs, ys)

    async def _deribit_iv(self, asset: str) -> Optional[float]:
        """Return ATM IV as a decimal (e.g. 0.60 for 60% annualized).

        Deribit /public/get_book_summary_by_currency returns mark_iv as a
        percentage (e.g. 60.0 means 60%). The deribit_iv_cron stores that
        raw value in crypto_iv.atm_iv, so the observer divides by 100 here.
        Note: B2 `crypto_fair_value` reads the same column without dividing,
        which is a known issue tracked separately — observer-side fix is
        independent.
        """
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            return None
        try:
            row = await db.fetch_nearest_crypto_iv(asset)
        except Exception:
            return None
        if not row:
            return None
        raw = _safe_float(row.get("atm_iv"))
        if raw is None or raw <= 0:
            return None
        return raw / 100.0

    def _build_contexts(self, markets: list[dict], now: datetime,
                        now_epoch: float) -> list[EventContext]:
        # Group by (series, event_key)
        by_event: dict[tuple[str, str], list[Leg]] = {}
        for m in markets:
            t = m.get("ticker", "")
            series = t.split("-")[0]
            if series not in TARGET_SERIES:
                continue
            leg = _build_leg(m)
            if leg is None:
                continue
            ev = _event_key(t)
            if not ev:
                continue
            by_event.setdefault((series, ev), []).append(leg)

        # 2026-05-20: cross-asset features computed ONCE per scan (not per event).
        spot_btc_now = self._price_feed.get_fresh_spot("BTC")
        spot_eth_now = self._price_feed.get_fresh_spot("ETH")
        spot_btc_30s = self._spot_n_seconds_ago("BTC", 30.0, now_epoch)
        spot_eth_30s = self._spot_n_seconds_ago("ETH", 30.0, now_epoch)
        spot_btc_10s = self._spot_n_seconds_ago("BTC", 10.0, now_epoch)
        spot_eth_10s = self._spot_n_seconds_ago("ETH", 10.0, now_epoch)
        corr_5min = self._correlation_lookback(300.0, now_epoch)
        corr_15min = self._correlation_lookback(900.0, now_epoch)

        contexts: list[EventContext] = []
        for (series, ev), legs in by_event.items():
            asset = SERIES_ASSET.get(series)
            if asset is None:
                continue
            spot = self._price_feed.get_fresh_spot(asset)
            if spot is None or spot <= 0:
                continue
            b_legs = sorted([l for l in legs if l.kind == "bucketed"],
                            key=lambda l: l.threshold)
            t_legs = sorted([l for l in legs if l.kind == "cumulative"],
                            key=lambda l: l.threshold)
            # Representative timing from first leg's raw dict
            rep = legs[0].raw
            close_s = _close_time_secs(rep, now)
            open_s = _open_age_secs(rep, now)
            spot_60s = self._spot_n_seconds_ago(asset, SPOT_DRIFT_LOOKBACK_S,
                                                now_epoch)
            contexts.append(EventContext(
                series=series, asset=asset, event_key=ev,
                now=now, spot=spot, spot_60s_ago=spot_60s,
                deribit_iv=None,  # filled async below
                b_legs=b_legs, t_legs=t_legs,
                open_time_secs=open_s, close_time_secs=close_s,
                spot_btc=spot_btc_now, spot_eth=spot_eth_now,
                spot_btc_30s_ago=spot_btc_30s, spot_eth_30s_ago=spot_eth_30s,
                spot_btc_10s_ago=spot_btc_10s, spot_eth_10s_ago=spot_eth_10s,
                corr_5min=corr_5min, corr_15min=corr_15min,
                book_mid_cache=self._book_mid_cache,
            ))
        return contexts

    async def scan_and_execute(self):
        # Observer is read-only: no circuit-breaker / kill switch needed.
        markets = self._scanner.get_active_markets()
        # 2026-05-19: throttled gate-by-gate diagnostic (1 log per 5 min) so we
        # can detect another silent-fail without redeploying. Cheap enough to
        # keep on permanently — 1 WARNING line every 5 min is noise-free.
        debug_now = time.time()
        last_debug = getattr(self, "_last_debug_log", 0.0)
        debug = (debug_now - last_debug) >= 300.0
        if debug:
            self._last_debug_log = debug_now
        if not markets:
            if debug:
                logger.warning("[OBS_DEBUG] markets=0 -> early return")
            return
        now = datetime.now(timezone.utc)
        now_epoch = time.time()
        self._update_spot_history(now_epoch)
        # 2026-05-20: cross-asset bookkeeping.
        for m in markets:
            t = m.get("ticker", "")
            series = t.split("-")[0]
            if series not in TARGET_SERIES:
                continue
            ya = _safe_float(m.get("yes_ask_dollars"))
            yb = _safe_float(m.get("yes_bid_dollars"))
            if ya is None or yb is None:
                continue
            suffix = t.rsplit("-", 1)[-1]
            kind = _suffix_kind(suffix)
            if kind != "bucketed":
                continue
            strike = _parse_suffix_threshold(suffix)
            if strike is None:
                continue
            mid = (ya + yb) / 2.0
            flushed = self._book_mid_cache.update(series, strike, now_epoch, mid)
            if flushed:
                logger.error(
                    "[BOOK_MID_CACHE_FLUSH] hard cap hit — alert ops"
                )
        # Prune stale book entries every 60s
        if (now_epoch - self._last_book_prune_ts) >= 60.0:
            removed = self._book_mid_cache.prune_stale(now_epoch, max_age_s=120.0)
            self._last_book_prune_ts = now_epoch
            if debug:
                logger.warning(
                    f"[BOOK_MID_CACHE_PRUNE] size={self._book_mid_cache.size()} "
                    f"removed={removed}"
                )
        # Soft watchdog
        if self._book_mid_cache.size() > 10_000:
            if debug:
                logger.warning(
                    f"[BOOK_MID_CACHE_SIZE_WARN] size={self._book_mid_cache.size()} "
                    f"approaching hard cap"
                )

        if debug:
            from collections import Counter as _Counter
            series_cnt = _Counter(m.get("ticker", "").split("-")[0] for m in markets)
            target_cnt = sum(1 for m in markets
                             if m.get("ticker", "").split("-")[0] in TARGET_SERIES)
            stale_info = {a: (self._price_feed.get_fresh_spot(a),
                              self._price_feed.is_spot_stale(a),
                              self._price_feed.is_stale(a),
                              self._price_feed.is_mid_stale(a))
                          for a in ("BTC", "ETH")}
            logger.warning(
                f"[OBS_DEBUG] markets={len(markets)} target_series={target_cnt} "
                f"by_series={dict(series_cnt)} stale={stale_info}"
            )

        contexts = self._build_contexts(markets, now, now_epoch)
        if not contexts:
            if debug:
                logger.warning("[OBS_DEBUG] contexts=0 -> early return")
            return
        if debug:
            ctx_info = [(c.series, c.event_key, len(c.b_legs), len(c.t_legs),
                         round(c.spot, 2)) for c in contexts]
            logger.warning(f"[OBS_DEBUG] contexts={len(contexts)} {ctx_info}")

        # Resolve Deribit IV once per asset (cached DB call)
        iv_by_asset: dict[str, Optional[float]] = {}
        funding_by_asset: dict[str, Optional[float]] = {}
        skew_by_asset: dict[str, Optional[float]] = {}
        for ctx in contexts:
            if ctx.asset not in iv_by_asset:
                iv_by_asset[ctx.asset] = await self._deribit_iv(ctx.asset)
            ctx.deribit_iv = iv_by_asset[ctx.asset]
            # 2026-05-19: Phase 0 additions. TTL-cached fetches inside the
            # helpers — safe to call every scan tick.
            if ctx.asset not in funding_by_asset:
                try:
                    from core.deribit_extras import get_funding_rate, get_iv_skew
                    funding_by_asset[ctx.asset] = get_funding_rate(ctx.asset)
                    skew_by_asset[ctx.asset] = get_iv_skew(ctx.asset)
                except Exception as e:
                    logger.warning(f"deribit_extras fetch failed {ctx.asset}: {e}")
                    funding_by_asset[ctx.asset] = None
                    skew_by_asset[ctx.asset] = None
            ctx.funding_8h = funding_by_asset[ctx.asset]
            ctx.iv_skew = skew_by_asset[ctx.asset]
            # 2026-05-19: yfinance-backed external-data observers.
            # TTL-cached 10 min — daily data, ok to call every scan.
            try:
                from core.market_extras import get_etf_flow_zscore, get_cme_basis
                ctx.etf_flow_z = get_etf_flow_zscore(ctx.asset)
                ctx.cme_basis = get_cme_basis(ctx.asset)
            except Exception as e:
                logger.warning(f"market_extras fetch failed {ctx.asset}: {e}")
                ctx.etf_flow_z = None
                ctx.cme_basis = None

        db = getattr(self._order_manager, "_db", None)
        if db is None:
            if debug:
                logger.warning("[OBS_DEBUG] db is None -> early return")
            return
        if debug:
            feat = {a: (funding_by_asset.get(a), skew_by_asset.get(a),
                        getattr(next((c for c in contexts if c.asset == a), None),
                                "etf_flow_z", None),
                        getattr(next((c for c in contexts if c.asset == a), None),
                                "cme_basis", None))
                    for a in ("BTC", "ETH")}
            logger.warning(f"[OBS_DEBUG] features {{asset: (funding, skew, etf_z, cme_basis)}} = {feat}")

        # Per-tick counters for debug
        n_emitted = 0
        n_cooldown = 0
        n_recorded = 0
        n_db_err = 0
        by_obs: dict[str, int] = {}
        for ctx in contexts:
            for obs in self._observers:
                try:
                    signals = obs.evaluate(ctx)
                except Exception as e:
                    logger.error(f"observer {obs.name} crashed: {e}")
                    continue
                if signals:
                    by_obs[obs.name] = by_obs.get(obs.name, 0) + len(signals)
                for sig in signals:
                    n_emitted += 1
                    cooldown_key = (sig.observer, sig.market_id)
                    last = self._last_fire.get(cooldown_key)
                    if last is not None and (now_epoch - last) < SIGNAL_COOLDOWN_S:
                        n_cooldown += 1
                        continue
                    try:
                        await db.record_crypto_observer_signal(
                            observer=sig.observer, series=sig.series,
                            event_key=sig.event_key, market_id=sig.market_id,
                            predicted_outcome=sig.predicted_outcome,
                            features_json=json.dumps(sig.features,
                                                    sort_keys=True),
                        )
                        self._last_fire[cooldown_key] = now_epoch
                        n_recorded += 1
                    except Exception as e:
                        n_db_err += 1
                        logger.error(f"record_crypto_observer_signal failed: {e}")
        if debug:
            logger.warning(
                f"[OBS_DEBUG] tick_done emitted={n_emitted} recorded={n_recorded} "
                f"cooldown={n_cooldown} db_err={n_db_err} by_observer={by_obs}"
            )

    async def scan_loop(self, scan_interval_secs: float = 1.0):
        """Dedicated fast-scan task — bypasses the main strategy loop so the
        17-observer pipeline isn't bottlenecked by slow sports portfolio
        scans. Pattern mirrors crypto_fair_value.scan_loop. 1 s cadence
        matches the BTC/ETH market freshness window; observer-side
        SIGNAL_COOLDOWN_S (60 s) handles cooldown dedup.
        """
        while True:
            try:
                await self.scan_and_execute()
            except Exception as e:
                logger.error(f"crypto_observer scan_loop error: {e}")
            await asyncio.sleep(scan_interval_secs)
