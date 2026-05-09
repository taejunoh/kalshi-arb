"""Macro event consensus — scrapers + bucket prob helpers + DB cache shims.

Stateless functions. Persistence is delegated to core/database.py.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import math
from typing import Optional


def normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Cumulative distribution function for N(mu, sigma)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bucket_prob_from_normal(
    low: Optional[float],
    high: Optional[float],
    mu: float,
    sigma: float,
) -> float:
    """Probability that X ~ N(mu, sigma) falls in the half-open interval [low, high].

    `low=None` treated as -infinity (P(X <= high)).
    `high=None` treated as +infinity (P(X >= low)).
    `sigma=0` is the degenerate case: returns 1.0 if mu is inside the
    interval, 0.0 otherwise.
    """
    if sigma <= 0:
        in_lower = (low is None) or (mu >= low)
        in_upper = (high is None) or (mu <= high)
        return 1.0 if (in_lower and in_upper) else 0.0
    p_high = 1.0 if high is None else normal_cdf(high, mu, sigma)
    p_low = 0.0 if low is None else normal_cdf(low, mu, sigma)
    return max(0.0, p_high - p_low)
