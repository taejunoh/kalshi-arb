"""Unit tests for each crypto observer's trigger logic.

Each observer is a pure function over EventContext. Tests construct a
context with the minimal data needed and assert which signals fire.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from strategies.crypto_observer import (
    Leg, EventContext,
    TailDecayObserver, AtmFadeObserver, ClusterSumObserver,
    BvTConsistencyObserver, SpotDriftMomentumObserver,
    OpenWideningObserver, DeribitTailSkewObserver, BidImbalanceObserver,
    OpenNoTouchObserver, AtmHoldObserver, BidImbalanceFadeObserver,
    LateCloseTailObserver, DeribitAtmSkewObserver,
    MidOtmTailObserver,
    SettlementEdgeObserver,
    LognormalFairDevObserver,
    StrikeLadderLiteralObserver,
    CrossVenueFundingConsensusObserver,
    _parse_suffix_threshold, _suffix_kind, _event_key, _build_leg,
    _lognormal_p_above, _flip,
)


NOW = datetime(2026, 5, 13, 13, 50, 0, tzinfo=timezone.utc)


def _b(ticker_suffix: int, ya: float, yb: float, *,
       ya_size: float = 200, yb_size: float = 200,
       na_size: float = 200, nb_size: float = 200) -> Leg:
    """Build a B-bucket Leg with $100 grid."""
    return Leg(
        ticker=f"KXBTC-26MAY1310-B{ticker_suffix}",
        kind="bucketed", threshold=float(ticker_suffix),
        ya=ya, yb=yb, na=1.0 - yb, nb=1.0 - ya,
        ya_size=ya_size, yb_size=yb_size,
        na_size=na_size, nb_size=nb_size,
        liquidity=500.0, raw={},
    )


def _t(strike: float, ya: float, yb: float) -> Leg:
    return Leg(
        ticker=f"KXBTC-26MAY1310-T{strike}",
        kind="cumulative", threshold=float(strike),
        ya=ya, yb=yb, na=1.0 - yb, nb=1.0 - ya,
        ya_size=100, yb_size=100, na_size=100, nb_size=100,
        liquidity=200.0, raw={},
    )


def _ctx(
    *, b_legs=None, t_legs=None, spot=89750.0, spot_60s_ago=None,
    close_s=120.0, open_s=None, iv=None,
) -> EventContext:
    return EventContext(
        series="KXBTC", asset="BTC", event_key="26MAY1310",
        now=NOW, spot=spot, spot_60s_ago=spot_60s_ago, deribit_iv=iv,
        b_legs=b_legs or [], t_legs=t_legs or [],
        open_time_secs=open_s, close_time_secs=close_s,
    )


# ── helpers ────────────────────────────────────────────────────────


def test_parse_suffix_threshold_handles_b_and_t():
    assert _parse_suffix_threshold("B89750") == 89750.0
    assert _parse_suffix_threshold("T89749.99") == 89749.99
    # Helper strips any non-numeric prefix and float-parses the tail —
    # callers (or _suffix_kind) decide whether to honor unknown prefixes.
    assert _parse_suffix_threshold("ZZZZ") is None
    assert _parse_suffix_threshold("") is None


def test_suffix_kind_distinguishes():
    assert _suffix_kind("B70000") == "bucketed"
    assert _suffix_kind("T70000.5") == "cumulative"
    assert _suffix_kind("X1") is None


def test_event_key_extraction():
    assert _event_key("KXBTC-26MAY1310-B89750") == "26MAY1310"
    assert _event_key("KXBTC-26MAY09-1500-B70250") == "26MAY09-1500"


def test_build_leg_returns_none_on_missing_prices():
    assert _build_leg({"ticker": "KXBTC-26MAY1310-B89750"}) is None
    leg = _build_leg({
        "ticker": "KXBTC-26MAY1310-B89750",
        "yes_ask_dollars": 0.50, "yes_bid_dollars": 0.48,
        "no_ask_dollars": 0.52, "no_bid_dollars": 0.50,
        "yes_ask_size_fp": 100, "yes_bid_size_fp": 200,
        "no_ask_size_fp": 150, "no_bid_size_fp": 250,
        "liquidity_dollars": 500.0,
    })
    assert leg is not None
    assert leg.threshold == 89750.0
    assert leg.ya == 0.50 and leg.kind == "bucketed"


def test_lognormal_p_above_at_spot_is_half():
    # Strike at spot, regardless of vol, P should be ~0.5
    p = _lognormal_p_above(spot=89000, strike=89000, vol=0.6, tau_years=1/365)
    assert p is not None
    assert 0.45 < p < 0.55


# ── 1. TailDecay ───────────────────────────────────────────────────


def test_tail_decay_fires_on_far_otm_near_close():
    obs = TailDecayObserver()
    # Spot $89,750. Bucket at $91,000 = $1,250 away = 1.4% → triggers.
    legs = [_b(91000, ya=0.03, yb=0.02, yb_size=80)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=180.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].market_id.endswith("B91000")


def test_tail_decay_skips_within_distance():
    obs = TailDecayObserver()
    # Bucket 0.1% from spot → too close, skip.
    legs = [_b(89800, ya=0.03, yb=0.02, yb_size=80)]
    assert obs.evaluate(_ctx(b_legs=legs)) == []


def test_tail_decay_skips_inactive_bucket():
    obs = TailDecayObserver()
    # Far enough but yb_size below threshold → phantom quote, skip.
    legs = [_b(91000, ya=0.03, yb=0.0, yb_size=0)]
    assert obs.evaluate(_ctx(b_legs=legs)) == []


def test_tail_decay_skips_far_from_close():
    obs = TailDecayObserver()
    legs = [_b(91000, ya=0.03, yb=0.02, yb_size=80)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=900.0)) == []


def test_tail_decay_skips_above_price_cap():
    obs = TailDecayObserver()
    legs = [_b(91000, ya=0.20, yb=0.18, yb_size=80)]
    assert obs.evaluate(_ctx(b_legs=legs)) == []


# ── 2. AtmFade ─────────────────────────────────────────────────────


def test_atm_fade_fires_on_atm_with_high_ya():
    obs = AtmFadeObserver()
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.50, 0.48),   # ATM
            _b(89850, 0.10, 0.09)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=180.0))
    assert len(sigs) == 1
    assert sigs[0].market_id.endswith("B89750")
    assert sigs[0].predicted_outcome == "no"


def test_atm_fade_skips_when_ya_too_low():
    obs = AtmFadeObserver()
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.30, 0.28),
            _b(89850, 0.10, 0.09)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=180.0)) == []


def test_atm_fade_skips_when_ya_already_at_99_percent():
    """If MM has the ATM bucket at ya=0.99, the market is already pricing
    it as certain-YES — fading isn't alpha, it's just betting against
    correctly-priced markets. Observed pre-fix: n=15 / 0% WR."""
    obs = AtmFadeObserver()
    legs = [_b(89650, 0.01, 0.00),
            _b(89750, 0.99, 0.97),
            _b(89850, 0.01, 0.00)]
    assert obs.evaluate(_ctx(b_legs=legs, spot=89750.0, close_s=180.0)) == []


def test_atm_fade_skips_when_atm_bucket_off_center():
    """If best bucket is > 0.25 × grid from spot, skip."""
    obs = AtmFadeObserver()
    # Spot 89750, but only buckets at 89400 and 89500 exist. Closest is
    # 89500 — 250 from spot = 2.5× a $100 grid. Skip.
    legs = [_b(89400, 0.50, 0.48), _b(89500, 0.50, 0.48)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=180.0)) == []


# ── 3. ClusterSum ──────────────────────────────────────────────────


def test_cluster_sum_fires_on_underpriced_cluster():
    obs = ClusterSumObserver()
    # ATM idx = 2 (89750). Cluster radius 5 keeps all 7. Sum ya = 0.85.
    legs = [_b(89550, 0.05, 0.04),
            _b(89650, 0.10, 0.09),
            _b(89750, 0.40, 0.38),
            _b(89850, 0.10, 0.09),
            _b(89950, 0.05, 0.04),
            _b(90050, 0.05, 0.04),
            _b(90150, 0.10, 0.09)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=180.0))
    # sum_ya = 0.85 ≤ 0.95 → underpriced fires
    assert any(s.features["kind"] == "underpriced" for s in sigs)


def test_cluster_sum_fires_on_overpriced_cluster():
    obs = ClusterSumObserver()
    legs = [_b(89550, 0.20, 0.19),
            _b(89650, 0.30, 0.29),
            _b(89750, 0.40, 0.38),
            _b(89850, 0.30, 0.29),
            _b(89950, 0.20, 0.19)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=180.0))
    # sum_yb = 1.34 ≥ 1.05 → overpriced fires
    assert any(s.features["kind"] == "overpriced" for s in sigs)


def test_cluster_sum_skips_when_balanced():
    obs = ClusterSumObserver()
    legs = [_b(89550, 0.15, 0.14),
            _b(89650, 0.20, 0.19),
            _b(89750, 0.30, 0.29),
            _b(89850, 0.20, 0.19),
            _b(89950, 0.15, 0.14)]
    # sum_ya = 1.0; sum_yb = 0.95 → neither gate trips
    assert obs.evaluate(_ctx(b_legs=legs, close_s=180.0)) == []


def test_cluster_sum_skips_when_cluster_too_small():
    obs = ClusterSumObserver()
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.40, 0.38),
            _b(89850, 0.10, 0.09)]  # only 3 legs < min 5
    assert obs.evaluate(_ctx(b_legs=legs, close_s=180.0)) == []


def test_cluster_sum_skips_when_atm_not_covering_spot():
    """Some Kalshi events list only far-OTM buckets. `atm_bucket_index`
    picks the leg closest to spot, but if that leg is still 1%+ from
    spot, the ±5 cluster is a far-OTM zone where sum-to-1 math is
    meaningless. Pre-fix observed: 381 settled / 0% WR with target
    buckets $1,000+ away from spot. Gate: ATM must be within 0.5% of spot."""
    obs = ClusterSumObserver()
    # Spot $79,300 but event buckets only span $80,400-$80,900 (1.4%+ above).
    legs = [_b(80400, 0.01, 0.00),
            _b(80500, 0.01, 0.00),
            _b(80550, 0.02, 0.01),
            _b(80600, 0.01, 0.00),
            _b(80700, 0.01, 0.00),
            _b(80800, 0.01, 0.00),
            _b(80900, 0.01, 0.00)]
    ctx = _ctx(b_legs=legs, spot=79300.0, close_s=180.0)
    assert obs.evaluate(ctx) == []


# ── 4. BvTConsistency ──────────────────────────────────────────────


def test_bvt_consistency_fires_on_misaligned_t_vs_b_sum():
    obs = BvTConsistencyObserver()
    # T_89800 yes_ask = 0.10, but Σ ya over B≥89800 = 0.40 → B says
    # 4x higher probability. Predict T yes.
    b_legs = [_b(89800, 0.20, 0.18),
              _b(89900, 0.10, 0.09),
              _b(90000, 0.10, 0.09)]
    t_legs = [_t(89800, ya=0.10, yb=0.09)]
    sigs = obs.evaluate(_ctx(b_legs=b_legs, t_legs=t_legs, close_s=300.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"
    assert sigs[0].features["deviation"] > 0
    assert sigs[0].features["n_active_b"] == 3


def test_bvt_consistency_skips_when_aligned():
    obs = BvTConsistencyObserver()
    b_legs = [_b(89800, 0.20, 0.18), _b(89900, 0.10, 0.09)]
    t_legs = [_t(89800, ya=0.28, yb=0.26)]  # Σ B = 0.30, diff = 0.02 < 0.05
    assert obs.evaluate(_ctx(b_legs=b_legs, t_legs=t_legs)) == []


def test_bvt_consistency_excludes_phantom_quoted_t_market():
    """T markets quoted at extreme prices (≤$0.05 or ≥$0.95) are typically
    phantom illiquids — firing on them yields trivially-predictable signals.
    E.g., T1530 with ETH spot $2267 quoted at ya=$0.01: bucket sum is
    obviously much higher, but the 'predict yes' resolution is trivial."""
    obs = BvTConsistencyObserver()
    b_legs = [_b(2280, 0.50, 0.48), _b(2300, 0.30, 0.28)]
    t_legs = [_t(1530, ya=0.01, yb=0.0)]  # phantom ya
    assert obs.evaluate(_ctx(b_legs=b_legs, t_legs=t_legs)) == []


def test_bvt_consistency_excludes_phantom_quoted_legs():
    """Inactive tail buckets quote ya=$0.99/yb=$0. Summing them would
    blow past $1.0 and trigger false positives — same failure mode that
    retired ladder_crypto. Filter requires yb > 0."""
    obs = BvTConsistencyObserver()
    # 2 active legs ya=0.10+0.05 = 0.15. Plus 10 phantom legs at ya=0.99
    # that should be excluded. T_X = 0.18 → diff with active legs = 0.03
    # (< MIN), so should NOT fire. If phantoms were included, diff = 9.9+,
    # which would trigger.
    b_legs = (
        [_b(89800, 0.10, 0.09), _b(89900, 0.05, 0.04)]
        + [_b(90000 + i * 100, 0.99, 0.00) for i in range(10)]
    )
    t_legs = [_t(89800, ya=0.18, yb=0.16)]
    sigs = obs.evaluate(_ctx(b_legs=b_legs, t_legs=t_legs, close_s=300.0))
    assert sigs == []


# ── 5. SpotDriftMomentum ───────────────────────────────────────────


def test_spot_drift_fires_on_upward_drift():
    obs = SpotDriftMomentumObserver()
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.40, 0.38),
            _b(89850, 0.10, 0.09)]
    ctx = _ctx(b_legs=legs, spot=89750.0, spot_60s_ago=89400.0,
               close_s=120.0)
    sigs = obs.evaluate(ctx)
    # 0.39% drift up → bucket above spot (89850) selected
    assert len(sigs) == 1
    assert sigs[0].market_id.endswith("B89850")
    assert sigs[0].predicted_outcome == "yes"


def test_spot_drift_fires_on_downward_drift():
    obs = SpotDriftMomentumObserver()
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.40, 0.38),
            _b(89850, 0.10, 0.09)]
    ctx = _ctx(b_legs=legs, spot=89750.0, spot_60s_ago=90200.0,
               close_s=120.0)
    sigs = obs.evaluate(ctx)
    # -0.5% drift → bucket below spot (89650)
    assert len(sigs) == 1
    assert sigs[0].market_id.endswith("B89650")


def test_spot_drift_skips_on_no_history():
    obs = SpotDriftMomentumObserver()
    legs = [_b(89750, 0.40, 0.38)]
    assert obs.evaluate(_ctx(b_legs=legs, spot_60s_ago=None)) == []


def test_spot_drift_skips_below_threshold():
    obs = SpotDriftMomentumObserver()
    legs = [_b(89850, 0.10, 0.09)]
    # 0.1% drift < 0.3% min
    ctx = _ctx(b_legs=legs, spot=89750.0, spot_60s_ago=89660.0)
    assert obs.evaluate(ctx) == []


# ── 6. OpenWidening ────────────────────────────────────────────────


def test_open_widening_fires_within_age_window():
    obs = OpenWideningObserver()
    # Spot 89750, ±1% = ±$897.5 → keep [88853, 90647].
    legs = [_b(87000, 0.01, 0.005),     # outside
            _b(89700, 0.20, 0.18),       # in
            _b(89750, 0.50, 0.48),       # in
            _b(89800, 0.20, 0.18),       # in
            _b(92000, 0.01, 0.005)]      # outside
    ctx = _ctx(b_legs=legs, open_s=300.0)
    sigs = obs.evaluate(ctx)
    assert {s.market_id.rsplit("-", 1)[-1] for s in sigs} == {
        "B89700", "B89750", "B89800"}


def test_open_widening_fires_on_t_legs():
    """T-cumulative legs (daily contracts) use same gates as B."""
    obs = OpenWideningObserver()
    # Spot 89750. T-strike at 90250 (~0.56% above, within 1% radius). ya=0.30.
    t_legs = [_t(90250, ya=0.30, yb=0.25)]
    ctx = _ctx(t_legs=t_legs, open_s=300.0)
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].features["kind"] == "cumulative"


def test_open_widening_skips_after_window():
    obs = OpenWideningObserver()
    legs = [_b(89750, 0.50, 0.48)]
    ctx = _ctx(b_legs=legs, open_s=1200.0)
    assert obs.evaluate(ctx) == []


# ── 7. DeribitTailSkew ─────────────────────────────────────────────


def test_deribit_tail_skew_fires_when_ya_overshoots_lognormal():
    obs = DeribitTailSkewObserver()
    # Bucket ~1% OTM, 1h to close, iv=80%. Yields p_ln ~2%, so ya=0.10
    # gives ratio ~4.5 — overshoots the 3× gate but stays under the
    # ratio cap (50). Phase 0 sweet spot.
    legs = [_b(90750, ya=0.10, yb=0.09, yb_size=50),
            _b(90850, ya=0.10, yb=0.09, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=3600.0, iv=0.80)
    sigs = obs.evaluate(ctx)
    assert all(s.predicted_outcome == "no" for s in sigs)
    assert len(sigs) >= 1


def test_deribit_tail_skew_skips_when_ratio_exceeds_cap():
    """Deep-OTM buckets push p_ln toward zero, blowing ratio past 50.
    Phase 0 cohort showed WR drops to 83-93% in that regime — lognormal
    breakdown, not real mispricing. Cap rejects."""
    obs = DeribitTailSkewObserver()
    # +$2k OTM with 5min/50% IV → p_ln ≈ 0, ratio = inf
    legs = [_b(91750, ya=0.05, yb=0.04, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.50)
    assert obs.evaluate(ctx) == []


def test_deribit_tail_skew_skips_when_atm_and_priced_fairly():
    obs = DeribitTailSkewObserver()
    legs = [_b(89750, ya=0.50, yb=0.48, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.50)
    # ATM bucket P_LN > 0 and ya not 3× P_LN — skip
    assert obs.evaluate(ctx) == []


def test_deribit_tail_skew_skips_without_iv():
    obs = DeribitTailSkewObserver()
    legs = [_b(91750, ya=0.05, yb=0.04, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, iv=None)) == []


# ── 8. BidImbalance ────────────────────────────────────────────────


def test_bid_imbalance_fires_with_yes_lean():
    obs = BidImbalanceObserver()
    legs = [_b(89750, 0.50, 0.48,
               yb_size=400, nb_size=100, ya_size=50, na_size=50)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=300.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"
    assert sigs[0].features["ratio"] >= 3.0


def test_bid_imbalance_fires_with_no_lean():
    obs = BidImbalanceObserver()
    legs = [_b(89750, 0.50, 0.48,
               yb_size=100, nb_size=400, ya_size=50, na_size=50)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=300.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"


def test_bid_imbalance_skips_when_balanced():
    obs = BidImbalanceObserver()
    legs = [_b(89750, 0.50, 0.48,
               yb_size=200, nb_size=200, ya_size=200, na_size=200)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=300.0)) == []


def test_bid_imbalance_fires_on_t_legs():
    """T-cumulative legs work same as B — depth ratio is leg-shape agnostic."""
    obs = BidImbalanceObserver()
    # Construct T-leg directly with imbalanced depth (helper defaults are balanced).
    t_leg = Leg(
        ticker="KXBTCD-26MAY1310-T89750.0",
        kind="cumulative", threshold=89750.0,
        ya=0.50, yb=0.48, na=0.52, nb=0.50,
        ya_size=50, yb_size=400, na_size=50, nb_size=100,
        liquidity=200.0, raw={},
    )
    sigs = obs.evaluate(_ctx(t_legs=[t_leg], close_s=300.0))
    assert len(sigs) == 1
    assert sigs[0].features["kind"] == "cumulative"
    assert sigs[0].predicted_outcome == "yes"  # yb (400) > nb (100)


# ── Flipped observers — OpenNoTouch / AtmHold ──────────────────────


def test_flip_helper_swaps_yes_and_no():
    assert _flip("yes") == "no"
    assert _flip("no") == "yes"


def test_open_no_touch_emits_flipped_predictions():
    """For every signal OpenWideningObserver emits (pred=yes), the
    OpenNoTouchObserver must emit the same market_id with pred=no."""
    legs = [_b(87000, 0.01, 0.005),
            _b(89700, 0.20, 0.18),
            _b(89750, 0.50, 0.48),
            _b(89800, 0.20, 0.18),
            _b(92000, 0.01, 0.005)]
    ctx = _ctx(b_legs=legs, open_s=300.0)

    orig_sigs = OpenWideningObserver().evaluate(ctx)
    flip_sigs = OpenNoTouchObserver().evaluate(ctx)

    assert len(orig_sigs) == len(flip_sigs)
    by_market_orig = {s.market_id: s.predicted_outcome for s in orig_sigs}
    by_market_flip = {s.market_id: s.predicted_outcome for s in flip_sigs}
    for mkt, pred in by_market_orig.items():
        assert by_market_flip[mkt] == _flip(pred)
    # And the observer name itself
    assert all(s.observer == "open_no_touch" for s in flip_sigs)


def test_atm_hold_emits_flipped_predictions():
    legs = [_b(89650, 0.10, 0.09),
            _b(89750, 0.50, 0.48),   # ATM, ya in [0.40, 0.65]
            _b(89850, 0.10, 0.09)]
    ctx = _ctx(b_legs=legs, close_s=180.0)

    orig_sigs = AtmFadeObserver().evaluate(ctx)
    flip_sigs = AtmHoldObserver().evaluate(ctx)

    assert len(orig_sigs) == len(flip_sigs) == 1
    assert flip_sigs[0].market_id == orig_sigs[0].market_id
    assert flip_sigs[0].predicted_outcome == "yes"
    assert orig_sigs[0].predicted_outcome == "no"
    assert flip_sigs[0].observer == "atm_hold"


def test_bid_imbalance_fade_flips_predictions_below_max_ya():
    """For any bid_imbalance signal with ya<0.60, the fade observer must
    emit the same market_id with the predicted side flipped."""
    legs = [
        _b(89650, ya=0.20, yb=0.18,
           yb_size=400, nb_size=100, ya_size=50, na_size=50),  # ya<0.60, pred=yes
        _b(89750, ya=0.45, yb=0.43,
           yb_size=100, nb_size=400, ya_size=50, na_size=50),  # ya<0.60, pred=no
    ]
    ctx = _ctx(b_legs=legs, close_s=300.0)

    orig_sigs = BidImbalanceObserver().evaluate(ctx)
    flip_sigs = BidImbalanceFadeObserver().evaluate(ctx)

    assert len(orig_sigs) == 2
    assert len(flip_sigs) == 2
    by_market_orig = {s.market_id: s.predicted_outcome for s in orig_sigs}
    by_market_flip = {s.market_id: s.predicted_outcome for s in flip_sigs}
    for mkt, pred in by_market_orig.items():
        assert by_market_flip[mkt] == _flip(pred)
    assert all(s.observer == "bid_imbalance_fade" for s in flip_sigs)


def test_bid_imbalance_fade_skips_when_ya_at_or_above_max():
    """ya≥0.60 leg should be dropped by fade observer (original still fires)."""
    legs = [
        _b(89750, ya=0.70, yb=0.68,
           yb_size=400, nb_size=100, ya_size=50, na_size=50),  # ya>=0.60
    ]
    ctx = _ctx(b_legs=legs, close_s=300.0)
    assert len(BidImbalanceObserver().evaluate(ctx)) == 1
    assert BidImbalanceFadeObserver().evaluate(ctx) == []


# ── 9. LateCloseTail (2026-05-18) ──────────────────────────────────


def test_late_close_tail_fires_in_mid_otm_band():
    """ya in [0.05, 0.30], dist ≥ 0.5% from spot, ≤10min to close → pred=no."""
    obs = LateCloseTailObserver()
    # Spot $89,750. Bucket at $90,250 = 0.56% above. ya=0.15, yb_size=50.
    legs = [_b(90250, ya=0.15, yb=0.13, yb_size=50)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=400.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].market_id.endswith("B90250")
    assert "na" in sigs[0].features


def test_late_close_tail_skips_below_min_ya():
    """ya < 0.05 is the tail_decay zone — late_close_tail must not double-fire."""
    obs = LateCloseTailObserver()
    legs = [_b(90250, ya=0.03, yb=0.02, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=400.0)) == []


def test_late_close_tail_skips_above_max_ya():
    """ya > 0.30 = near-ATM territory where the OTM-no-touch thesis fails."""
    obs = LateCloseTailObserver()
    legs = [_b(90250, ya=0.40, yb=0.38, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=400.0)) == []


def test_late_close_tail_skips_within_dist_floor():
    """≥0.5% from spot — closer buckets are ATM-ish, not tails."""
    obs = LateCloseTailObserver()
    # Bucket $50 above spot $89,750 = 0.056% — way under 0.5%.
    legs = [_b(89800, ya=0.20, yb=0.18, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=400.0)) == []


def test_late_close_tail_skips_inactive_bucket():
    """yb_size below MIN cuts phantom-quoted strikes."""
    obs = LateCloseTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.0, yb_size=0)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=400.0)) == []


def test_late_close_tail_skips_outside_close_window():
    """>10min to close is the open_no_touch / mid-event regime."""
    obs = LateCloseTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.13, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=1200.0)) == []


def test_late_close_tail_fires_on_t_legs_for_daily_series():
    """T-cumulative legs (KXBTCD/KXETHD daily contracts) use the same gates.

    Features include leg kind so post-hoc analysis can slice b vs t.
    """
    obs = LateCloseTailObserver()
    # Spot $89,750. T-strike at $91,000 = 1.39% above. ya=0.10, yb_size=50.
    t_legs = [_t(91000, ya=0.10, yb=0.06)]
    # Override yb_size to pass active-bid gate (helper defaults yb_size=100).
    sigs = obs.evaluate(_ctx(t_legs=t_legs, close_s=400.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].features["kind"] == "cumulative"
    assert "T91000" in sigs[0].market_id


# ── 9b. MidOtmTail (2026-05-20) ────────────────────────────────────


def test_mid_otm_tail_fires_in_wider_window():
    """Same gates as late_close_tail but mts_s ∈ (600, 7200]."""
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.13, yb_size=50)]
    sigs = obs.evaluate(_ctx(b_legs=legs, close_s=1800.0))
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].observer == "mid_otm_tail"
    assert "na" in sigs[0].features
    assert sigs[0].features["mts_s"] == 1800.0


def test_mid_otm_tail_skips_inside_late_close_window():
    """≤600s is late_close_tail's domain — avoid double-fire."""
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.13, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=400.0)) == []


def test_mid_otm_tail_skips_beyond_2h_window():
    """>2hr to close — events further out have too much path variance."""
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.13, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=8000.0)) == []


def test_mid_otm_tail_skips_below_min_ya():
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.03, yb=0.02, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=1800.0)) == []


def test_mid_otm_tail_skips_above_max_ya():
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.40, yb=0.38, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=1800.0)) == []


def test_mid_otm_tail_skips_within_dist_floor():
    obs = MidOtmTailObserver()
    legs = [_b(89800, ya=0.20, yb=0.18, yb_size=50)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=1800.0)) == []


def test_mid_otm_tail_skips_inactive_bucket():
    obs = MidOtmTailObserver()
    legs = [_b(90250, ya=0.15, yb=0.0, yb_size=0)]
    assert obs.evaluate(_ctx(b_legs=legs, close_s=1800.0)) == []


def test_mid_otm_tail_fires_on_t_legs_for_daily_series():
    """T-cumulative legs use the same gates as B-bucketed in the (600, 7200]
    window. Features include leg kind for post-hoc slicing."""
    obs = MidOtmTailObserver()
    t_legs = [_t(91000, ya=0.10, yb=0.06)]
    sigs = obs.evaluate(_ctx(t_legs=t_legs, close_s=1800.0))
    assert len(sigs) == 1
    assert sigs[0].features["kind"] == "cumulative"


# ── 10. DeribitAtmSkew (2026-05-18) ────────────────────────────────


def test_deribit_atm_skew_fires_when_atm_ya_overshoots_lognormal():
    """ATM bucket with ya ≥ 1.5 × P_LN, ya in [0.30, 0.70], iv present → no."""
    obs = DeribitAtmSkewObserver()
    # 5 min to close, IV=80% → ATM bucket P_LN ~25-30%. ya=0.60 gives
    # ratio ~2-2.5, clears 1.5× gate.
    legs = [_b(89650, ya=0.05, yb=0.04, yb_size=50),
            _b(89750, ya=0.60, yb=0.58, yb_size=50),
            _b(89850, ya=0.05, yb=0.04, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.80)
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].market_id.endswith("B89750")
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].features["ratio"] >= 1.5
    assert "na" in sigs[0].features


def test_deribit_atm_skew_skips_when_ya_fair():
    """ya below 1.5 × P_LN means market is pricing the ATM ~fairly.
    1 min to close + iv=0.80 makes the ATM-bucket p_ln ~0.39, so ya=0.30
    yields ratio ~0.77 — under the 1.5× gate."""
    obs = DeribitAtmSkewObserver()
    legs = [_b(89650, ya=0.05, yb=0.04, yb_size=50),
            _b(89750, ya=0.30, yb=0.28, yb_size=50),
            _b(89850, ya=0.05, yb=0.04, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=60.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_deribit_atm_skew_skips_when_atm_off_center():
    """ATM bucket must be within 0.25 × grid of spot."""
    obs = DeribitAtmSkewObserver()
    # Spot 89750, closest bucket at 89400 = 350 from spot vs $100 grid → 3.5×.
    legs = [_b(89300, ya=0.60, yb=0.58, yb_size=50),
            _b(89400, ya=0.60, yb=0.58, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_deribit_atm_skew_skips_outside_ya_band():
    obs = DeribitAtmSkewObserver()
    legs = [_b(89650, ya=0.05, yb=0.04, yb_size=50),
            _b(89750, ya=0.85, yb=0.83, yb_size=50),  # too rich
            _b(89850, ya=0.05, yb=0.04, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_deribit_atm_skew_skips_without_iv():
    """No Deribit IV → can't compute lognormal — skip rather than fire blind."""
    obs = DeribitAtmSkewObserver()
    legs = [_b(89750, ya=0.60, yb=0.58, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=None)
    assert obs.evaluate(ctx) == []


def test_deribit_atm_skew_skips_inactive_bucket():
    obs = DeribitAtmSkewObserver()
    legs = [_b(89750, ya=0.60, yb=0.58, yb_size=0)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_deribit_atm_skew_skips_outside_close_window():
    obs = DeribitAtmSkewObserver()
    legs = [_b(89650, ya=0.05, yb=0.04, yb_size=50),
            _b(89750, ya=0.60, yb=0.58, yb_size=50),
            _b(89850, ya=0.05, yb=0.04, yb_size=50)]
    # >10min to close
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=1200.0, iv=0.80)
    assert obs.evaluate(ctx) == []


# ── settlement_edge observer ──────────────────────────────────────


def test_settlement_edge_fires_on_asymmetric_depth_in_atm():
    """ATM bucket within mts_s ≤ 30 with ask much thinner than bid →
    predict_outcome='yes' (bid heavier → market expects touch)."""
    # Build a ladder with $250 spacing, spot 70000, ATM bucket = B70000.
    # ATM bucket has ask_size=20, bid_size=100 → skew = (20-100)/120 = -0.667
    # Predicted='yes' since skew < 0.
    b_legs = [
        _b(69500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
        _b(69750, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70000, ya=0.50, yb=0.48, ya_size=20, yb_size=100),   # ATM (closest to spot 70000)
        _b(70250, ya=0.40, yb=0.38, ya_size=10, yb_size=80),
        _b(70500, ya=0.30, yb=0.28, ya_size=15, yb_size=60),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=15.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    # ATM±2 covers all 5 legs (radius 2 → indices 0..4 in this 5-leg ladder).
    # Every leg has skew magnitude ≥ 0.20 (all are 60-80% bid-heavy).
    # Expect a signal on the ATM leg.
    atm_sigs = [s for s in sigs if s.market_id.endswith("B70000")]
    assert len(atm_sigs) == 1, f"expected 1 ATM signal, got {len(atm_sigs)}: {sigs}"
    atm = atm_sigs[0]
    assert atm.predicted_outcome == "yes"
    assert atm.features["skew"] == pytest.approx(-0.667, abs=0.01)
    assert atm.features["ask_size"] == 20
    assert atm.features["bid_size"] == 100
    assert atm.features["mts_s"] == pytest.approx(15.0, abs=0.01)
    assert atm.features["bucket_dist_from_atm"] == 0


def test_settlement_edge_does_not_fire_outside_30s_window():
    """mts_s = 60 (outside ≤30 window) → no signal even with strong skew."""
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),  # skew = -0.818
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=60.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert sigs == [], f"expected no signals at mts_s=60, got: {sigs}"


def test_settlement_edge_does_not_fire_at_or_below_zero_mts():
    """mts_s ≤ 0 (post-close or missing) → no signal."""
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),
    ]
    # Zero
    ctx_zero = _ctx(b_legs=b_legs, spot=70000.0, close_s=0.0)
    assert SettlementEdgeObserver().evaluate(ctx_zero) == []
    # Negative (already past close)
    ctx_neg = _ctx(b_legs=b_legs, spot=70000.0, close_s=-5.0)
    assert SettlementEdgeObserver().evaluate(ctx_neg) == []
    # None
    ctx_none = _ctx(b_legs=b_legs, spot=70000.0, close_s=None)
    assert SettlementEdgeObserver().evaluate(ctx_none) == []


def test_settlement_edge_filters_near_zero_skew():
    """|skew| = 0.05 (below ABS_SKEW_MIN=0.20) → no signal even within window."""
    # ask_size=50, bid_size=55 → skew = (50-55)/105 = -0.0476
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=50, yb_size=55),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=20.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert sigs == [], f"expected no signal at |skew|≈0.05, got: {sigs}"


def test_settlement_edge_fires_exactly_at_skew_threshold():
    """|skew| just over 0.20 fires; just under 0.20 does not."""
    # Code fires when abs(skew) >= ABS_SKEW_MIN (0.20). Avoid the
    # exact-boundary case to stay robust to float-equality decisions;
    # use 0.21 to fire: ask=121, bid=79 → skew = 42/200 = 0.21.
    b_legs_fire = [
        _b(70000, ya=0.50, yb=0.48, ya_size=121, yb_size=79),
    ]
    ctx_fire = _ctx(b_legs=b_legs_fire, spot=70000.0, close_s=20.0)
    sigs_fire = SettlementEdgeObserver().evaluate(ctx_fire)
    assert len(sigs_fire) == 1
    assert sigs_fire[0].predicted_outcome == "no"  # skew > 0 → no

    # Just under: ask=119, bid=81 → skew = 38/200 = 0.19 → no fire
    b_legs_skip = [
        _b(70000, ya=0.50, yb=0.48, ya_size=119, yb_size=81),
    ]
    ctx_skip = _ctx(b_legs=b_legs_skip, spot=70000.0, close_s=20.0)
    assert SettlementEdgeObserver().evaluate(ctx_skip) == []


def test_settlement_edge_skips_thin_book():
    """ask_size + bid_size ≤ MIN_SIDE_SIZE → no signal (no MM presence)."""
    # All sizes zero
    b_legs_zero = [
        _b(70000, ya=0.50, yb=0.48, ya_size=0, yb_size=0),
    ]
    ctx_zero = _ctx(b_legs=b_legs_zero, spot=70000.0, close_s=15.0)
    assert SettlementEdgeObserver().evaluate(ctx_zero) == []

    # Total exactly at MIN_SIDE_SIZE (1) — still skipped (<=)
    b_legs_min = [
        _b(70000, ya=0.50, yb=0.48, ya_size=1, yb_size=0),
    ]
    ctx_min = _ctx(b_legs=b_legs_min, spot=70000.0, close_s=15.0)
    assert SettlementEdgeObserver().evaluate(ctx_min) == []


def test_settlement_edge_fires_on_negative_skew_predicts_yes():
    """Pure direction-mapping check: skew < 0 → predicted='yes'."""
    # ask=10, bid=100 → skew = -90/110 = -0.818
    b_legs = [
        _b(70000, ya=0.50, yb=0.48, ya_size=10, yb_size=100),
    ]
    ctx = _ctx(b_legs=b_legs, spot=70000.0, close_s=10.0)
    sigs = SettlementEdgeObserver().evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"
    assert sigs[0].features["skew"] < 0


def test_settlement_edge_registered_in_observer_classes():
    """Harness picks up settlement_edge via OBSERVER_CLASSES tuple."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert SettlementEdgeObserver in OBSERVER_CLASSES


# ── LognormalFairDev ───────────────────────────────────────────────


def test_lognormal_fair_dev_fires_when_kalshi_underprices_itm():
    """ITM T-cumulative leg where Kalshi impl << P_LN → predict 'yes'.

    T-leg uses P(S_T ≥ strike) directly (no grid needed). Spot=89750,
    strike=80000 (~11% ITM), iv=0.80, mts=3600s → P(S_T≥80000) ≈ 0.9+.
    Set impl=0.20 (artificially cheap) → dev > +0.10, fires 'yes'.
    """
    obs = LognormalFairDevObserver()
    legs = [_t(80000.0, ya=0.20, yb=0.20)]
    ctx = _ctx(t_legs=legs, spot=89750.0, close_s=3600.0, iv=0.80)
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "yes"
    assert sigs[0].features["deviation"] > 0
    assert sigs[0].features["kind"] == "cumulative"
    assert "fair" in sigs[0].features and "impl" in sigs[0].features


def test_lognormal_fair_dev_fires_when_kalshi_overprices_tail():
    """OTM bucket where Kalshi impl >> lognormal fair → predict 'no'.

    Spot=89750, OTM bucket 50% above (134000) with IV=50%, mts=120s.
    P_LN ≈ 0. Set impl=0.50 (absurdly overpriced) → dev ≈ -0.50, fires 'no'.
    """
    obs = LognormalFairDevObserver()
    legs = [_b(133900, ya=0.50, yb=0.50),
            _b(134000, ya=0.50, yb=0.50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=120.0, iv=0.50)
    sigs = obs.evaluate(ctx)
    no_sigs = [s for s in sigs if s.predicted_outcome == "no"]
    assert len(no_sigs) >= 1
    assert no_sigs[0].features["deviation"] < 0


def test_lognormal_fair_dev_skips_when_deviation_below_threshold():
    """|fair - impl| < 0.10 → no signal even though leg is in window.

    ATM bucket with impl ≈ fair on a single $100 bucket at near-close
    is fair ~0.4-0.5; using a deep-OTM strike where both fair and impl
    are tiny is the cleanest way to assert sub-threshold no-fire.
    """
    obs = LognormalFairDevObserver()
    legs = [_b(150000, ya=0.01, yb=0.01),
            _b(150100, ya=0.01, yb=0.01)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.80)
    sigs = obs.evaluate(ctx)
    assert sigs == []


def test_lognormal_fair_dev_skips_without_iv():
    """No Deribit IV (e.g. XRP) → can't compute fair → skip cleanly."""
    obs = LognormalFairDevObserver()
    legs = [_b(89650, ya=0.02, yb=0.02),
            _b(89750, ya=0.02, yb=0.02)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=None)
    assert obs.evaluate(ctx) == []


def test_lognormal_fair_dev_skips_inside_min_window():
    """mts_s < 60s → skip. Sub-minute physics breaks lognormal vol."""
    obs = LognormalFairDevObserver()
    legs = [_b(89650, ya=0.02, yb=0.02),
            _b(89750, ya=0.02, yb=0.02)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=30.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_lognormal_fair_dev_skips_beyond_max_window():
    """mts_s > 7200s → skip. Long horizons get drift-dominated."""
    obs = LognormalFairDevObserver()
    legs = [_b(89650, ya=0.02, yb=0.02),
            _b(89750, ya=0.02, yb=0.02)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=10000.0, iv=0.80)
    assert obs.evaluate(ctx) == []


def test_lognormal_fair_dev_handles_cumulative_t_legs():
    """T-leg uses P(S_T ≥ strike), not bucket spread. Deep OTM should fire 'no'
    if Kalshi prices it richly."""
    obs = LognormalFairDevObserver()
    # T-cumulative strike 50% above spot — P_LN ≈ 0 with iv=0.5, mts=120s.
    legs = [_t(134000.0, ya=0.50, yb=0.48)]
    ctx = _ctx(t_legs=legs, spot=89750.0, close_s=120.0, iv=0.50)
    sigs = obs.evaluate(ctx)
    assert len(sigs) == 1
    assert sigs[0].predicted_outcome == "no"
    assert sigs[0].features["kind"] == "cumulative"


def test_lognormal_fair_dev_registered_in_observer_classes():
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert LognormalFairDevObserver in OBSERVER_CLASSES


# ── StrikeLadderLiteral ────────────────────────────────────────────


def _build_ladder(*, n_legs: int, ya_each: float, yb_each: float | None = None,
                  spot: float = 70_000.0, base_strike: float = 65_000.0,
                  step: float = 500.0) -> list:
    """Helper: n_legs evenly-spaced B-buckets starting at base_strike."""
    if yb_each is None:
        yb_each = ya_each * 0.9
    legs = []
    for i in range(n_legs):
        strike = base_strike + i * step
        legs.append(_b(int(strike), ya=ya_each, yb=yb_each))
    return legs


def test_strike_ladder_literal_full_underpriced_fires():
    """sum(ya) across 30 legs ≤ 0.95 → emits full_underpriced 'yes' signal."""
    # 30 legs × 0.025 ya each = 0.75 total ← below 0.95
    legs = _build_ladder(n_legs=30, ya_each=0.025, yb_each=0.02)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    full = [s for s in sigs if s.features["kind"] == "full_underpriced"]
    assert len(full) == 1
    assert full[0].predicted_outcome == "yes"
    assert full[0].features["full_sum_ya"] == pytest.approx(0.75, abs=0.01)
    assert full[0].features["n_legs_total"] == 30
    assert full[0].features["cluster_n"] is None  # full kind has no cluster fields


def test_strike_ladder_literal_full_overpriced_fires():
    """sum(yb) across 30 legs ≥ 1.05 → emits full_overpriced 'no' signal."""
    # 30 legs × 0.05 yb each = 1.50 ← above 1.05
    legs = _build_ladder(n_legs=30, ya_each=0.06, yb_each=0.05)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    full = [s for s in sigs if s.features["kind"] == "full_overpriced"]
    assert len(full) == 1
    assert full[0].predicted_outcome == "no"
    assert full[0].features["full_sum_yb"] == pytest.approx(1.50, abs=0.01)


def test_strike_ladder_literal_otm_sub_cluster_fires_above_spot():
    """OTM cluster centered ~+15% above spot, sub-cluster sum_ya low → 'yes'."""
    # Spot 70k. Build 30 legs $500-step covering 65k-79500. Target center
    # = 70_000 * 1.15 = 80_500. Nearest leg = 79_500 (idx=29) — but that's
    # the last leg, cluster radius±5 → only 5 legs available [79_500] only.
    # Extend range: 30 legs from 70_000 step 500 covers 70_000-84_500.
    # Best center for +15% (80_500) = idx 21 (=80_500). Cluster [16..26] = 11 legs.
    # ya 0.005 each × 11 = 0.055 → well below 0.95.
    legs = _build_ladder(n_legs=30, ya_each=0.005, yb_each=0.004,
                         base_strike=70_000.0, step=500.0)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    # full_underpriced will also fire (0.005×30=0.15 ≤ 0.95). Filter OTM ones.
    otm = [s for s in sigs if s.features["kind"] == "otm_underpriced"]
    assert len(otm) >= 1
    # Check the +15% direction fired
    above_spot = [s for s in otm if s.features["cluster_center_dist_pct"] > 0]
    assert len(above_spot) == 1
    sig = above_spot[0]
    assert sig.predicted_outcome == "yes"
    assert sig.features["cluster_center_dist_pct"] == pytest.approx(0.15, abs=0.02)
    assert sig.features["cluster_n"] >= 5
    assert sig.features["cluster_sum_ya"] is not None


def test_strike_ladder_literal_excludes_settlement_edge_window():
    """mts_s < 60s → skip (settlement_edge owns that window)."""
    legs = _build_ladder(n_legs=30, ya_each=0.005, yb_each=0.004)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=30.0)
    assert StrikeLadderLiteralObserver().evaluate(ctx) == []


def test_strike_ladder_literal_skips_below_min_legs():
    """len(b_legs) < 10 → skip."""
    legs = _build_ladder(n_legs=8, ya_each=0.005, yb_each=0.004)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    assert StrikeLadderLiteralObserver().evaluate(ctx) == []


def test_strike_ladder_literal_skips_when_otm_center_too_close_to_spot():
    """If event ladder doesn't cover ±10-30% OTM, the OTM scan skips
    (full-ladder may still fire). 10 tight legs near ATM → no OTM fire."""
    # 10 legs from 69_750 to 70_700 = max 1% from spot 70k
    legs = _build_ladder(n_legs=10, ya_each=0.005, yb_each=0.004,
                         base_strike=69_750.0, step=100.0)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    # full_underpriced should still fire (0.005 * 10 = 0.05 ≤ 0.95)
    full = [s for s in sigs if s.features["kind"] == "full_underpriced"]
    assert len(full) == 1
    # but no OTM fires — nearest legs to ±15% center are <1% from spot
    otm = [s for s in sigs if s.features["kind"].startswith("otm_")]
    assert otm == []


def test_strike_ladder_literal_skips_when_no_violations():
    """Healthy ladder where neither full nor OTM sub-cluster sums violate.

    Need:
      full_sum_ya > 0.95  → ya > 0.032
      full_sum_yb < 1.05  → yb < 0.035
      OTM-cluster sum_ya > 0.95 (per ~6-leg edge cluster) → ya > 0.16
      OTM-cluster sum_yb < 1.05 → yb < 0.17
    ya=0.18, yb=0.03 satisfies all four.
    """
    legs = _build_ladder(n_legs=30, ya_each=0.18, yb_each=0.03)
    ctx = _ctx(b_legs=legs, spot=70_000.0, close_s=300.0)
    sigs = StrikeLadderLiteralObserver().evaluate(ctx)
    assert sigs == []


def test_strike_ladder_literal_registered_in_observer_classes():
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert StrikeLadderLiteralObserver in OBSERVER_CLASSES


def test_event_context_has_funding_8h_by_venue_default_empty():
    """EventContext.funding_8h_by_venue defaults to empty dict."""
    ctx = _ctx(b_legs=[], close_s=600.0)
    assert hasattr(ctx, "funding_8h_by_venue")
    assert ctx.funding_8h_by_venue == {}


# ── cross_venue_funding_consensus observer ────────────────────────


def _ctx_for_funding(funding_by_venue, *, spot=70_000.0, close_s=300.0,
                     b_legs=None):
    """Build an EventContext with the cross-venue funding field set.

    The standard _ctx() helper doesn't accept funding_8h_by_venue
    because the field is new; this wrapper sets it after construction.
    """
    if b_legs is None:
        b_legs = [_b(70_000, ya=0.10, yb=0.08, yb_size=200),
                  _b(71_000, ya=0.10, yb=0.08, yb_size=200)]
    ctx = _ctx(b_legs=b_legs, spot=spot, close_s=close_s)
    ctx.funding_8h_by_venue = funding_by_venue
    return ctx


def test_cross_venue_funding_consensus_fires_on_agreement():
    """All 4 venues funding > +0.0002 → consensus_fade signal,
    predicted='no' on strikes above spot (fade upside)."""
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0005, "okx": 0.0004,
                          "gate": 0.0007, "mexc": 0.0006},
        spot=70_000.0, close_s=300.0,
        b_legs=[_b(70_000, ya=0.10, yb=0.08, yb_size=200),
                _b(71_000, ya=0.10, yb=0.08, yb_size=200)],
    )
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)

    consensus = [s for s in sigs if s.features["kind"] == "consensus_fade"]
    assert len(consensus) >= 1, f"expected consensus_fade signal, got {sigs}"
    sig = consensus[0]
    assert sig.predicted_outcome == "no"
    assert sig.features["consensus_sign"] == 1   # all positive
    assert set(sig.features["venues_reporting"]) == {"deribit", "okx", "gate", "mexc"}
    assert sig.features["funding_deribit"] == pytest.approx(0.0005)
    assert sig.features["funding_okx"]     == pytest.approx(0.0004)
    assert sig.features["funding_gate"]    == pytest.approx(0.0007)
    assert sig.features["funding_mexc"]    == pytest.approx(0.0006)
    assert sig.features["min_abs"] == pytest.approx(0.0004, abs=1e-6)
    # Strike must be above spot for positive-funding fade (fade upside)
    assert sig.features["side"] == "fade_upside"
    assert sig.features["strike"] > sig.features["spot"]


def test_cross_venue_funding_divergence_fires():
    """One venue +0.0008, another -0.0003 → spread 0.0011 ≥ 0.0005,
    divergence event-level signal."""
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0008, "okx": -0.0003,
                          "gate": 0.0001, "mexc": 0.0002},
        spot=70_000.0, close_s=300.0,
        b_legs=[_b(69_500, ya=0.45, yb=0.43, yb_size=200),
                _b(70_000, ya=0.50, yb=0.48, yb_size=200),
                _b(70_500, ya=0.45, yb=0.43, yb_size=200)],
    )
    sigs = CrossVenueFundingConsensusObserver().evaluate(ctx)

    div_sigs = [s for s in sigs if s.features["kind"] == "divergence"]
    assert len(div_sigs) == 1, (
        f"expected exactly 1 divergence signal, got {len(div_sigs)}: {sigs}"
    )
    sig = div_sigs[0]
    # spread = 0.0008 - (-0.0003) = 0.0011
    assert sig.features["spread"] == pytest.approx(0.0011, abs=1e-6)
    # consensus_sign = 0 (not all agree — deribit/gate/mexc positive, okx negative)
    assert sig.features["consensus_sign"] == 0
    # Divergence anchors to ATM bucket (70000)
    assert sig.market_id.endswith("B70000")
    # No consensus_fade in the same evaluation (signs disagree)
    assert not any(s.features["kind"] == "consensus_fade" for s in sigs)


def test_cross_venue_funding_skips_below_min_venues():
    """Only 1 venue reporting (< MIN_VENUES=2) → no signal."""
    # Only deribit reports; others are None
    ctx = _ctx_for_funding(
        funding_by_venue={"deribit": 0.0005, "okx": None,
                          "gate": None, "mexc": None},
        spot=70_000.0, close_s=300.0,
    )
    assert CrossVenueFundingConsensusObserver().evaluate(ctx) == []

    # Empty dict
    ctx_empty = _ctx_for_funding(funding_by_venue={}, spot=70_000.0, close_s=300.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_empty) == []


def test_cross_venue_funding_skips_outside_mts_window():
    """mts_s > MAX (7200) or ≤ 0 → no signal."""
    funding = {"deribit": 0.0005, "okx": 0.0004,
               "gate": 0.0007, "mexc": 0.0006}
    # Way past the 2-hour window
    ctx_far = _ctx_for_funding(funding_by_venue=funding, close_s=10_000.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_far) == []
    # Already closed
    ctx_zero = _ctx_for_funding(funding_by_venue=funding, close_s=0.0)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_zero) == []
    # None
    ctx_none = _ctx_for_funding(funding_by_venue=funding, close_s=None)
    assert CrossVenueFundingConsensusObserver().evaluate(ctx_none) == []


def test_cross_venue_funding_consensus_registered_in_observer_classes():
    """Harness picks up cross_venue_funding_consensus via OBSERVER_CLASSES."""
    from strategies.crypto_observer import OBSERVER_CLASSES
    assert CrossVenueFundingConsensusObserver in OBSERVER_CLASSES
