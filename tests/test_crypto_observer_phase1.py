"""Unit tests for the Phase 1 paper traders.

Cover side selection + freshness gating + price lookup. The DB read +
order_manager call are mocked — those paths are covered elsewhere.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.crypto_observer_phase1 import (
    DeribitTailFadeV1Strategy, DeribitTailFadeHighV1Strategy,
    BidImbalanceV1Strategy,
    OpenNoTouchV1Strategy, AtmHoldV1Strategy,
    BidImbalanceFadeV1Strategy,
    LateCloseTailV1Strategy, DeribitAtmFadeV1Strategy,
    CmeBasisFadeV1Strategy,
)


def _cfg():
    return MagicMock(
        DERIBIT_TAIL_FADE_SIZE_USDC=20.0,
        DERIBIT_TAIL_FADE_MAX_YA=0.20,
        DERIBIT_TAIL_FADE_MIN_YA=0.08,
        DERIBIT_TAIL_FADE_MAX_NO_ASK=0.95,
        DERIBIT_TAIL_FADE_EXCLUDE_ASSETS="KXBTC",
        DERIBIT_TAIL_FADE_MIN_FIRE_CONTRACTS=3.0,
        DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S=15.0,
        DERIBIT_TAIL_FADE_HIGH_SIZE_USDC=20.0,
        DERIBIT_TAIL_FADE_HIGH_MIN_YA=0.65,
        DERIBIT_TAIL_FADE_HIGH_MAX_YA=0.95,
        DERIBIT_TAIL_FADE_HIGH_SIGNAL_FRESHNESS_S=15.0,
        DERIBIT_TAIL_FADE_HIGH_MIN_FIRE_CONTRACTS=5.0,
        DERIBIT_TAIL_FADE_HIGH_MAX_FIRES_PER_EVENT=2,
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
        OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_LOW=0.40,
        OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_HIGH=0.60,
        ATM_HOLD_SIZE_USDC=20.0,
        ATM_HOLD_SIGNAL_FRESHNESS_S=15.0,
        LATE_CLOSE_TAIL_SIZE_USDC=20.0,
        LATE_CLOSE_TAIL_SIGNAL_FRESHNESS_S=15.0,
        LATE_CLOSE_TAIL_MIN_YA=0.05,
        LATE_CLOSE_TAIL_MAX_YA=0.30,
        LATE_CLOSE_TAIL_MIN_FIRE_CONTRACTS=3.0,
        LATE_CLOSE_TAIL_MAX_NO_ASK=1.0,
        LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_LOW=0.0,
        LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_HIGH=0.0,
        DERIBIT_ATM_FADE_SIZE_USDC=20.0,
        DERIBIT_ATM_FADE_SIGNAL_FRESHNESS_S=15.0,
        DERIBIT_ATM_FADE_MIN_YA=0.30,
        DERIBIT_ATM_FADE_MAX_YA=0.70,
        DERIBIT_ATM_FADE_MIN_FIRE_CONTRACTS=3.0,
        CME_BASIS_FADE_SIZE_USDC=20.0,
        CME_BASIS_FADE_SIGNAL_FRESHNESS_S=15.0,
        CME_BASIS_FADE_MIN_YA=0.17,
        CME_BASIS_FADE_MAX_YA=0.42,
        CME_BASIS_FADE_MIN_FIRE_CONTRACTS=3.0,
        CME_BASIS_FADE_MAX_NO_ASK=1.0,
        CME_BASIS_FADE_MAX_FIRES_PER_EVENT=999,
        PHASE1_TICKER_COOLDOWN_S=30.0,
        PHASE1_TICKER_PRICE_DIFF=0.03,
    )


def _now_iso(seconds_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _build_strategy(cls, *, signals=None, market_lookup=None, last_trade=None,
                    event_fire_count=0):
    """Construct a Phase 1 strategy with mocked OM/DB/scanner.

    `last_trade`: dict with keys {timestamp, price} or None. Returned by
    db.get_last_trade_for_ticker — controls the per-ticker cooldown gate
    (2026-05-20). Default None = no prior fire, cooldown inactive.

    `event_fire_count`: int returned by db.count_strategy_fires_for_event.
    Controls per-event fire cap (2026-05-21 cme_basis_fade hardening).
    """
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    db = MagicMock()
    db.fetch_recent_crypto_observer_signals = AsyncMock(return_value=signals or [])
    db.get_last_trade_for_ticker = AsyncMock(return_value=last_trade)
    db.count_strategy_fires_for_event = AsyncMock(return_value=event_fire_count)
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
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 50.0,
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
async def test_deribit_tail_fade_skips_excluded_asset():
    """2026-05-21: KXBTC excluded by default. Pre-cap ex-mega audit
    (n=75) showed KXBTC −$74.44 / ROI −4.96%, 4/5 −$20 wipes at NO<0.95
    (cap doesn't defend). Same gate would have fired on KXETH OK."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2108-B77150",
           "predicted_outcome": "no"}
    # In-band ya + no_ask, depth available — only the asset exclude blocks
    markets = {"KXBTC-26MAY2108-B77150": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_skips_when_no_ask_above_cap():
    """2026-05-20: MAX_NO_ASK=0.95. Paper n=148 at no_ask>=0.97 = -$18.58
    total / -$0.13/fire — net loser even at 100% paper fill rate. Plus all
    5 live FOK rejects (5/19 KXETH-26MAY1919-B2090 burst) were at 0.98.
    Wide-spread cells slip past the ya gate (yes_ask=0.10 + no_ask=0.98 =
    8c spread), so we cap no_ask directly."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1919-B2090",
           "predicted_outcome": "no"}
    # ya = 0.10 (in [0.08, 0.20]), but no_ask = 0.98 (above cap 0.95)
    markets = {"KXETH-26MAY1919-B2090": {
        "yes_ask_dollars": 0.10, "no_ask_dollars": 0.98,
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 10.0,
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
        "yes_bid_size_fp": 2.0,  # below MIN_FIRE_CONTRACTS=3
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
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# ── DeribitTailFadeHighV1 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_fires_paper_no_at_high_ya():
    """Lifetime observer n=1,880: high-ya 0.65-0.95 buckets show 80-94% WR
    on the same observer (deribit_tail_skew) the parent trader uses. Sister
    strategy covers this near-expiry near-strike subset."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.88, "no_ask_dollars": 0.12,
        "yes_bid_size_fp": 200.0,  # >= target 20/0.12 ≈ 167
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["ticker"] == "KXBTC-26MAY1418-B80950"
    assert args["side"] == "no"
    assert args["price"] == 0.12
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "deribit_tail_fade_high_v1"


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_skips_when_ya_below_min():
    """ya 0.50-0.55 lifetime: WR 68% / +$0.18/ct — covered by parent in
    range it allows; this trader specifically targets the 0.65+ tail."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.50,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_skips_when_ya_above_max():
    """ya >= 0.95 zone: NO costs <= $0.05/ct, but n=7 lifetime at 0.95-1.00
    showed WR 43% (random). Cap upper bound to keep payoff math sensible."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.97, "no_ask_dollars": 0.03,
        "yes_bid_size_fp": 500.0,
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_clamps_size_when_book_thin():
    """High-ya NO-side books are typically thin near expiry. Depth-aware
    sizing clamps to top-of-book — paper ledger stays honest about
    fill-realism for the eventual LIVE promotion decision."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    # target_count = 20/0.12 ≈ 167, depth 50 → clamp to 50*0.12 = $6
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.88, "no_ask_dollars": 0.12,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["size_usdc"] == pytest.approx(50.0 * 0.12)


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_skips_when_depth_below_min():
    """MIN_FIRE_CONTRACTS=5 (vs parent's 1). High-ya signals fire on thin
    books and the dust-trade threshold needs to be high enough to filter
    sub-economic fires that would inflate the paper ledger size."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.88, "no_ask_dollars": 0.12,
        "yes_bid_size_fp": 3.0,  # below MIN_FIRE_CONTRACTS=5
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_skips_when_predicted_yes():
    """Observer always emits pred='no' for tail-skew signals; defensive
    against future feature changes that would add yes predictions."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.88, "no_ask_dollars": 0.12,
        "yes_bid_size_fp": 500.0,
    }}
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_deribit_tail_fade_high_caps_at_max_fires_per_event():
    """Per-event cap=2 (LIVE soft-launch config 2026-05-21). The base
    class's _max_fires_per_event is now wired through the config field.
    With event_fire_count=2 already on the event, the 3rd signal in the
    same event must skip."""
    sig = {"id": 1, "ts": _now_iso(2.0),
           "market_id": "KXBTC-26MAY1418-B80950",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1418-B80950": {
        "yes_ask_dollars": 0.88, "no_ask_dollars": 0.12,
        "yes_bid_size_fp": 500.0,
    }}
    # event_fire_count returns 2 → cap=2 reached → no order placed.
    s, om = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                            market_lookup=markets.get, event_fire_count=2)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0

    # With event_fire_count=1, cap=2 not reached → fires.
    s2, om2 = _build_strategy(DeribitTailFadeHighV1Strategy, signals=[sig],
                              market_lookup=markets.get, event_fire_count=1)
    await s2.scan_and_execute()
    assert om2.market_order.await_count == 1


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
async def test_open_no_touch_skips_kxeth_wipe_band():
    """KXETH / KXETHD × no_ask ∈ [0.40, 0.60) was a structural wipe in
    Phase 1 paper 14d (n=69, -$982, WR 7%, 8/8 days negative). Same band
    on KXBTC is +EV, so the gate is asset-prefix scoped — not a global
    MAX_YA shift. Bound is exclusive on top to match ya<MAX_YA."""
    # KXETHD daily (T-strike) at no_ask=0.55 — inside wipe band, ya=0.45.
    # Test ya gate must allow ya=0.45 → widen the in-test MAX_YA.
    sig = {"id": 1, "ts": _now_iso(2.0),
           "market_id": "KXETHD-26MAY2023-T2139.99",
           "predicted_outcome": "no"}
    markets = {"KXETHD-26MAY2023-T2139.99": {
        "yes_ask_dollars": 0.45, "no_ask_dollars": 0.55,
    }}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._max_ya = 0.50  # widen so ya=0.45 fits the ya window
    await s.scan_and_execute()
    assert om.market_order.await_count == 0

    # KXBTC at the same no_ask=0.55 should still fire (asset-scoped gate).
    sig2 = {"id": 2, "ts": _now_iso(2.0),
            "market_id": "KXBTC-26MAY2023-B77000",
            "predicted_outcome": "no"}
    markets2 = {"KXBTC-26MAY2023-B77000": {
        "yes_ask_dollars": 0.45, "no_ask_dollars": 0.55,
    }}
    s2, om2 = _build_strategy(OpenNoTouchV1Strategy, signals=[sig2],
                              market_lookup=markets2.get)
    s2._max_ya = 0.50
    await s2.scan_and_execute()
    assert om2.market_order.await_count == 1

    # KXETH at no_ask=0.60 (top of band, exclusive) should still fire.
    sig3 = {"id": 3, "ts": _now_iso(2.0),
            "market_id": "KXETH-26MAY1311-B2270",
            "predicted_outcome": "no"}
    markets3 = {"KXETH-26MAY1311-B2270": {
        "yes_ask_dollars": 0.40, "no_ask_dollars": 0.60,
    }}
    s3, om3 = _build_strategy(OpenNoTouchV1Strategy, signals=[sig3],
                              market_lookup=markets3.get)
    await s3.scan_and_execute()
    assert om3.market_order.await_count == 1


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
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 50.0,
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
        "yes_bid_size_fp": 10.0,
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
        "yes_bid_size_fp": 2.0,
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
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


# 2026-05-21 LIVE-promo pre-coding: MAX_NO_ASK + EXCLUDE_NO_ASK gates.
# Defaults are no-op; these tests assert the active-threshold behavior so
# the gates are safe to flip via env var at the 6/03 audit checkpoint.


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_no_ask_exceeds_max_no_ask():
    """LIVE-promo gate: cap no_ask at 0.94 to avoid rounding-residual zone."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.05, "no_ask_dollars": 0.96,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._max_no_ask = 0.94
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_late_close_tail_fires_at_max_no_ask_boundary():
    """no_ask == MAX_NO_ASK is inclusive (passes)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.06, "no_ask_dollars": 0.94,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._max_no_ask = 0.94
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_late_close_tail_default_max_no_ask_is_no_op():
    """Default MAX_NO_ASK=1.0 must not block any in-band fire (paper parity)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.02, "no_ask_dollars": 0.98,
        "yes_bid_size_fp": 50.0,
    }}
    # Note: ya=0.02 < MIN_YA=0.05 would skip; pick ya inside band.
    markets["KXBTC-26MAY1815-B91000"]["yes_ask_dollars"] = 0.05
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_late_close_tail_skips_when_no_ask_in_exclude_band():
    """LIVE-promo gate: skip the 0.88-0.89 cliff bucket."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.12, "no_ask_dollars": 0.88,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._exclude_no_ask_low = 0.88
    s._exclude_no_ask_high = 0.89
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_late_close_tail_fires_outside_exclude_band():
    """Bands neighboring the cliff (0.87 / 0.90) must still fire."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.13, "no_ask_dollars": 0.87,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._exclude_no_ask_low = 0.88
    s._exclude_no_ask_high = 0.89
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_late_close_tail_exclude_band_disabled_when_low_ge_high():
    """LOW >= HIGH disables the band (default 0.0/0.0 = inactive)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B91000",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B91000": {
        "yes_ask_dollars": 0.12, "no_ask_dollars": 0.88,
        "yes_bid_size_fp": 50.0,
    }}
    s, om = _build_strategy(LateCloseTailV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    # Defaults already 0.0/0.0 — no_ask=0.88 should fire.
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


# ── DeribitAtmFadeV1 (2026-05-18) ──────────────────────────────────


@pytest.mark.asyncio
async def test_deribit_atm_fade_fires_no_on_atm_overshoot():
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.60, "no_ask_dollars": 0.40,
        "yes_bid_size_fp": 100.0,
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
        "yes_bid_size_fp": 100.0,
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
        "yes_bid_size_fp": 100.0,
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
        "yes_bid_size_fp": 20.0,
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
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(DeribitAtmFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


def test_live_crypto_phase1_use_limit_ttl():
    """LIVE crypto Phase 1 traders must route through limit+TTL, not market.
    Reason: 5/19 22:54-22:58 deribit_tail_fade_v1 had 5 consecutive live
    market orders rejected as `fill_or_kill_insufficient_resting_volume`
    on KXETH-26MAY1919-B2090 NO @ 0.98 — Kalshi crypto tail books are too
    thin for FOK on $20 stake. Same root cause as the WNBA/NHL playoff
    FOK fix that lives in LIMIT_TTL_STRATEGIES already.
    """
    from core.order_manager import LIMIT_TTL_STRATEGIES
    assert "deribit_tail_fade_v1" in LIMIT_TTL_STRATEGIES
    assert "atm_hold_v1" in LIMIT_TTL_STRATEGIES


def test_crypto_phase1_per_strategy_ttl():
    """TTL tuned per-strategy by ROI, not fill-rate alone:

    - atm_hold_v1 = 20s. ATM bucket-flip risk dominates above 30s; 60s
      adverse selection costs ~14% per-trade alpha vs 20s sweet spot.
    - deribit_tail_fade_v1 = 60s (default). Deep tail is stale-immune;
      longer TTL captures more partial fills = more alpha.
    """
    from core.order_manager import (
        LIMIT_TTL_SECONDS, LIMIT_TTL_SECONDS_BY_STRATEGY, _ttl_seconds_for,
    )
    assert LIMIT_TTL_SECONDS == 60  # default unchanged for sports
    assert LIMIT_TTL_SECONDS_BY_STRATEGY.get("atm_hold_v1") == 20
    assert "deribit_tail_fade_v1" not in LIMIT_TTL_SECONDS_BY_STRATEGY
    assert _ttl_seconds_for("atm_hold_v1") == 20
    assert _ttl_seconds_for("deribit_tail_fade_v1") == 60  # inherits default
    assert _ttl_seconds_for("sports_underdog_v31") == 60  # sports fallback


# ── cme_basis_fade_v1 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_cme_basis_fade_fires_no_on_signal_in_band():
    """ya in [0.17, 0.42] + predicted='no' → BUY NO at no_ask."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.25, "no_ask_dollars": 0.74,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["side"] == "no"
    assert args["price"] == 0.74
    assert args["size_usdc"] == 20.0
    assert args["strategy"] == "cme_basis_fade_v1"


@pytest.mark.asyncio
async def test_cme_basis_fade_skips_when_ya_below_min():
    """ya 0.10 < 0.17 → fee-locked zone, skip."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.10, "no_ask_dollars": 0.89,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cme_basis_fade_skips_when_ya_above_max():
    """ya 0.50 > 0.42 → WR drops below BE, skip."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.49,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cme_basis_fade_skips_when_predicted_yes():
    """Observer always predicts 'no'; defensive guard against bad signals."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.25, "no_ask_dollars": 0.74,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cme_basis_fade_clamps_size_when_book_thin():
    """target_count=$20/0.74≈27. depth=20 → clamp to 20×0.74≈$14.8."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1815-B89750",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY1815-B89750": {
        "yes_ask_dollars": 0.25, "no_ask_dollars": 0.74,
        "yes_bid_size_fp": 20.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["size_usdc"] == pytest.approx(20.0 * 0.74)


@pytest.mark.asyncio
async def test_cme_basis_fade_skips_when_no_ask_above_max():
    """no_ask 0.87 > MAX_NO_ASK 0.85 → fee-net-negative zone, skip.
    See [[cme_basis_fade_v1_live_promotion_2026-05-21]] Fix 1."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2112-B77150",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY2112-B77150": {
        "yes_ask_dollars": 0.18, "no_ask_dollars": 0.87,
        "yes_bid_size_fp": 100.0,
    }}
    cfg = _cfg()
    cfg.CME_BASIS_FADE_MAX_NO_ASK = 0.85
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    s._max_no_ask = 0.85
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cme_basis_fade_skips_when_event_fire_cap_reached():
    """8 prior fires on the same event_key → 9th is blocked.
    Defends against cascade losses where one underlying drift wipes many
    adjacent strikes (5/20 KXETH-26MAY2014 cascade -$300 / 5/21 KXBTC-
    26MAY2112-B77150 -$59 cluster). See [[cme_basis_fade_v1_live_promotion_2026-05-21]] Fix 2."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2112-B77250",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY2112-B77250": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get, event_fire_count=8)
    s._max_fires_per_event_cfg = 8
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cme_basis_fade_fires_under_event_cap():
    """7 prior fires < cap=8 → fire proceeds."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2112-B77250",
           "predicted_outcome": "no"}
    markets = {"KXBTC-26MAY2112-B77250": {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.80,
        "yes_bid_size_fp": 100.0,
    }}
    s, om = _build_strategy(CmeBasisFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get, event_fire_count=7)
    s._max_fires_per_event_cfg = 8
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


# ── Per-ticker cooldown (2026-05-20) ────────────────────────────────
#
# DB-persisted cooldown gate added to _Phase1Base after the atm_hold_v1
# 5/20 incident: 3 fires on the same KXBTC ticker at the same $0.49 within
# 4 minutes from 3 different bot PIDs (in-memory `_processed` set wiped on
# each restart). Promo n=11 paper cohort audit confirmed legitimate multi-
# fire cascades always moved ya by ≥7c, so blocking only when both the
# time gap is <PHASE1_TICKER_COOLDOWN_S AND the price gap is
# <PHASE1_TICKER_PRICE_DIFF preserves edge while killing pollution.
# See [[atm_hold_v1_promo_cohort_audit_2026-05-20]].


@pytest.mark.asyncio
async def test_cooldown_blocks_same_price_within_window():
    """Same ticker, same price (.49 → .49), 10s gap: BLOCK.
    Mirrors the 15:55:06 → 15:55:33 atm_hold_v1 restart-pollution case."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2012-B77450",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2012-B77450": {
        "yes_ask_dollars": 0.49, "no_ask_dollars": 0.50,
    }}
    last = {"timestamp": _now_iso(10.0), "price": 0.49}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cooldown_allows_price_cascade_within_window():
    """Same ticker, ya cascade (.49 → .57, 8c gap), 10s gap: ALLOW.
    Mirrors promo paper 1415-B81550 case (.58 → .51, both wins) — the
    strategy's edge is independent observations as ya moves through the band."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2001-B76750",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2001-B76750": {
        "yes_ask_dollars": 0.57, "no_ask_dollars": 0.42,
    }}
    last = {"timestamp": _now_iso(10.0), "price": 0.49}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
    args = om.market_order.await_args.kwargs
    assert args["price"] == 0.57


@pytest.mark.asyncio
async def test_cooldown_expires_after_window():
    """Same ticker, same price, but 60s gap (>30s window): ALLOW."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2012-B77450",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2012-B77450": {
        "yes_ask_dollars": 0.49, "no_ask_dollars": 0.50,
    }}
    last = {"timestamp": _now_iso(60.0), "price": 0.49}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_no_prior_trade_allows():
    """First-ever fire on this ticker: no DB row → ALLOW."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2001-B76750",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2001-B76750": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.49,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=None)
    await s.scan_and_execute()
    assert om.market_order.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_survives_restart_via_db():
    """Restart pollution defense — fresh strategy instance (in-memory
    `_processed` empty) but DB still has the previous fire. New process
    must still honor the cooldown."""
    sig = {"id": 9001, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2012-B77450",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2012-B77450": {
        "yes_ask_dollars": 0.49, "no_ask_dollars": 0.50,
    }}
    # Simulate: previous PID fired at 0.49 27s ago. Fresh instance starts.
    last = {"timestamp": _now_iso(27.0), "price": 0.49}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    assert s._processed == set()  # fresh in-memory state, as on restart
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cooldown_applies_to_deribit_tail_fade():
    """Cooldown is shared by all _Phase1Base subclasses, verify on
    deribit_tail_fade_v1 too (DB-side restart-pollution defense)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXETH-26MAY1311-B2220",
           "predicted_outcome": "no"}
    markets = {"KXETH-26MAY1311-B2220": {
        "yes_ask_dollars": 0.15, "no_ask_dollars": 0.85,
        "yes_bid_size_fp": 50.0,
    }}
    last = {"timestamp": _now_iso(5.0), "price": 0.85}
    s, om = _build_strategy(DeribitTailFadeV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cooldown_applies_to_open_no_touch():
    """OpenNoTouchV1 has its own scan_and_execute (event-cap based);
    cooldown must still hook in to defend against same-event re-fire
    after restart."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY1311-B89000",
           "predicted_outcome": "no", "event_key": "BTC-26MAY1311"}
    markets = {"KXBTC-26MAY1311-B89000": {
        "no_ask_dollars": 0.62,
    }}
    # ya = 1 - 0.62 = 0.38, within [0.30, 0.45]. Prior fire at no_ask=0.62 5s ago.
    last = {"timestamp": _now_iso(5.0), "price": 0.62}
    s, om = _build_strategy(OpenNoTouchV1Strategy, signals=[sig],
                            market_lookup=markets.get, last_trade=last)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_cooldown_db_error_does_not_block_fire():
    """Defensive: if DB query raises, log + allow (don't silently kill
    the strategy on transient sqlite errors)."""
    sig = {"id": 1, "ts": _now_iso(2.0), "market_id": "KXBTC-26MAY2001-B76750",
           "predicted_outcome": "yes"}
    markets = {"KXBTC-26MAY2001-B76750": {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.49,
    }}
    s, om = _build_strategy(AtmHoldV1Strategy, signals=[sig],
                            market_lookup=markets.get)
    om._db.get_last_trade_for_ticker = AsyncMock(side_effect=RuntimeError("db locked"))
    await s.scan_and_execute()
    assert om.market_order.await_count == 1
