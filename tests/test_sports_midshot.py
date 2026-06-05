"""Unit tests for Sports Midshot strategy — 5-35c contracts, 2.5x+ payoff."""
from unittest.mock import MagicMock

from strategies.sports_midshot import SportsMidshotStrategy


def _make_strategy(bankroll: float = 90.0) -> SportsMidshotStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    # ④ gate-fee flag (commit 37b4f4c): strategy now calls config.gate_fee_pct(price)
    # instead of the inline min(fee, price, 1-price)/price. With REAL_FEE_GATE OFF
    # (production/live default) it returns the byte-identical legacy value, so stub it
    # here to keep these fire/no-fire assertions on the unchanged formula.
    config.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1 else min(0.07, price, 1.0 - price) / price
    )
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    client = MagicMock()
    return SportsMidshotStrategy(config, order_mgr, client)


def test_midshot_accepts_20c_yes():
    """yes=0.20 → payoff ~3.70x passes 2.5x threshold."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.80, fair_prob=0.25)
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["price"] == 0.20
    # payoff = 1 / (0.20 + 0.07) = 3.7037
    assert 3.6 < signal["edge"] < 3.8


def test_midshot_rejects_40c_over_price_cap():
    """Both sides > 0.35 → None."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.40, kalshi_no=0.62, fair_prob=0.40)
    assert signal is None


def test_midshot_rejects_35c_payoff_below_threshold():
    """yes=0.35 → payoff 2.38x < 2.5x; no=0.66 > 0.35 cap → None."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.35, kalshi_no=0.66, fair_prob=0.35)
    assert signal is None


def test_midshot_accepts_30c():
    """yes=0.30 → payoff 2.70x passes."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.30, kalshi_no=0.72, fair_prob=0.30)
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["price"] == 0.30
    # payoff = 1 / (0.30 + 0.07) = 2.7027
    assert 2.6 < signal["edge"] < 2.8


def test_midshot_picks_higher_payoff_side():
    """yes=0.30 (payoff 2.70), no=0.20 (payoff 3.70) → no side wins."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.30, kalshi_no=0.20, fair_prob=0.50)
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["price"] == 0.20
    assert 3.6 < signal["edge"] < 3.8


def test_midshot_rejects_dust_below_5c():
    """yes=0.03 < 0.05 lower bound → None (no side > 0.35 cap)."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.03, kalshi_no=0.98, fair_prob=0.03)
    assert signal is None
