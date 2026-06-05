import pytest
from unittest.mock import MagicMock, AsyncMock
from strategies.market_maker import MarketMakerStrategy


def _make_strategy():
    config = MagicMock()
    config.MM_ENABLED = True
    config.MM_MIN_VOLUME = 1000
    config.MM_SPREAD_CENTS = 4
    config.MM_REQUOTE_INTERVAL = 30
    config.MM_MAX_POSITION = 200.0
    config.MM_MAX_MARKETS = 3
    order_mgr = MagicMock()
    order_mgr.is_circuit_breaker_active.return_value = False
    order_mgr.calculate_position_size.return_value = 100.0
    order_mgr.get_position.return_value = {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0}
    order_mgr.get_strategy_exposure.return_value = 0.0
    order_mgr.limit_order = AsyncMock(return_value={"order_id": "p1", "status": "paper_open"})
    order_mgr.cancel_order = AsyncMock(return_value=True)
    order_mgr.requote = AsyncMock(return_value={"order_id": "p2", "status": "paper_open"})
    scanner = MagicMock()
    client = MagicMock()
    return MarketMakerStrategy(config, order_mgr, scanner, client), scanner, client, order_mgr


def test_mid_price_calculation():
    strat, _, _, _ = _make_strategy()
    levels = {
        "yes": [(0.45, 100), (0.44, 200)],
        "no": [(0.48, 150), (0.47, 50)],
    }
    mid = strat.calculate_mid_price(levels)
    assert mid is not None
    # yes_vwap = (45+88)/300 = 0.4433, no_vwap = (72+23.5)/200 = 0.4775
    # mid = (0.4433 + (1-0.4775)) / 2 = 0.4829
    assert mid == pytest.approx(0.483, abs=0.01)


def test_quote_placement_prices():
    strat, _, _, _ = _make_strategy()
    # Spread = 4 cents → half = 2 cents
    yes_bid, no_bid = strat.calculate_quote_prices(0.50)
    assert yes_bid == pytest.approx(0.48)
    assert no_bid == pytest.approx(0.48)


def test_quote_placement_asymmetric():
    strat, _, _, _ = _make_strategy()
    yes_bid, no_bid = strat.calculate_quote_prices(0.70)
    # yes_bid = 0.70 - 0.02 = 0.68
    # no_bid = (1.0 - 0.70) - 0.02 = 0.28
    assert yes_bid == pytest.approx(0.68)
    assert no_bid == pytest.approx(0.28)


def test_inventory_skew():
    strat, _, _, order_mgr = _make_strategy()
    # Long YES position → should widen yes bid, tighten no bid
    order_mgr.get_position.return_value = {"side": "yes", "qty": 10, "avg_price": 0.50, "size_usdc": 50}
    yes_bid, no_bid = strat.apply_inventory_skew(0.48, 0.48, "T1")
    assert yes_bid < 0.48  # Widened (less aggressive)
    assert no_bid > 0.48  # Tightened (more aggressive to flatten)


def test_max_markets_limit():
    strat, scanner, client, _ = _make_strategy()
    scanner.get_unlocked_markets.return_value = [
        {"ticker": f"MKT-{i}", "volume_fp": "5000"} for i in range(10)
    ]
    selected = strat._select_markets()
    assert len(selected) <= 3


def test_cancels_all_on_circuit_breaker():
    strat, _, _, order_mgr = _make_strategy()
    order_mgr.is_circuit_breaker_active.return_value = True
    strat._active_quotes = {
        "T1": {"yes_order_id": "y1", "no_order_id": "n1", "last_quote_time": 0, "mid_price": 0.5, "size_usdc": 50},
        "T2": {"yes_order_id": "y2", "no_order_id": "n2", "last_quote_time": 0, "mid_price": 0.5, "size_usdc": 50},
    }
    import asyncio
    asyncio.run(strat.scan_and_execute())
    assert len(strat._active_quotes) == 0
    assert order_mgr.cancel_order.call_count == 4  # 2 markets × 2 sides


def test_skips_when_disabled():
    strat, scanner, _, _ = _make_strategy()
    strat.config.MM_ENABLED = False
    import asyncio
    asyncio.run(strat.scan_and_execute())
    scanner.get_unlocked_markets.assert_not_called()
