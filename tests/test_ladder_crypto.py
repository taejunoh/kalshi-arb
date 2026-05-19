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


@pytest.mark.asyncio
async def test_phase0_fade_logs_with_walked_slippage(monkeypatch):
    """Fade path: walked > top → would_fire=False; correct action_taken."""
    from core import orderbook_walk

    def fake_walk(client, ticker, side, target_notional_usdc):
        # Walked price 4c above top — exceeds 2c gap threshold
        return orderbook_walk.WalkResult(
            avg_fill_price=0.14, max_fillable_usdc=25.0,
            levels_consumed=2, top_ask=0.10, top_vs_walked_gap_cents=4.0,
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

    fade_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_fade_log"
    ]
    assert len(fade_calls) == 1
    kw = fade_calls[0].kwargs
    assert kw["would_fire"] is False
    # fade walked_edge = inversion_amount - (walked_avg - top_ask) - fee
    # inversion = 0.70 - 0.10 = 0.60 (t2.yes_bid - t1.yes_ask)
    # slippage = 0.14 - 0.10 = 0.04
    # walked_edge = 0.60 - 0.04 - 0.07 = 0.49
    assert kw["walked_edge_usdc"] == pytest.approx(0.49, abs=0.01)


@pytest.mark.asyncio
async def test_phase0_bucketed_buy_no_payoff_n_minus_1(monkeypatch):
    """Bucketed BUY-NO arb uses payoff n-1, not 1.

    Construct 4 legs whose yes_bids sum to 1.40 (sum_mid in [0.90, 1.10]
    range fails — so use yes_bid=0.35, yes_ask=0.35: sum_mid=1.40 → above
    bucketed gate). To get inside the gate, use yes_bid=0.27 (sum=1.08 OK)
    and let walked simulate a BUY-NO arb where total NO cost is high.

    Math check: 4 legs, each yes_bid=0.27 → no_bid=0.73, no_ask=0.73.
    sum_yes_bid = 1.08 (passes BUCKETED_SUM_MID gate).
    Bucketed BUY-NO arb: cost = (n - sum_yes_bid) + n*fee = 2.92 + 0.28 = 3.20
                         payoff = n-1 = 3.0; edge = 3.0 - 3.20 = -0.20 NEGATIVE
    So this scenario won't be detected as arb by find_arb_opportunities.

    Instead, mock find_arb_opportunities to inject a bucketed BUY-NO arb
    directly, then verify _log_phase0_arb's payoff branch.
    """
    from core import orderbook_walk
    from core.ladder_framework import ArbTrade, ArbLeg

    # Skip the framework gate — directly construct a bucketed BUY-NO arb
    bucketed_buy_no_arb = ArbTrade(
        group_key="KXBTC-FAKE",
        legs=tuple(
            ArbLeg(ticker=f"KXBTC-FAKE-B{i}", side="no", size_usdc=12.5, price=0.73)
            for i in range(4)
        ),
        expected_edge_usdc=0.0,
    )
    monkeypatch.setattr(
        "strategies.ladder_crypto.find_arb_opportunities",
        lambda *a, **kw: [bucketed_buy_no_arb],
    )
    monkeypatch.setattr(
        "strategies.ladder_crypto.find_fade_opportunities",
        lambda *a, **kw: [],
    )

    def fake_walk(client, ticker, side, target_notional_usdc):
        return orderbook_walk.WalkResult(
            avg_fill_price=0.73, max_fillable_usdc=25.0,
            levels_consumed=1, top_ask=0.73, top_vs_walked_gap_cents=0.0,
        )
    monkeypatch.setattr("strategies.ladder_crypto.walk_orderbook", fake_walk)

    cfg = _make_config()
    om = MagicMock(); om.market_order = AsyncMock()
    db = MagicMock(); db.record_ladder_violation = AsyncMock(return_value=1)
    strat = LadderCryptoStrategy(cfg, om, MagicMock(), MagicMock())
    strat._db = db

    # Bucketed legs to satisfy BUCKETED_SUM_MID gate (need 0.90 <= sum_mid <= 1.10)
    bucketed_legs = tuple(
        LadderLeg(
            ticker=f"KXBTC-FAKE-B{i}", threshold=float(i),
            yes_ask=0.27, yes_bid=0.27, no_ask=0.73, no_bid=0.73,
            yes_depth=100.0, no_depth=100.0,
        )
        for i in range(4)
    )
    group = LadderGroup(series="KXBTC", event_key="FAKE",
                        ladder_type="bucketed", direction="asc",
                        legs=bucketed_legs)
    await strat._handle_group(group)

    arb_calls = [
        c for c in db.record_ladder_violation.await_args_list
        if c.kwargs.get("action_taken") == "phase0_arb_log"
    ]
    assert len(arb_calls) == 1
    kw = arb_calls[0].kwargs
    # walked: 4 × $0.73 = $2.92, fee = 4 × 0.07 = $0.28, payoff = n-1 = 3.0
    # walked_edge = 3.0 - 2.92 - 0.28 = -0.20 (negative, would_fire=False)
    # BUT verify the payoff branch was taken: with WRONG payoff=1.0, edge = 1.0 - 2.92 - 0.28 = -2.20
    assert kw["walked_edge_usdc"] == pytest.approx(-0.20, abs=0.01)
    assert kw["would_fire"] is False
