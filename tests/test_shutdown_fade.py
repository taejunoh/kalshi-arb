import pytest
from unittest.mock import MagicMock, AsyncMock
from strategies.shutdown_fade import ShutdownFadeStrategy


def _make_strategy():
    config = MagicMock()
    config.SHUTDOWN_FADE_ENABLED = True
    config.SHUTDOWN_FADE_THRESHOLD = 0.25
    config.SHUTDOWN_FADE_SCALE_LEVELS = 3
    config.SHUTDOWN_FADE_MAX_POSITION = 500.0
    order_mgr = MagicMock()
    order_mgr.is_circuit_breaker_active.return_value = False
    order_mgr.market_order = AsyncMock(return_value={"status": "paper_filled", "order_id": "p1"})
    scanner = MagicMock()
    client = MagicMock()
    return ShutdownFadeStrategy(config, order_mgr, scanner, client), scanner, client, order_mgr


def test_identifies_shutdown_markets():
    strat, scanner, _, _ = _make_strategy()
    scanner.search_markets.side_effect = lambda kw: [
        {"ticker": "GOV-SHUTDOWN-YES", "title": "Government shutdown before April?"}
    ] if "shutdown" in kw else []
    markets = strat._find_shutdown_markets()
    assert len(markets) >= 1
    assert markets[0]["ticker"] == "GOV-SHUTDOWN-YES"


def test_buys_no_when_yes_above_threshold():
    strat, _, _, _ = _make_strategy()
    signal = strat.evaluate(0.30, "GOV-SHUTDOWN-1")
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["level"] == 0


def test_scales_in_at_multiple_levels():
    strat, _, _, _ = _make_strategy()
    # First level at 0.25
    sig1 = strat.evaluate(0.26, "T1")
    assert sig1 is not None
    strat._active_positions["T1"] = {"levels_filled": 1, "total_size": sig1["size_usdc"]}
    # Second level at 0.40
    sig2 = strat.evaluate(0.41, "T1")
    assert sig2 is not None
    assert sig2["level"] == 1
    strat._active_positions["T1"]["levels_filled"] = 2
    strat._active_positions["T1"]["total_size"] += sig2["size_usdc"]
    # Third level at 0.55
    sig3 = strat.evaluate(0.56, "T1")
    assert sig3 is not None
    assert sig3["level"] == 2


def test_respects_max_position():
    strat, _, _, _ = _make_strategy()
    strat._active_positions["T1"] = {"levels_filled": 2, "total_size": 500.0}
    signal = strat.evaluate(0.60, "T1")
    assert signal is None


def test_skips_when_disabled():
    strat, scanner, _, _ = _make_strategy()
    strat.config.SHUTDOWN_FADE_ENABLED = False
    import asyncio
    asyncio.get_event_loop().run_until_complete(strat.scan_and_execute())
    scanner.search_markets.assert_not_called()
