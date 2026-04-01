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
    price_feed.get_price.return_value = 82000.0  # BTC at $82k
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
    # BTC at $82k, threshold $75k → price well above → high YES probability
    prob = strat.estimate_probability("KXBTC-26MAR3017-B75000", 82000)
    assert prob > 0.90

    # BTC at $82k, threshold $85k → price below → low YES probability
    prob = strat.estimate_probability("KXBTC-26MAR3017-B85000", 82000)
    assert prob < 0.15


def test_evaluate_crypto_no_underpriced():
    strat = _make_strategy()
    # Fair prob = 0.20, NO ask = 0.50 → NO underpriced (1-0.20=0.80 vs 0.50)
    signal = strat.evaluate_crypto(yes_ask=0.60, no_ask=0.50, fair_prob=0.20)
    assert signal is not None
    assert signal["side"] == "no"
    # Net edge = (1-0.20) - 0.50 * (1 + 0.07/0.50) = 0.80 - 0.57 = 0.23
    assert signal["edge"] == pytest.approx(0.23)


def test_no_yes_trades_in_no_only_mode():
    strat = _make_strategy()
    # Fair prob = 0.80, YES would be underpriced, but NO-only mode skips it
    signal = strat.evaluate_crypto(yes_ask=0.50, no_ask=0.60, fair_prob=0.80)
    # NO edge = (1-0.80) - 0.60 = -0.40 → no signal
    assert signal is None


def test_no_signal_within_edge():
    strat = _make_strategy()
    # Fair prob = 0.50, NO edge = 0.50 - 0.48 = 0.02 < 0.05
    signal = strat.evaluate_crypto(yes_ask=0.55, no_ask=0.48, fair_prob=0.50)
    assert signal is None


def test_skips_when_disabled():
    strat = _make_strategy()
    strat.config.SPREAD_SCANNER_ENABLED = False
    import asyncio
    asyncio.get_event_loop().run_until_complete(strat.scan_and_execute())
    strat._scanner.get_active_markets.assert_not_called()
