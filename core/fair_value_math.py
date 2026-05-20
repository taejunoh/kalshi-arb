"""Lognormal fair-value math for binary "asset_T >= K" markets.

Reuses core/macro_consensus.py:normal_cdf (no scipy dependency).

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import math
from typing import Optional

from core.macro_consensus import normal_cdf


def lognormal_cdf_above(
    *, spot: float, strike: float, vol: float, tau_years: float,
) -> Optional[float]:
    """P(S_T >= K) under lognormal with drift r=0 (short-horizon crypto)."""
    if tau_years < 0 or strike <= 0 or spot <= 0:
        return None
    if vol <= 0 or tau_years == 0:
        if spot > strike:
            return 1.0
        if spot < strike:
            return 0.0
        return 0.5
    sigma_sqrt_tau = vol * math.sqrt(tau_years)
    d = (math.log(spot / strike) - 0.5 * vol * vol * tau_years) / sigma_sqrt_tau
    return normal_cdf(d, mu=0.0, sigma=1.0)
