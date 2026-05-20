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
