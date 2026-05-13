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
    _parse_suffix_threshold, _suffix_kind, _event_key, _build_leg,
    _lognormal_p_above,
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


def test_bvt_consistency_skips_when_aligned():
    obs = BvTConsistencyObserver()
    b_legs = [_b(89800, 0.20, 0.18), _b(89900, 0.10, 0.09)]
    t_legs = [_t(89800, ya=0.28, yb=0.26)]  # Σ B = 0.30, diff = 0.02 < 0.05
    assert obs.evaluate(_ctx(b_legs=b_legs, t_legs=t_legs)) == []


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


def test_open_widening_skips_after_window():
    obs = OpenWideningObserver()
    legs = [_b(89750, 0.50, 0.48)]
    ctx = _ctx(b_legs=legs, open_s=1200.0)
    assert obs.evaluate(ctx) == []


# ── 7. DeribitTailSkew ─────────────────────────────────────────────


def test_deribit_tail_skew_fires_when_ya_overshoots_lognormal():
    obs = DeribitTailSkewObserver()
    # Bucket far from spot. iv = 50%. tau = 5 min. P_LN of a 100$-wide
    # bucket at spot+$2k should be near zero. ya = $0.05 → ratio huge.
    legs = [_b(91750, ya=0.05, yb=0.04, yb_size=50),
            _b(91850, ya=0.05, yb=0.04, yb_size=50)]
    ctx = _ctx(b_legs=legs, spot=89750.0, close_s=300.0, iv=0.50)
    sigs = obs.evaluate(ctx)
    assert all(s.predicted_outcome == "no" for s in sigs)
    assert len(sigs) >= 1


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
