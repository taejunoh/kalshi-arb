"""Tests for orderbook_walk utility (Phase 0 phantom-quote defense)."""
import pytest
from unittest.mock import MagicMock

from core.orderbook_walk import walk_orderbook, WalkResult


def _mock_client(yes_levels, no_levels):
    """Mock Kalshi client returning canned orderbook_levels."""
    client = MagicMock()
    client.get_orderbook_levels.return_value = {"yes": yes_levels, "no": no_levels}
    return client


def test_walk_empty_book_returns_none():
    client = _mock_client([], [])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is None


def test_walk_single_level_yes_side():
    # To buy YES: read NO bids → derive YES asks
    # NO bid at $0.95 → YES ask at $0.05 (qty 10 contracts = $0.50 notional max)
    client = _mock_client(yes_levels=[(0.04, 100)], no_levels=[(0.95, 10)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.top_ask == pytest.approx(0.05)
    assert result.avg_fill_price == pytest.approx(0.05)
    assert result.max_fillable_usdc == pytest.approx(0.50)  # 10 contracts × $0.05
    assert result.levels_consumed == 1
    assert result.top_vs_walked_gap_cents == pytest.approx(0.0)


def test_walk_multi_level_with_gap():
    # NO bids: [(0.95, 5), (0.90, 50)]
    # → YES asks: [($0.05, 5), ($0.10, 50)]
    # Target $25 but book exhausts at $5.25:
    #   Level 1: 5 contracts @ $0.05 = $0.25
    #   Level 2: 50 contracts @ $0.10 = $5.00  (qty cap hit, not budget)
    # Total: 55 contracts, cost $5.25, avg = $5.25 / 55 ≈ $0.09545
    # Gap: (0.09545 - 0.05) * 100 ≈ 4.55 cents
    client = _mock_client(yes_levels=[], no_levels=[(0.95, 5), (0.90, 50)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.top_ask == pytest.approx(0.05)
    assert result.avg_fill_price == pytest.approx(5.25 / 55, abs=1e-4)
    assert result.levels_consumed == 2
    assert result.top_vs_walked_gap_cents == pytest.approx((5.25 / 55 - 0.05) * 100, abs=0.1)


def test_walk_api_error_returns_none():
    client = MagicMock()
    client.get_orderbook_levels.side_effect = RuntimeError("kalshi 502")
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is None


def test_walk_thin_book_returns_partial_fill():
    # NO bid: (0.99, 2) → YES ask $0.01, only 2 contracts available = $0.02 notional
    client = _mock_client(yes_levels=[], no_levels=[(0.99, 2)])
    result = walk_orderbook(client, "KXBTC-FAKE-B100", side="yes", target_notional_usdc=25.0)
    assert result is not None
    assert result.max_fillable_usdc == pytest.approx(0.02)
    assert result.max_fillable_usdc < 25.0  # partial fill
