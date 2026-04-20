"""Unit tests for Sports V4 Edge strategy — midshot price filter + edge gate."""
from unittest.mock import MagicMock

from strategies.sports_v4_edge import SportsV4EdgeStrategy


def _make_strategy(pinnacle_yes_prob=None, bankroll=2000.0) -> SportsV4EdgeStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    client = MagicMock()
    s = SportsV4EdgeStrategy(config, order_mgr, client)
    s._last_pinnacle_yes_prob = pinnacle_yes_prob
    return s


def test_fires_when_price_in_range_and_pinnacle_edge_ok():
    """price 0.20, pinnacle yes_prob 0.35 → 0.35 - 0.20*(1+0.35) = 0.08 > 0.03. Also payoff 1/(0.27)=3.7 > 2.5."""
    strat = _make_strategy(pinnacle_yes_prob=0.35)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.35, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["price"] == 0.20


def test_falls_back_to_consensus_when_pinnacle_missing():
    """No Pinnacle → use consensus fair_prob."""
    strat = _make_strategy(pinnacle_yes_prob=None)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.35, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"


def test_rejects_when_price_above_midshot_cap():
    """Price > 0.35 filtered regardless of edge."""
    strat = _make_strategy(pinnacle_yes_prob=0.80)
    signal = strat.evaluate(kalshi_yes=0.40, kalshi_no=0.62, fair_prob=0.80, ticker="T")
    assert signal is None


def test_rejects_when_payoff_below_25x():
    """price 0.35 → payoff 1/(0.35+0.07) = 2.38 < 2.5 → skip."""
    strat = _make_strategy(pinnacle_yes_prob=0.50)
    signal = strat.evaluate(kalshi_yes=0.35, kalshi_no=0.68, fair_prob=0.50, ticker="T")
    assert signal is None


def test_rejects_when_edge_below_3pct():
    """Price 0.20 passes filter but edge gate 0.03 not met."""
    # pinnacle 0.22 → yes_edge = 0.22 - 0.20*1.35 = -0.05 < 0.03
    strat = _make_strategy(pinnacle_yes_prob=0.22)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.22, ticker="T")
    assert signal is None


def test_selects_no_side_when_better():
    """NO side edge better: no_ask 0.20, yes 0.82. Pinnacle says NO 0.35 prob → no_edge = 0.35 - 0.20*1.35 = 0.08."""
    strat = _make_strategy(pinnacle_yes_prob=0.65)
    signal = strat.evaluate(kalshi_yes=0.82, kalshi_no=0.20, fair_prob=0.65, ticker="T")
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["price"] == 0.20


def test_strategy_name():
    strat = _make_strategy(pinnacle_yes_prob=0.5)
    assert strat.STRATEGY_NAME == "sports_v4_edge"
    assert strat.TRADED_TICKERS_FILE == "data/sports_v4_edge_tickers.json"


def test_v4_edge_has_depth_and_dead_zone_gates():
    """Live-promotion safety rails: 5 USDC depth + 10–20¢ dead zone."""
    assert SportsV4EdgeStrategy.MIN_DEPTH_USDC == 5.0
    assert SportsV4EdgeStrategy.ENTRY_PRICE_DEAD_ZONE == (0.10, 0.20)
    assert SportsV4EdgeStrategy.MIN_ENTRY_PRICE is None
