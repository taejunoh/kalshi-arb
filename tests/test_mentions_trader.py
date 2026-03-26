import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from strategies.mentions_trader import MentionsTraderStrategy


def _make_strategy():
    config = MagicMock()
    config.MENTIONS_ENABLED = True
    config.MENTIONS_MIN_EDGE = 0.15
    config.MENTIONS_FREQUENCY_API_URL = ""
    order_mgr = MagicMock()
    order_mgr.is_circuit_breaker_active.return_value = False
    order_mgr.calculate_position_size.return_value = 50.0
    order_mgr.market_order = AsyncMock(return_value={"status": "paper_filled"})
    scanner = MagicMock()
    client = MagicMock()
    strat = MentionsTraderStrategy(config, order_mgr, scanner, client)
    return strat, scanner, client, order_mgr


def test_title_pattern_matching():
    strat, _, _, _ = _make_strategy()
    result = strat.parse_market_title("Will Biden mention inflation?")
    assert result is not None
    assert result[0] == "Biden"
    assert result[1] == "inflation"


def test_title_pattern_say():
    strat, _, _, _ = _make_strategy()
    result = strat.parse_market_title("Will Trump say 'China'?")
    assert result is not None
    assert result[0] == "Trump"
    assert result[1] == "China"


def test_title_no_match():
    strat, _, _, _ = _make_strategy()
    result = strat.parse_market_title("Bitcoin price above 80000?")
    assert result is None


def test_buys_yes_when_underpriced():
    strat, _, _, _ = _make_strategy()
    # Historical frequency 0.60, market at 0.30 → edge = 0.30 > 0.15
    signal = strat.evaluate(yes_price=0.30, historical_freq=0.60)
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["edge"] == pytest.approx(0.30)


def test_buys_no_when_overpriced():
    strat, _, _, _ = _make_strategy()
    # Historical frequency 0.20, market at 0.60 → edge = -0.40
    signal = strat.evaluate(yes_price=0.60, historical_freq=0.20)
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["edge"] == pytest.approx(0.40)


def test_no_signal_within_edge():
    strat, _, _, _ = _make_strategy()
    # freq=0.50, price=0.45 → edge = 0.05 < 0.15
    signal = strat.evaluate(yes_price=0.45, historical_freq=0.50)
    assert signal is None


def test_skips_when_disabled():
    strat, scanner, _, _ = _make_strategy()
    strat.config.MENTIONS_ENABLED = False
    import asyncio
    asyncio.get_event_loop().run_until_complete(strat.scan_and_execute())
    scanner.filter_markets.assert_not_called()
