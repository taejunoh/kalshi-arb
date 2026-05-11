import math
import pytest

from core.fair_value_math import lognormal_cdf_above


def test_atm_zero_vol_zero_tau_returns_neutral():
    assert lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.0, tau_years=0.0) == 0.5
    assert lognormal_cdf_above(spot=110.0, strike=100.0, vol=0.0, tau_years=0.0) == 1.0
    assert lognormal_cdf_above(spot=90.0, strike=100.0, vol=0.0, tau_years=0.0) == 0.0


def test_atm_3month_20pct_vol():
    """S=100 K=100 sigma=0.20 tau=0.25:
    fair = Phi((-0.005) / 0.1) = Phi(-0.05) ≈ 0.4801"""
    p = lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert abs(p - 0.4801) < 0.01


def test_itm_call_deeper_than_atm():
    p = lognormal_cdf_above(spot=110.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert p > 0.5


def test_otm_call_below_atm():
    p = lognormal_cdf_above(spot=90.0, strike=100.0, vol=0.20, tau_years=0.25)
    assert p < 0.5


def test_negative_tau_returns_none():
    assert lognormal_cdf_above(spot=100.0, strike=100.0, vol=0.20, tau_years=-0.01) is None


def test_zero_or_negative_strike_returns_none():
    assert lognormal_cdf_above(spot=100.0, strike=0.0, vol=0.20, tau_years=0.25) is None
    assert lognormal_cdf_above(spot=100.0, strike=-50.0, vol=0.20, tau_years=0.25) is None
