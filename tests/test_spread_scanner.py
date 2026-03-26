import pytest
from unittest.mock import MagicMock, AsyncMock
from strategies.spread_scanner import SpreadScannerStrategy


def _make_strategy():
    config = MagicMock()
    config.SPREAD_SCANNER_ENABLED = True
    config.SPREAD_SCANNER_MIN_WIDTH = 0.08
    config.SPREAD_SCANNER_MIN_VOLUME = 500
    config.SPREAD_SCANNER_INTERVAL = 0  # No delay in tests
    order_mgr = MagicMock()
    order_mgr.is_circuit_breaker_active.return_value = False
    order_mgr.calculate_position_size.return_value = 50.0
    order_mgr.market_order = AsyncMock(return_value={"status": "paper_filled"})
    scanner = MagicMock()
    client = MagicMock()
    return SpreadScannerStrategy(config, order_mgr, scanner, client)


def test_spread_calculation():
    strat = _make_strategy()
    # YES=0.55, NO=0.55 → spread = 0.10
    assert strat.calculate_spread(0.55, 0.55) == pytest.approx(0.10)
    # YES=0.50, NO=0.50 → spread = 0.0 (perfectly efficient)
    assert strat.calculate_spread(0.50, 0.50) == pytest.approx(0.0)


def test_scoring_formula():
    strat = _make_strategy()
    # Higher spread, volume, shorter time = higher score
    score1 = strat.score_market(0.10, 1000, 1.0)  # 100
    score2 = strat.score_market(0.10, 1000, 10.0)  # 10
    assert score1 > score2
    assert strat.score_market(0.10, 1000, 0) == 0.0


def test_vwap_approximation():
    strat = _make_strategy()
    levels = {
        "yes": [(0.45, 100), (0.44, 200)],
        "no": [(0.48, 150), (0.47, 50)],
    }
    mid = strat.calculate_vwap_mid(levels)
    assert mid is not None
    # yes_vwap = (0.45*100 + 0.44*200) / 300 = 133/300 ≈ 0.4433
    # no_vwap = (0.48*150 + 0.47*50) / 200 = 95.5/200 = 0.4775
    # mid = (0.4433 + (1 - 0.4775)) / 2 = (0.4433 + 0.5225) / 2 = 0.4829
    assert mid == pytest.approx(0.4829, abs=0.001)


def test_takes_underpriced_side():
    strat = _make_strategy()
    # VWAP mid is higher than current mid → YES underpriced
    signal = strat.evaluate(yes_ask=0.40, no_ask=0.70, vwap_mid=0.55)
    # current_mid = (0.40 + (1 - 0.70)) / 2 = (0.40 + 0.30) / 2 = 0.35
    # deviation = 0.55 - 0.35 = 0.20 > 0 → buy yes
    assert signal is not None
    assert signal["side"] == "yes"


def test_respects_min_volume():
    strat = _make_strategy()
    # With min_width=0.08, spread of 0.05 should not trigger
    signal = strat.evaluate(yes_ask=0.50, no_ask=0.50, vwap_mid=0.51)
    # deviation = 0.01, min_width/2 = 0.04 → too small
    assert signal is None


def test_skips_when_disabled():
    strat = _make_strategy()
    strat.config.SPREAD_SCANNER_ENABLED = False
    import asyncio
    asyncio.get_event_loop().run_until_complete(strat.scan_and_execute())
    strat._scanner.get_all_markets.assert_not_called()
