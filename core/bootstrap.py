"""Bootstrap confidence intervals — adds statistical rigor to sim verdicts.

Phase B's day_skew and walk_forward checks compute point estimates (mean
EV, sum PnL). A pooled +EV of 0.04 on n=50 could be noise. Bootstrap
resampling computes the 95% confidence interval around the point estimate;
if it overlaps zero, the +EV claim is statistically weak.

Used by:
  - core/regime_stress.py — wraps total_pnl with CI
  - core/walk_forward.py — per-window EV CI
  - core/sim_reliability.py — downgrades verdict if CI overlaps 0

Implementation: simple percentile bootstrap (B resamples with replacement,
take 2.5th and 97.5th percentile). Default B=2000 (matches promotion_eval).
"""
from __future__ import annotations

import random
import statistics
from typing import Callable, Optional


def bootstrap_ci(values: list, statistic: Callable[[list], float] = None,
                  n_resamples: int = 2000,
                  ci_pct: float = 95.0,
                  seed: Optional[int] = 42) -> dict:
    """Compute bootstrap CI for a statistic over the given samples.

    Args:
        values: list of per-row observations (e.g., per-trade PnL).
        statistic: function values -> scalar. Default = mean.
        n_resamples: number of bootstrap draws.
        ci_pct: confidence level (default 95).
        seed: RNG seed for reproducibility. Pass None for nondeterministic.

    Returns dict:
        {
          "point": float,         # observed statistic
          "lower": float,          # CI lower bound
          "upper": float,          # CI upper bound
          "ci_pct": float,
          "n": int,
          "overlap_zero": bool,    # True if CI brackets 0
          "n_resamples": int,
        }
    """
    if statistic is None:
        statistic = lambda v: sum(v) / len(v) if v else 0.0
    n = len(values)
    if n == 0:
        return {"point": 0.0, "lower": 0.0, "upper": 0.0,
                "ci_pct": ci_pct, "n": 0, "overlap_zero": True,
                "n_resamples": 0}

    rng = random.Random(seed)
    point = statistic(values)

    # Resample with replacement
    draws = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        draws.append(statistic(sample))
    draws.sort()

    alpha = (100 - ci_pct) / 2 / 100
    lo_idx = int(alpha * n_resamples)
    hi_idx = int((1 - alpha) * n_resamples) - 1
    lo_idx = max(0, lo_idx)
    hi_idx = min(n_resamples - 1, hi_idx)

    lower, upper = draws[lo_idx], draws[hi_idx]
    overlap_zero = (lower <= 0 <= upper)

    return {
        "point": round(point, 5),
        "lower": round(lower, 5),
        "upper": round(upper, 5),
        "ci_pct": ci_pct,
        "n": n,
        "overlap_zero": overlap_zero,
        "n_resamples": n_resamples,
    }


def bootstrap_mean_ci(values: list, **kwargs) -> dict:
    """Convenience wrapper for mean."""
    return bootstrap_ci(values, statistic=None, **kwargs)


def bootstrap_sum_ci(values: list, **kwargs) -> dict:
    """Convenience wrapper for sum (e.g., total PnL)."""
    return bootstrap_ci(values, statistic=sum, **kwargs)
