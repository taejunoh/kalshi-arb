"""Unit tests for _has_sufficient_depth helper on SportsTraderStrategy."""
from unittest.mock import MagicMock

from strategies.sports_trader import SportsTraderStrategy


def _make_strategy(yes_depth: float, no_depth: float) -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = 100.0
    client = MagicMock()
    client.get_order_book_depth = MagicMock(return_value=(yes_depth, no_depth))
    return SportsTraderStrategy(config, order_mgr, client)


def test_depth_sufficient_yes_side():
    strat = _make_strategy(yes_depth=10.0, no_depth=5.0)
    assert strat._has_sufficient_depth("TEST-TKR", "yes", 3.0) is True


def test_depth_insufficient_yes_side():
    strat = _make_strategy(yes_depth=2.0, no_depth=50.0)
    assert strat._has_sufficient_depth("TEST-TKR", "yes", 3.0) is False


def test_depth_sufficient_no_side():
    strat = _make_strategy(yes_depth=0.0, no_depth=7.0)
    assert strat._has_sufficient_depth("TEST-TKR", "no", 5.0) is True


def test_depth_insufficient_no_side():
    strat = _make_strategy(yes_depth=100.0, no_depth=1.0)
    assert strat._has_sufficient_depth("TEST-TKR", "no", 3.0) is False


def test_depth_equal_to_required():
    """Exact equality passes (>=)."""
    strat = _make_strategy(yes_depth=3.0, no_depth=3.0)
    assert strat._has_sufficient_depth("TEST-TKR", "yes", 3.0) is True
