"""Cross-asset crypto Phase 0 observers.

Three observers test hypotheses that the within-market-state observers in
crypto_observer.py do not cover:
  - btc_eth_lead_lag: BTC moves first, ETH spot/Kalshi hasn't caught up
  - cross_asset_corr_break: BTC-ETH 5-min correlation drops sharply
  - kalshi_book_lag: Spot moved but Kalshi bucket-mid is stale

Phase 0 data-only — no trading. Promotion to Phase 1 requires bucket-WR
analysis after ≥1 week of data per the design spec.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Optional

logger = logging.getLogger("crypto_cross_asset_observer")

# Minimum samples required to compute a stable correlation. Below this, return
# None and the consuming observer gracefully skips (any None feature → skip).
CORR_MIN_SAMPLES = 10


def _pearson_correlation(xs: list[float], ys: list[float]) -> Optional[float]:
    """Pearson correlation of two equal-length numeric series.

    Returns None if either series has < CORR_MIN_SAMPLES points or zero
    variance (the correlation is undefined when one series is constant).
    """
    n = len(xs)
    if n != len(ys) or n < CORR_MIN_SAMPLES:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return None
    return num / denom


# BookMidCache: per-bucket rolling history of mid prices for book_lag observer.
#
# Memory budget (t3.small, ~530MB current RSS):
#   per entry: (ts, mid) tuple ~64 bytes
#   per bucket: maxlen=60 → ~4 KB
#   ~5000 active buckets (KXBTC + KXETH × ~24 contracts × ~50 strikes)
#   max ~20 MB — t3.small has 2 GB so this is ~1% headroom impact
#
# Defenses:
#   - deque maxlen=60 auto-evicts oldest entry on overflow
#   - prune_stale() removes keys whose last entry is older than max_age_s
#   - hard_cap (default 20,000 keys) triggers full flush + Telegram alert.
#     If we ever hit the cap, pruning is broken — alert and recover.
class _BookMidCache:
    """Rolling (series, strike) → deque[(ts, mid)] storage for book_lag."""

    DEQUE_MAXLEN = 60                  # ~30s window at 1Hz + 30s slack
    DEFAULT_HARD_CAP = 20_000          # max distinct (series, strike) keys
    STALE_GUARD_MULTIPLIER = 2.0       # n_seconds_ago accepts ≤ 2× lookback away

    def __init__(self, hard_cap: int = DEFAULT_HARD_CAP):
        self._cache: dict[tuple[str, float], deque] = {}
        self._hard_cap = hard_cap

    def update(self, series: str, strike: float, ts: float, mid: float) -> bool:
        """Insert (ts, mid) for the (series, strike) bucket.

        Returns True if a hard-cap flush occurred during this call (caller
        is responsible for downstream alerting). On flush, the triggering
        entry is NOT added; the caller should re-insert on next cycle.
        """
        flushed = False
        if (series, strike) not in self._cache and len(self._cache) >= self._hard_cap:
            logger.error(
                f"_BookMidCache hard cap {self._hard_cap} reached — flushing all "
                f"entries. This indicates pruning is broken."
            )
            self._cache.clear()
            flushed = True
        # On flush, skip insertion of the triggering entry
        if flushed:
            return flushed
        key = (series, strike)
        dq = self._cache.get(key)
        if dq is None:
            dq = deque(maxlen=self.DEQUE_MAXLEN)
            self._cache[key] = dq
        dq.append((ts, mid))
        return flushed

    def n_seconds_ago(self, series: str, strike: float, lookback_s: float,
                      now_ts: float) -> Optional[float]:
        """Most recent mid at or before (now_ts - lookback_s).

        Returns None if:
          - the key has no history, or
          - the nearest sample is > STALE_GUARD_MULTIPLIER × lookback_s away
            (data gap — stale lookback would mis-attribute).
        """
        dq = self._cache.get((series, strike))
        if not dq:
            return None
        target = now_ts - lookback_s
        # When lookback_s=0 (asking for "now"), use a generous guard to accept
        # recently-pruned data; for positive lookback, enforce the multiplier.
        if lookback_s > 0:
            max_gap = self.STALE_GUARD_MULTIPLIER * lookback_s
        else:
            max_gap = 120.0  # ~1 min grace for "now" queries after prune_stale
        # Walk deque (oldest → newest) and remember the latest entry with ts ≤ target.
        best_ts = None
        best_mid = None
        for ts, mid in dq:
            if ts <= target:
                best_ts = ts
                best_mid = mid
            else:
                break
        if best_ts is None:
            return None
        if (target - best_ts) > max_gap:
            return None
        return best_mid

    def prune_stale(self, now_ts: float, max_age_s: float = 120.0) -> int:
        """Drop keys whose newest entry is older than max_age_s. Returns count."""
        to_drop: list[tuple[str, float]] = []
        for key, dq in self._cache.items():
            if not dq:
                to_drop.append(key)
                continue
            last_ts, _ = dq[-1]
            if (now_ts - last_ts) > max_age_s:
                to_drop.append(key)
        for key in to_drop:
            del self._cache[key]
        return len(to_drop)

    def size(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# BtcEthLeadLagObserver
# ---------------------------------------------------------------------------


def _safe_div(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return a / b


class _CrossAssetBaseObserver:
    """Shared parent for cross-asset observers. Provides feature helpers."""
    name: str = ""

    def _delta_pct(self, now_val: Optional[float], past_val: Optional[float]
                   ) -> Optional[float]:
        if now_val is None or past_val is None or past_val == 0:
            return None
        return (now_val - past_val) / past_val

    def _source_signal_hash(self, now_ts: float, delta_btc_pct: float) -> int:
        bucket_time_15s = int(now_ts // 15) * 15
        sign = 1 if delta_btc_pct >= 0 else -1
        return hash((bucket_time_15s, sign, round(delta_btc_pct, 3)))


# Gates per design spec
LEAD_LAG_LEAD_THRESHOLD = 0.003       # 0.3% Δspot_BTC over 30s
LEAD_LAG_LAG_THRESHOLD = 0.0015       # 0.15% Δspot_ETH over 30s
LEAD_LAG_YA_MIN = 0.05
LEAD_LAG_YA_MAX = 0.60
LEAD_LAG_MTS_MIN_S = 300.0
LEAD_LAG_DIST_PCT_MAX = 0.020         # 2% from spot
LEAD_LAG_YB_SIZE_MIN = 30
LEAD_LAG_TARGET_SERIES = "KXETH"      # Primary — KXBTC reverse-direction also logged


class BtcEthLeadLagObserver(_CrossAssetBaseObserver):
    """BTC moves first, ETH spot/Kalshi hasn't caught up.

    Fires on KXETH bucket near the expected catch-up zone. Logs predicted
    direction (up/down) corresponding to sign(Δspot_BTC). Phase 0 only.
    """
    name = "btc_eth_lead_lag"

    def evaluate(self, ctx) -> list:
        from strategies.crypto_observer import Signal  # avoid circular at import time

        # Only fires on KXETH primary
        if ctx.series != LEAD_LAG_TARGET_SERIES:
            return []
        if ctx.close_time_secs is None or ctx.close_time_secs < LEAD_LAG_MTS_MIN_S:
            return []
        if not ctx.b_legs:
            return []

        delta_btc = self._delta_pct(ctx.spot_btc, ctx.spot_btc_30s_ago)
        delta_eth = self._delta_pct(ctx.spot_eth, ctx.spot_eth_30s_ago)
        if delta_btc is None or delta_eth is None:
            return []
        if abs(delta_btc) < LEAD_LAG_LEAD_THRESHOLD:
            return []
        if abs(delta_eth) > LEAD_LAG_LAG_THRESHOLD:
            return []

        direction = "up" if delta_btc > 0 else "down"
        out: list = []
        for leg in ctx.b_legs:
            if leg.ya < LEAD_LAG_YA_MIN or leg.ya > LEAD_LAG_YA_MAX:
                continue
            if leg.yb_size < LEAD_LAG_YB_SIZE_MIN:
                continue
            dist_pct = abs(leg.threshold - ctx.spot) / ctx.spot
            if dist_pct > LEAD_LAG_DIST_PCT_MAX:
                continue
            # Strike must be on the expected catch-up side
            if direction == "up" and leg.threshold <= ctx.spot:
                continue
            if direction == "down" and leg.threshold >= ctx.spot:
                continue

            delta_btc_10s = self._delta_pct(ctx.spot_btc, ctx.spot_btc_10s_ago)
            delta_eth_10s = self._delta_pct(ctx.spot_eth, ctx.spot_eth_10s_ago)
            now_ts = ctx.now.timestamp()

            out.append(Signal(
                observer=self.name, series=ctx.series,
                event_key=ctx.event_key, market_id=leg.ticker,
                predicted_outcome="yes",
                features={
                    "ya": leg.ya, "yb": leg.yb, "na": leg.na,
                    "yb_size": leg.yb_size,
                    "spot": ctx.spot, "strike": leg.threshold,
                    "dist_pct": round(dist_pct, 5),
                    "mts_s": round(ctx.close_time_secs, 1),
                    "kind": leg.kind,
                    "spot_btc": ctx.spot_btc, "spot_eth": ctx.spot_eth,
                    "spot_btc_30s_ago": ctx.spot_btc_30s_ago,
                    "spot_eth_30s_ago": ctx.spot_eth_30s_ago,
                    "spot_btc_10s_ago": ctx.spot_btc_10s_ago,
                    "spot_eth_10s_ago": ctx.spot_eth_10s_ago,
                    "delta_btc_30s_pct": round(delta_btc, 6),
                    "delta_eth_30s_pct": round(delta_eth, 6),
                    "delta_btc_10s_pct": round(delta_btc_10s, 6) if delta_btc_10s is not None else None,
                    "delta_eth_10s_pct": round(delta_eth_10s, 6) if delta_eth_10s is not None else None,
                    "corr_5min": ctx.corr_5min,
                    "corr_15min": ctx.corr_15min,
                    "predicted_direction": direction,
                    "source_signal_hash": self._source_signal_hash(now_ts, delta_btc),
                },
            ))
        return out
