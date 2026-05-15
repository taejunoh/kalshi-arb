"""Unit tests for the Phase 1 paper traders.

Cover side selection + freshness gating + price lookup. The DB read +
order_manager call are mocked — those paths are covered elsewhere.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.crypto_observer_phase1 import (
    DeribitTailFadeV1Strategy, BidImbalanceV1Strategy,
    OpenNoTouchV1Strategy, AtmHoldV1Strategy,
)


def _cfg():
    return MagicMock(
        DERIBIT_TAIL_FADE_SIZE_USDC=20.0,
        DERIBIT_TAIL_FADE_MAX_YA=0.35,
        DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S=15.0,
        BID_IMBALANCE_SIZE_USDC=20.0,
        BID_IMBALANCE_SIGNAL_FRESHNESS_S=15.0,
        OPEN_NO_TOUCH_SIZE_USDC=20.0,
        OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S=15.0,
        OPEN_NO_TOUCH_MIN_YA=0.20,
        OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT=3,
        ATM_HOLD_SIZE_USDC=20.0,
        ATM_HOLD_SIGNAL_FRESHNESS_S=15.0,
    )


def _now_iso(seconds_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _build_strategy(cls, *, signals=None, market_lookup=None):
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    db = MagicMock()
    db.fetch_recent_crypto_observer_signals = AsyncMock(return_value=signals or [])
    om._db = db
    scanner = MagicMock()
    scanner.get_market = MagicMock(side_effect=market_lookup or (lambda t: None))
    s = cls(_cfg(), om, scanner, MagicMock())
    return s, om


# ── DeribitTailFadeV1 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deribit_tail_fade_fires_paper_no_on_fresh_signal():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["ticker"] == "KXETH-26MAY1311-B2220"
    assert args["side"] == "no"
    assert args["price"] == 0.97
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "deribit_tail_fade_v1"


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_stale_signal():
    sig = {"id": 1, "ts": _now_iso(30.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_ya_above_max():
    """Cohort evidence covers ya ≤ $0.30. If current market drifted above
    the band, the signal is no longer in-distribution — skip."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_dedups_same_signal_id_within_run():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_predicted_outcome_yes():
    """Observer's tail-skew prediction is always "no". A "yes" prediction
    on this observer would be malformed data — refuse to act on it."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── BidImbalanceV1 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bid_imbalance_fires_yes_on_yes_prediction():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.50,
    }}
    s, om = _build_strategy(BidImbalanceV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "yes"
    assert args["price"] == 0.50


@pytest.mark.asyncio
async def test_bid_imbalance_fires_no_on_no_prediction():
    sig = {"id": 2, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s, om = _build_strategy(BidImbalanceV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.60


@pytest.mark.asyncio
async def test_bid_imbalance_skips_when_market_missing_from_scanner():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-DROPPED",
           "predicted_outcome": "yes"}
    s, om = _build_strategy(BidImbalanceV1Strategy, signals=[sig],
                            market_lookup=lambda t: None)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_fire():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.50,
    }}
    s, om = _build_strategy(BidImbalanceV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    om.is_circuit_breaker_active = MagicMock(return_value=True)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── OpenNoTouchV1 (flipped open_widening) ──────────────────────────


@pytest.mark.asyncio
async def test_open_no_touch_buys_no_on_flipped_signal():
    """Observer `open_no_touch` emits pred="no" (flipped from original
    open_widening's pred="yes"). Trader buys NO at current no_ask."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.60
    assert args["strategy"] == "open_no_touch_v1"


@pytest.mark.asyncio
async def test_open_no_touch_skips_low_ya_tail_bucket():
    """MIN_YA filter: tail buckets (ya < 0.20 → no_ask > 0.80) have
    payoff/fee asymmetry that kills EV even at 90%+ WR. First 37 paper
    trades hit -10.3% ROI because 84% of fires landed in this band.
    Filter drops them — only ATM-region (ya ≥ 0.20) fires."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.05, "no_ask_dollars": 0.95,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_open_no_touch_fires_at_ya_boundary():
    """ya = MIN_YA exactly should fire (≥ comparison)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_open_no_touch_skips_yes_prediction():
    """Defensive: if observer somehow emits pred="yes" on this stream,
    the trader skips rather than buying."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_open_no_touch_per_event_cap():
    """ATM±1% trigger fires on every bucket within the radius — for a
    KXBTC event that's 10-16 buckets. Without the cap, all 10+ trades
    share the same close outcome (correlated). Cap=3 keeps trades
    independent enough for statistical confidence."""
    # 5 distinct buckets, all under the same event_key 26MAY1311.
    sigs = [
        {"id": i, "ts": _now_iso(2.0),
         "market_id": f"KXBTC-26MAY1311-B{82000 + i * 100}",
         "event_key": "26MAY1311",
         "predicted_outcome": "no"}
        for i in range(5)
    ]
    markets = {
        f"KXBTC-26MAY1311-B{82000 + i * 100}": {
            "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
        }
        for i in range(5)
    }
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=sigs,
                            market_lookup=markets.get)
    await s.scan_and_execute()
    # Cap=3 — only first 3 sigs fire, last 2 skipped.
    assert om.market_order.await_count == 3


@pytest.mark.asyncio
async def test_open_no_touch_cap_isolates_per_event():
    """Cap counts are per-event_key — a different event resets the
    counter, so different events can each get up to N fires."""
    sigs = [
        # 3 trades on event A (hits cap)
        {"id": 1, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82000", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 2, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82100", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 3, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82200", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        # 1 trade on event B (well under cap)
        {"id": 4, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1312-B82000", "event_key": "26MAY1312",
         "predicted_outcome": "no"},
    ]
    markets = {sig["market_id"]: {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    } for sig in sigs}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=sigs,
                            market_lookup=markets.get)
    await s.scan_and_execute()
    # 3 fires on event A + 1 fire on event B = 4 total.
    assert om.market_order.await_count == 4


# ── AtmHoldV1 (flipped atm_fade) ───────────────────────────────────


@pytest.mark.asyncio
async def test_atm_hold_buys_yes_on_flipped_signal():
    """Observer `atm_hold` emits pred="yes" (flipped from original
    atm_fade's pred="no"). Trader buys YES at current ya."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.55, "no_ask_dollars": 0.45,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "yes"
    assert args["price"] == 0.55
    assert args["strategy"] == "atm_hold_v1"


@pytest.mark.asyncio
async def test_atm_hold_skips_no_prediction():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.55, "no_ask_dollars": 0.45,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── OpenNoTouchV1 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_no_touch_fires_no_at_no_ask():
    from strategies.crypto_observer_phase1 import OpenNoTouchV1Strategy
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1410-B2200",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1410-B2200": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.80
    assert args["strategy"] == "open_no_touch_v1"


@pytest.mark.asyncio
async def test_open_no_touch_skips_if_predicted_yes():
    from strategies.crypto_observer_phase1 import OpenNoTouchV1Strategy
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1410-B2200",
           "predicted_outcome": "yes"}  # malformed for this trader
    markets = {"KXETH-26MAY1410-B2200": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── AtmHoldV1 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_atm_hold_fires_yes_at_yes_ask():
    from strategies.crypto_observer_phase1 import AtmHoldV1Strategy
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1410-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1410-B2270": {
        "yes_ask_dollars": 0.55, "no_ask_dollars": 0.45,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "yes"
    assert args["price"] == 0.55
    assert args["strategy"] == "atm_hold_v1"


@pytest.mark.asyncio
async def test_atm_hold_skips_if_predicted_no():
    from strategies.crypto_observer_phase1 import AtmHoldV1Strategy
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1410-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1410-B2270": {
        "yes_ask_dollars": 0.55, "no_ask_dollars": 0.45,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0
