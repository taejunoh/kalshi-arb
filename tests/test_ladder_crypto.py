"""Tests for ladder_crypto Phase 0 _handle_group logging path."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.ladder_crypto import LadderCryptoStrategy
from core.ladder_framework import LadderGroup, LadderLeg


def _make_config():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.LADDER_MIN_ARB_EDGE_USDC = 0.02
    cfg.LADDER_MIN_FADE_INVERSION = 0.05
    cfg.LADDER_MIN_DEPTH_USDC = 30.0
    cfg.LADDER_MAX_POSITION_PER_GROUP = 50.0
    cfg.LADDER_PHASE0_MIN_WALKED_EDGE_USDC = 0.15
    cfg.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS = 2.0
    cfg.LADDER_PHASE0_PROBE_NOTIONAL_USDC = 25.0
    cfg.LADDER_PHASE0_ENABLED = True
    return cfg


def _cumulative_legs(t1_yes_ask=0.10, t2_yes_ask=0.70):
    """Two cumulative legs with inversion-style ask gap.

    t1 (lower threshold), t2 (higher). Framework cumulative arb gate:
      t2.yes_bid > t1.yes_ask  AND  t1.yes_ask + t2.no_ask + 2*fee < 1 - min_edge
    Default: t1.yes_ask=0.10, t2.no_ask=0.30, cost=0.40, edge=1-0.40-0.14=0.46.
    """
    return (
        LadderLeg(
            ticker="KXBTC-26MAY1915-T66250", threshold=66250.0,
            yes_ask=t1_yes_ask, yes_bid=t1_yes_ask,
            no_ask=round(1.0 - t1_yes_ask, 4), no_bid=round(1.0 - t1_yes_ask, 4),
            yes_depth=100.0, no_depth=100.0,
        ),
        LadderLeg(
            ticker="KXBTC-26MAY1915-T66350", threshold=66350.0,
            yes_ask=t2_yes_ask, yes_bid=t2_yes_ask,
            no_ask=round(1.0 - t2_yes_ask, 4), no_bid=round(1.0 - t2_yes_ask, 4),
            yes_depth=100.0, no_depth=100.0,
        ),
    )


@pytest.mark.asyncio
async def test_phase0_would_fire_logs_violation(monkeypatch):
    """Cumulative arb with deep fillable books, no phantom → would_fire=True."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        # Return walked == top-of-book (no phantom). Prices match
        # _cumulative_legs defaults: t1.yes_ask=$0.10, t2.no_ask=$0.30.
        price = 0.10 if "T66250" in ticker else 0.30
        return orderbook_walk.WalkResult(
            avg_fill_price=price, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=price, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock()
    om.is_circuit_breaker_active.return_value = False
    om.market_order = AsyncMock()
    db = MagicMock()
    db.record_ladder_violation = AsyncMock(return_value=1)
    client = MagicMock()
    scanner = MagicMock()

    strat = LadderCryptoStrategy(cfg, om, scanner, client)
    strat._db = db  # injected; see Step 3.4

    legs = _cumulative_legs()  # framework detects arb edge $0.46
    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc", legs=legs)

    await strat._handle_group(group)

    assert om.market_order.await_count == 0, "Phase 0 must not fire"
    assert db.record_ladder_violation.await_count >= 1
    # Find the arb log (action_taken='phase0_arb_log')
    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    kw = arb_calls[0].kwargs
    assert kw["would_fire"] is True
    assert kw["phase"] == "phase0_paper"
    # walked_edge = 1 - (0.10 + 0.30) - 2*0.07 = 0.46
    assert kw["walked_edge_usdc"] == pytest.approx(0.46, abs=0.01)


@pytest.mark.asyncio
async def test_phase0_phantom_quote_rejected(monkeypatch):
    """Top-vs-walked gap > 2c on any leg → would_fire=False."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        # t1.yes leg has 3c walked gap (phantom); t2.no leg is clean
        if "T66250" in ticker:
            return orderbook_walk.WalkResult(
                avg_fill_price=0.13, max_fillable_usdc=25.0,
                levels_consumed=3, top_ask=0.10, top_vs_walked_gap_cents=3.0,
            )
        return orderbook_walk.WalkResult(
            avg_fill_price=0.30, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=0.30, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock(return_value=1)
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    kw = arb_calls[0].kwargs
    assert kw["would_fire"] is False
    assert kw["top_vs_walked_max_gap_cents"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_phase0_thin_book_rejected(monkeypatch):
    """max_fillable_usdc < probe_notional on any leg → would_fire=False."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        if "T66250" in ticker:
            # t1.yes leg only fillable for $5 (thin)
            return orderbook_walk.WalkResult(
                avg_fill_price=0.10, max_fillable_usdc=5.0,
                levels_consumed=1, top_ask=0.10, top_vs_walked_gap_cents=0.0,
            )
        return orderbook_walk.WalkResult(
            avg_fill_price=0.30, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=0.30, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock(return_value=1)
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    assert arb_calls[0].kwargs["would_fire"] is False


@pytest.mark.asyncio
async def test_phase0_no_market_order_calls(monkeypatch):
    """Strict invariant: Phase 0 NEVER calls order_manager.market_order."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        price = 0.10 if "T66250" in ticker else 0.30
        return orderbook_walk.WalkResult(
            avg_fill_price=price, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=price, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock()
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    group = LadderGroup(series="KXBTC", event_key="26MAY1915-1500",
                        ladder_type="cumulative", direction="asc",
                        legs=_cumulative_legs())
    await strat._handle_group(group)

    assert om.market_order.await_count == 0
