import pytest
from unittest.mock import MagicMock, AsyncMock
from strategies.spread_scanner import SpreadScannerStrategy


def _make_strategy():
    config = MagicMock()
    config.SPREAD_SCANNER_ENABLED = True
    config.SPREAD_SCANNER_MIN_WIDTH = 0.08
    config.SPREAD_SCANNER_MIN_VOLUME = 500
    config.SPREAD_SCANNER_INTERVAL = 0
    config.SPREAD_SCANNER_MAX_HOURS = 12
    config.SPREAD_SCANNER_MIN_EDGE = 0.05
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    order_mgr = MagicMock()
    order_mgr.is_circuit_breaker_active.return_value = False
    order_mgr.calculate_position_size.return_value = 50.0
    order_mgr.limit_order = AsyncMock(return_value={"status": "paper_open"})
    scanner = MagicMock()
    client = MagicMock()
    price_feed = MagicMock()
    price_feed.get_price.return_value = 82000.0
    return SpreadScannerStrategy(config, order_mgr, scanner, client, price_feed)


def test_parse_threshold_above():
    strat = _make_strategy()
    result = strat._parse_threshold("KXBTC-26MAR3017-B75000")
    assert result == ("above", 75000.0)


def test_parse_threshold_range():
    strat = _make_strategy()
    result = strat._parse_threshold("KXBTC-26MAR3017-T76249.99")
    assert result == ("range", 76249.99)


def test_estimate_probability_above():
    strat = _make_strategy()
    # BTC at $82k, threshold $75k, 1h to close, 60% vol → high YES prob
    prob = strat.estimate_probability("KXBTC-26MAR3017-B75000", 82000, hours_to_close=1.0, volatility=0.60)
    assert prob > 0.80

    # BTC at $82k, threshold $85k → price below → low YES probability
    prob = strat.estimate_probability("KXBTC-26MAR3017-B85000", 82000, hours_to_close=1.0, volatility=0.60)
    assert prob < 0.25


def test_probability_time_scaling():
    strat = _make_strategy()
    # Same distance but different time — shorter time = more extreme probability
    prob_short = strat.estimate_probability("KXBTC-26MAR3017-B80000", 82000, hours_to_close=0.1, volatility=0.60)
    prob_long = strat.estimate_probability("KXBTC-26MAR3017-B80000", 82000, hours_to_close=6.0, volatility=0.60)
    # With price above threshold: shorter time → higher prob (less time to drop)
    assert prob_short > prob_long


def test_probability_volatility_scaling():
    strat = _make_strategy()
    # Same setup but different volatility — higher vol = less certain
    prob_low_vol = strat.estimate_probability("KXBTC-26MAR3017-B80000", 82000, hours_to_close=2.0, volatility=0.30)
    prob_high_vol = strat.estimate_probability("KXBTC-26MAR3017-B80000", 82000, hours_to_close=2.0, volatility=1.00)
    # With price above threshold: lower vol → more confident it stays above
    assert prob_low_vol > prob_high_vol


def test_momentum_bias():
    strat = _make_strategy()
    # Positive momentum (bullish) should increase YES probability for above markets
    prob_neutral = strat.estimate_probability("KXBTC-26MAR3017-B82000", 82000, hours_to_close=1.0, volatility=0.60, momentum=0.0)
    prob_bullish = strat.estimate_probability("KXBTC-26MAR3017-B82000", 82000, hours_to_close=1.0, volatility=0.60, momentum=0.5)
    assert prob_bullish > prob_neutral


def test_evaluate_crypto_bidirectional():
    strat = _make_strategy()
    # Fair prob = 0.80, YES ask = 0.50 → YES is underpriced (now bidirectional picks YES)
    signal = strat.evaluate_crypto(yes_ask=0.50, no_ask=0.60, fair_prob=0.80)
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["edge"] > 0.05


def test_evaluate_crypto_no_side():
    strat = _make_strategy()
    # Fair prob = 0.20, NO ask = 0.50 → NO underpriced
    signal = strat.evaluate_crypto(yes_ask=0.60, no_ask=0.50, fair_prob=0.20)
    assert signal is not None
    assert signal["side"] == "no"


def test_no_signal_within_edge():
    strat = _make_strategy()
    # Fair prob = 0.50, both edges < 5%
    signal = strat.evaluate_crypto(yes_ask=0.52, no_ask=0.52, fair_prob=0.50)
    assert signal is None


def test_skips_when_disabled():
    strat = _make_strategy()
    strat.config.SPREAD_SCANNER_ENABLED = False
    import asyncio
    asyncio.get_event_loop().run_until_complete(strat.scan_and_execute())
    strat._scanner.get_active_markets.assert_not_called()
