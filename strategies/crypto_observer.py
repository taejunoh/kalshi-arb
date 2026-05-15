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

TARGET_SERIES = frozenset({"KXBTC", "KXETH"})
SERIES_ASSET = {"KXBTC": "BTC", "KXETH": "ETH"}

# Tunables — all observers use the same trigger thresholds for the
# initial 1-week sweep. Post-data they're independently re-tuned.
TAIL_DECAY_MTS_MAX_S = 300            # ≤5 min to close
TAIL_DECAY_DIST_MIN_PCT = 0.010       # ≥1.0% from spot
TAIL_DECAY_YA_MAX = 0.05              # yes_ask ≤ $0.05
TAIL_DECAY_YB_SIZE_MIN = 50           # active bid present

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

BID_IMBALANCE_RATIO_MIN = 3.0         # (yb+nb) / (ya+na) sizes ≥ 3
BID_IMBALANCE_MTS_MAX_S = 600         # ≤ 10 min to close
BID_IMBALANCE_FADE_MAX_YA = 0.60      # flipped sibling only fires below this ya —
                                       # Phase 0 cohort 2026-05-15 (n=266) showed
                                       # full-flip 63.9% WR but ya<0.60 sub-cohort
                                       # 83% WR (n=141). Above 0.60 the original
                                       # direction still wins, so the flipped
                                       # observer stays out of that zone.

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


OBSERVER_CLASSES: tuple[type[BaseObserver], ...] = (
    TailDecayObserver, AtmFadeObserver, ClusterSumObserver,
    BvTConsistencyObserver, SpotDriftMomentumObserver,
    OpenWideningObserver, DeribitTailSkewObserver, BidImbalanceObserver,
    # Flipped siblings
    OpenNoTouchObserver, AtmHoldObserver, BidImbalanceFadeObserver,
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
        self._spot_history: dict[str, deque] = {
            "BTC": deque(maxlen=180),  # ~3 min at 1Hz
            "ETH": deque(maxlen=180),
        }

    def _update_spot_history(self, now: float):
        for asset in ("BTC", "ETH"):
            p = self._price_feed.get_price(asset)
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

        contexts: list[EventContext] = []
        for (series, ev), legs in by_event.items():
            asset = SERIES_ASSET.get(series)
            if asset is None:
                continue
            spot = self._price_feed.get_price(asset)
            if spot is None or spot <= 0:
                continue
            if self._price_feed.is_stale(asset):
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
            ))
        return contexts

    async def scan_and_execute(self):
        # Observer is read-only: no circuit-breaker / kill switch needed.
        markets = self._scanner.get_active_markets()
        if not markets:
            return
        now = datetime.now(timezone.utc)
        now_epoch = time.time()
        self._update_spot_history(now_epoch)

        contexts = self._build_contexts(markets, now, now_epoch)
        if not contexts:
            return

        # Resolve Deribit IV once per asset (cached DB call)
        iv_by_asset: dict[str, Optional[float]] = {}
        for ctx in contexts:
            if ctx.asset not in iv_by_asset:
                iv_by_asset[ctx.asset] = await self._deribit_iv(ctx.asset)
            ctx.deribit_iv = iv_by_asset[ctx.asset]

        db = getattr(self._order_manager, "_db", None)
        if db is None:
            return

        for ctx in contexts:
            for obs in self._observers:
                try:
                    signals = obs.evaluate(ctx)
                except Exception as e:
                    logger.error(f"observer {obs.name} crashed: {e}")
                    continue
                for sig in signals:
                    cooldown_key = (sig.observer, sig.market_id)
                    last = self._last_fire.get(cooldown_key)
                    if last is not None and (now_epoch - last) < SIGNAL_COOLDOWN_S:
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
                    except Exception as e:
                        logger.error(f"record_crypto_observer_signal failed: {e}")
