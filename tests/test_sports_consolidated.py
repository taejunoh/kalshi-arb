"""Unit tests for sports_consolidated — union of longshot/midshot/underdog filters."""
from unittest.mock import MagicMock

from strategies.sports_consolidated import SportsConsolidatedStrategy


def _make_strategy(bankroll=2000.0) -> SportsConsolidatedStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    # ④ gate flag (37b4f4c): inline `min(fee, price, 1-price)/price` was moved
    # into config.gate_fee_pct(price). With REAL_FEE_GATE OFF (live default)
    # it is byte-identical to the legacy formula, so stub the MagicMock with it.
    config.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1 else min(0.07, price, 1.0 - price) / price
    )
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    client = MagicMock()
    return SportsConsolidatedStrategy(config, order_mgr, client)


def test_fires_in_longshot_range():
    """price 0.20, payoff 3.70x → within 0.05-0.40 and payoff >= 2 → fire."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.30, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"


def test_fires_in_midshot_range():
    """price 0.30, payoff 2.70x → passes."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.30, kalshi_no=0.72, fair_prob=0.30, ticker="T")
    assert signal is not None


def test_fires_in_underdog_range():
    """price 0.38, payoff 1/(0.38+0.07)=2.22x → passes 2.0 threshold."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.62, kalshi_no=0.38, fair_prob=0.62, ticker="T")
    assert signal is not None
    assert signal["side"] == "no"


def test_rejects_price_above_040():
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.45, kalshi_no=0.56, fair_prob=0.45, ticker="T")
    assert signal is None


def test_rejects_payoff_below_2x():
    """price 0.50 → payoff 1/(0.50+0.07)=1.75 < 2 → None. Also > 0.40 cap."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.50, kalshi_no=0.51, fair_prob=0.50, ticker="T")
    assert signal is None


def test_single_fire_not_triple_fire_on_overlap():
    """Return a single signal even when longshot+midshot both qualify."""
    strat = _make_strategy()
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.30, ticker="T")
    # Should be exactly one signal dict, not a list
    assert isinstance(signal, dict)
    assert signal["strategy"] == "consolidated"


def test_strategy_name():
    strat = _make_strategy()
    assert strat.STRATEGY_NAME == "sports_consolidated"
    assert strat.TRADED_TICKERS_FILE == "data/sports_consolidated_tickers.json"
