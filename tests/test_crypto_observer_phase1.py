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
    BidImbalanceFadeV1Strategy,
    LateCloseTailV1Strategy, DeribitAtmFadeV1Strategy,
)


def _cfg():
    return MagicMock(
        DERIBIT_TAIL_FADE_SIZE_USDC=20.0,
        DERIBIT_TAIL_FADE_MAX_YA=0.20,
        DERIBIT_TAIL_FADE_MIN_YA=0.08,
        DERIBIT_TAIL_FADE_MIN_FIRE_CONTRACTS=3.0,
        DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S=15.0,
        BID_IMBALANCE_SIZE_USDC=20.0,
        BID_IMBALANCE_SIGNAL_FRESHNESS_S=15.0,
        BID_IMBALANCE_MIN_PRICE=0.50,
        BID_IMBALANCE_FADE_SIZE_USDC=20.0,
        BID_IMBALANCE_FADE_SIGNAL_FRESHNESS_S=15.0,
        OPEN_NO_TOUCH_SIZE_USDC=20.0,
        OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S=15.0,
        OPEN_NO_TOUCH_MIN_YA=0.30,
        OPEN_NO_TOUCH_MAX_YA=0.45,
        OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT=1,
        ATM_HOLD_SIZE_USDC=20.0,
        ATM_HOLD_SIGNAL_FRESHNESS_S=15.0,
        LATE_CLOSE_TAIL_SIZE_USDC=20.0,
        LATE_CLOSE_TAIL_SIGNAL_FRESHNESS_S=15.0,
        LATE_CLOSE_TAIL_MIN_YA=0.05,
        LATE_CLOSE_TAIL_MAX_YA=0.30,
        LATE_CLOSE_TAIL_MIN_FIRE_CONTRACTS=3.0,
        DERIBIT_ATM_FADE_SIZE_USDC=20.0,
        DERIBIT_ATM_FADE_SIGNAL_FRESHNESS_S=15.0,
        DERIBIT_ATM_FADE_MIN_YA=0.30,
        DERIBIT_ATM_FADE_MAX_YA=0.70,
        DERIBIT_ATM_FADE_MIN_FIRE_CONTRACTS=3.0,
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
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["ticker"] == "KXETH-26MAY1311-B2220"
    assert args["side"] == "no"
    assert args["price"] == 0.85
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "deribit_tail_fade_v1"


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_stale_signal():
    sig = {"id": 1, "ts": _now_iso(30.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_ya_above_max():
    """2026-05-18: MAX_YA tightened 0.30→0.20. no_ask <0.80 band (n=4)
    showed 50% WR / -54% ROI — signal mis-calibration zone."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.25, "no_ask_dollars": 0.75,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_ya_below_min():
    """2026-05-18: MIN_YA=0.08 added. At no_ask≥0.92 the win payoff
    ($0.01-0.07) can't cover the rare full loss; n=126 contributed
    near-zero PnL on $2,453 stake before the cap."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_clamps_size_when_book_thin():
    """2026-05-18: top-of-book depth-aware sizing. When no_ask_size_fp
    is below the target contract count, clamp order to fillable depth
    (avoids FOK reject)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    # target_count = 20 / 0.85 ≈ 23.5, depth is 10 → clamp to 10 contracts
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 10.0,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    # 10 contracts at $0.85 = $8.50
    assert args["size_usdc"] == pytest.approx(10.0 * 0.85)


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_depth_below_min():
    """2026-05-18: if top-of-book depth < MIN_FIRE_CONTRACTS, don't bother
    firing a dust trade — the slot in trade history isn't worth it."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 2.0,  # below MIN_FIRE_CONTRACTS=3
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_depth_missing():
    """If the scanner has no top-of-book size field, don't fire blind.
    Defensive against partial scanner snapshots."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        # no_ask_size_fp intentionally missing
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
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
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
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
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


@pytest.mark.asyncio
async def test_bid_imbalance_skips_when_price_below_min():
    """After 2026-05-15 cohort re-analysis, original direction is only +EV
    at the rich end. MIN_PRICE=0.50 drops fires below that band."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(BidImbalanceV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── BidImbalanceFadeV1 (ya<0.60 conditional flip) ──────────────────


@pytest.mark.asyncio
async def test_bid_imbalance_fade_buys_no_on_flipped_signal():
    """Observer `bid_imbalance_fade` emits the side flipped from the
    original (pred=no in this case, original would have been pred=yes).
    Trader buys NO at the current no_ask."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2200",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2200": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(BidImbalanceFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.80
    assert args["strategy"] == "bid_imbalance_fade_v1"


@pytest.mark.asyncio
async def test_bid_imbalance_fade_buys_yes_on_flipped_no_signal():
    """When the original would have predicted "no" (nb>yb), the flipped
    observer emits pred="yes" — trader buys YES at the current yes_ask."""
    sig = {"id": 2, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2300",
           "predicted_outcome": "yes"}
    markets = {"KXETH-26MAY1311-B2300": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s, om = _build_strategy(BidImbalanceFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "yes"
    assert args["price"] == 0.40


@pytest.mark.asyncio
async def test_bid_imbalance_fade_skips_stale_signal():
    sig = {"id": 1, "ts": _now_iso(30.0), "market_id": "KXETH-26MAY1311-B2200",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2200": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
    }}
    s, om = _build_strategy(BidImbalanceFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
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
    """Tail buckets (ya < MIN_YA → no_ask > 0.70) had -4 to -5% ROI in
    Phase 0 re-analysis (n=628 across <0.30 bands) — fee asymmetry
    kills EV even at 88-93% WR. Window gate drops them."""
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
async def test_open_no_touch_skips_when_ya_above_max():
    """Upper window gate: ya ≥ MAX_YA (no_ask ≤ 0.55) was -8% to -42%
    ROI in Phase 0 — WR collapses to 45-72% once strikes get close
    enough to ATM that the no-touch thesis fails."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.50,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_open_no_touch_fires_at_ya_min_boundary():
    """ya = MIN_YA exactly should fire (≥ on lower bound)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    # no_ask=0.70 → ya = 1 - 0.70 = 0.30 = MIN_YA
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.25, "no_ask_dollars": 0.70,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_open_no_touch_uses_no_ask_not_yes_ask_for_window():
    """Crypto books can have yes_ask + no_ask < 1 (wide spread). The
    window gate must derive ya from no_ask, not read yes_ask_dollars
    directly — otherwise tail strikes (no_ask > 0.70) slip past the
    ya≥0.30 gate when yes_ask is misleadingly large."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2270",
           "predicted_outcome": "no"}
    # yes_ask 0.35 alone would pass a naive "ya >= 0.30" gate, but the
    # real no_ask 0.85 means ya = 1 - 0.85 = 0.15 → should skip.
    markets = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.35, "no_ask_dollars": 0.85,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


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
async def test_open_no_touch_caps_at_one_fire_per_event():
    """ATM±1% strikes all resolve on the same BTC/ETH hourly close
    (correlated samples). Cap=1 keeps samples independent — 5 eligible
    strikes in one event = 1 fire, not 5."""
    # 5 distinct buckets all in the ya band, same event_key.
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
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_open_no_touch_picks_band_center_strike_per_event():
    """When multiple eligible strikes exist for one event, pick the one
    whose ya is closest to the band center (MIN+MAX)/2 = 0.375 — that's
    the EV maximum within the [0.30, 0.45) window."""
    # Three eligible strikes within band, with ya = 0.32, 0.38, 0.44.
    # Closest to 0.375 is strike B (ya 0.38 → distance 0.005). Should
    # fire on B and skip A and C.
    sigs = [
        {"id": 1, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82000", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 2, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82100", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 3, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82200", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
    ]
    markets = {
        "KXBTC-26MAY1311-B82000": {"yes_ask_dollars": 0.30, "no_ask_dollars": 0.68},  # ya 0.32
        "KXBTC-26MAY1311-B82100": {"yes_ask_dollars": 0.35, "no_ask_dollars": 0.62},  # ya 0.38
        "KXBTC-26MAY1311-B82200": {"yes_ask_dollars": 0.40, "no_ask_dollars": 0.56},  # ya 0.44
    }
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=sigs,
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    assert om.market_order.await_args.kwargs["ticker"] == "KXBTC-26MAY1311-B82100"


@pytest.mark.asyncio
async def test_open_no_touch_cap_isolates_per_event():
    """Cap counts are per-event_key — different events each get their
    own fire even with cap=1."""
    sigs = [
        {"id": 1, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82000", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 2, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1311-B82100", "event_key": "26MAY1311",
         "predicted_outcome": "no"},
        {"id": 3, "ts": _now_iso(2.0),
         "market_id": "KXBTC-26MAY1312-B82000", "event_key": "26MAY1312",
         "predicted_outcome": "no"},
    ]
    markets = {sig["market_id"]: {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    } for sig in sigs}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=sigs,
                            market_lookup=markets.get)
    await s.scan_and_execute()
    # 1 fire on event A + 1 fire on event B = 2 total.
    assert om.market_order.await_count == 2


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


# ── LateCloseTailV1 (2026-05-18) ───────────────────────────────────


@pytest.mark.asyncio
async def test_late_close_tail_fires_no_on_mid_otm_signal():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.85
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "late_close_tail_v1"


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_ya_above_max():
    """ya > 0.30 = near-ATM; protocol shifts to deribit_atm_fade territory."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_ya_below_min():
    """ya < 0.05 = tail_decay zone; don't double-fire."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.03, "no_ask_dollars": 0.97,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_late_close_tail_clamps_size_when_book_thin():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 10.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["size_usdc"] == pytest.approx(10.0 * 0.85)


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_depth_below_min():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 2.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_predicted_yes():
    """Observer always emits 'no' — guard against schema drift."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "no_ask_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── DeribitAtmFadeV1 (2026-05-18) ──────────────────────────────────


@pytest.mark.asyncio
async def test_deribit_atm_fade_fires_no_on_atm_overshoot():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.60, "no_ask_dollars": 0.40,
        "no_ask_size_fp": 100.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.40
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "deribit_atm_fade_v1"


@pytest.mark.asyncio
async def test_deribit_atm_fade_skips_when_ya_above_max():
    """ya > 0.70 is the deribit_tail_fade zone (different price geometry)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.75, "no_ask_dollars": 0.25,
        "no_ask_size_fp": 100.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_atm_fade_skips_when_ya_below_min():
    """ya < 0.30 = the leg isn't ATM at all → observer shouldn't fire,
    trader double-checks live price."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
        "no_ask_size_fp": 100.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_atm_fade_clamps_size_when_book_thin():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    # target_count = 20/0.40 = 50; depth=20 → clamp to 20 * 0.40 = $8
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.60, "no_ask_dollars": 0.40,
        "no_ask_size_fp": 20.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["size_usdc"] == pytest.approx(20.0 * 0.40)


@pytest.mark.asyncio
async def test_deribit_atm_fade_skips_when_predicted_yes():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.60, "no_ask_dollars": 0.40,
        "no_ask_size_fp": 100.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0
