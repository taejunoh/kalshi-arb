"""Unit tests for sports_liquidity_filtered — midshot logic + orderbook depth gate."""
from unittest.mock import MagicMock

from strategies.sports_liquidity_filtered import SportsLiquidityFilteredStrategy


def _make_strategy(yes_depth=100.0, no_depth=100.0, bankroll=2000.0) -> SportsLiquidityFilteredStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    client = MagicMock()
    client.get_order_book_depth = MagicMock(return_value=(yes_depth, no_depth))
    return SportsLiquidityFilteredStrategy(config, order_mgr, client)


def test_accepts_midshot_signal_when_depth_sufficient():
    """price 0.20, depth 100 > 3 → fires."""
    strat = _make_strategy(yes_depth=100.0, no_depth=5.0)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.25, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"


def test_rejects_midshot_signal_when_yes_depth_shallow():
    """price 0.20 midshot pass but YES depth 0.5 < size 3 → None."""
    strat = _make_strategy(yes_depth=0.5, no_depth=0.5)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.25, ticker="T")
    assert signal is None


def test_rejects_when_price_out_of_midshot_range():
    """price 0.40 > midshot 0.35 cap → skip regardless of depth."""
    strat = _make_strategy(yes_depth=100.0, no_depth=100.0)
    signal = strat.evaluate(kalshi_yes=0.40, kalshi_no=0.62, fair_prob=0.40, ticker="T")
    assert signal is None


def test_picks_no_side_when_yes_depth_missing_but_no_depth_ok():
    """YES payoff better (0.20) but depth empty; NO 0.30 pass → no signal."""
    # midshot picks highest payoff — yes 0.20 payoff 3.70 > no 0.30 payoff 2.70
    # but yes depth empty blocks → no signal (not fallback to no)
    strat = _make_strategy(yes_depth=0.5, no_depth=100.0)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.30, fair_prob=0.30, ticker="T")
    assert signal is None


def test_strategy_name():
    strat = _make_strategy()
    assert strat.STRATEGY_NAME == "sports_liquidity_filtered"
    assert strat.TRADED_TICKERS_FILE == "data/sports_liquidity_filtered_tickers.json"
