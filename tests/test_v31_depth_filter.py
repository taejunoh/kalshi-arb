"""V31 depth filter — orderbook shallow → skip."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from strategies.sports_longshot_v31 import SportsLongshotV31Strategy


def _make(depth_yes=10.0, depth_no=10.0):
    config = MagicMock()
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    om = MagicMock()
    om.bankroll = 2000.0
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    client = MagicMock()
    client.get_order_book_depth = MagicMock(return_value=(depth_yes, depth_no))
    strat = SportsLongshotV31Strategy(config, om, client)
    return strat, client


def test_v31_min_depth_is_5():
    strat, _ = _make()
    assert strat.MIN_DEPTH_USDC == 5.0


def test_v3_base_min_depth_is_zero():
    from strategies.sports_longshot import SportsLongshotStrategy
    assert SportsLongshotStrategy.MIN_DEPTH_USDC == 0.0


def test_has_sufficient_depth_returns_true_when_ample():
    strat, _ = _make(depth_yes=10.0, depth_no=10.0)
    assert strat._has_sufficient_depth("T1", "yes", 5.0) is True


def test_has_sufficient_depth_returns_false_when_shallow():
    strat, _ = _make(depth_yes=2.0, depth_no=2.0)
    assert strat._has_sufficient_depth("T1", "yes", 5.0) is False
    assert strat._has_sufficient_depth("T1", "no", 5.0) is False


def test_has_sufficient_depth_checks_correct_side():
    strat, _ = _make(depth_yes=10.0, depth_no=2.0)
    assert strat._has_sufficient_depth("T1", "yes", 5.0) is True
    assert strat._has_sufficient_depth("T1", "no", 5.0) is False
