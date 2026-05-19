"""Smoke tests for the 4 new Phase 0 observers (2026-05-19)."""
from datetime import datetime, timezone
from strategies.crypto_observer import (
    FundingRateFadeObserver, VolSkewFadeObserver,
    EtfFlowLagObserver, CmeBasisFadeObserver,
    EventContext, Leg,
)

def _leg(threshold, ya=0.10, yb_size=50):
    return Leg(
        ticker=f"KXBTC-26MAY1822-B{int(threshold)}",
        kind="bucketed", threshold=threshold,
        ya=ya, yb=0.05, na=1-ya, nb=0.05,
        ya_size=10, yb_size=yb_size, na_size=10, nb_size=10,
        liquidity=100.0, raw={},
    )


def _ctx(spot=80000, funding=None, skew=None, etf_z=None, cme_b=None, mts=1800):
    return EventContext(
        series="KXBTC", asset="BTC", event_key="26MAY1822",
        now=datetime.now(timezone.utc), spot=spot,
        spot_60s_ago=spot, deribit_iv=0.50,
        b_legs=[_leg(spot - 1000), _leg(spot - 500), _leg(spot + 500), _leg(spot + 1000)],
        t_legs=[], open_time_secs=None, close_time_secs=mts,
        funding_8h=funding, iv_skew=skew,
        etf_flow_z=etf_z, cme_basis=cme_b,
    )


def test_funding_fade_no_signal_when_funding_small():
    o = FundingRateFadeObserver()
    sigs = o.evaluate(_ctx(funding=0.0001))  # below 0.0003 threshold
    assert sigs == []


def test_funding_fade_fires_upside_strikes_on_positive_funding():
    o = FundingRateFadeObserver()
    sigs = o.evaluate(_ctx(funding=0.0005))  # positive elevated
    # Should only fire on strikes > spot (upside)
    assert len(sigs) == 2  # 2 strikes > spot
    for s in sigs:
        assert s.predicted_outcome == "no"
        assert s.features["side"] == "fade_upside"


def test_funding_fade_fires_downside_strikes_on_negative_funding():
    o = FundingRateFadeObserver()
    sigs = o.evaluate(_ctx(funding=-0.0005))
    assert len(sigs) == 2  # 2 strikes < spot
    for s in sigs:
        assert s.features["side"] == "fade_downside"


def test_vol_skew_fade_no_signal_when_flat():
    o = VolSkewFadeObserver()
    sigs = o.evaluate(_ctx(skew=0.02))  # below 0.05 threshold
    assert sigs == []


def test_vol_skew_fade_fires_downside_on_put_skew():
    o = VolSkewFadeObserver()
    sigs = o.evaluate(_ctx(skew=0.10))  # put-skew (positive)
    # Should fire on strikes < spot (fade downside fear)
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_downside"


def test_vol_skew_fade_fires_upside_on_call_skew():
    o = VolSkewFadeObserver()
    sigs = o.evaluate(_ctx(skew=-0.10))
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_upside"


def test_observers_skip_when_data_missing():
    f = FundingRateFadeObserver()
    v = VolSkewFadeObserver()
    ctx = _ctx(funding=None, skew=None)
    assert f.evaluate(ctx) == []
    assert v.evaluate(ctx) == []


def test_observers_respect_mts_window():
    f = FundingRateFadeObserver()
    # mts > 28800 (8h) means signal won't fire
    sigs = f.evaluate(_ctx(funding=0.001, mts=30000))
    assert sigs == []


# ── ETF flow lag ───────────────────────────────────────────────────


def test_etf_flow_no_signal_when_small_zscore():
    o = EtfFlowLagObserver()
    sigs = o.evaluate(_ctx(etf_z=1.0))   # below 1.5σ
    assert sigs == []


def test_etf_flow_fires_downside_strikes_on_positive_z():
    o = EtfFlowLagObserver()
    sigs = o.evaluate(_ctx(etf_z=2.0))   # bullish flow
    # Only strikes < spot
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_downside"


def test_etf_flow_fires_upside_strikes_on_negative_z():
    o = EtfFlowLagObserver()
    sigs = o.evaluate(_ctx(etf_z=-2.0))   # bearish flow
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_upside"


def test_etf_flow_skip_when_none():
    o = EtfFlowLagObserver()
    assert o.evaluate(_ctx(etf_z=None)) == []


# ── CME basis ──────────────────────────────────────────────────────


def test_cme_basis_no_signal_when_flat():
    o = CmeBasisFadeObserver()
    sigs = o.evaluate(_ctx(cme_b=0.001))   # below 0.2% threshold
    assert sigs == []


def test_cme_basis_fires_downside_on_contango():
    o = CmeBasisFadeObserver()
    sigs = o.evaluate(_ctx(cme_b=0.005))   # 0.5% contango
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_downside"


def test_cme_basis_fires_upside_on_backwardation():
    o = CmeBasisFadeObserver()
    sigs = o.evaluate(_ctx(cme_b=-0.005))
    assert len(sigs) == 2
    for s in sigs:
        assert s.features["side"] == "fade_upside"


def test_cme_basis_skip_when_none():
    o = CmeBasisFadeObserver()
    assert o.evaluate(_ctx(cme_b=None)) == []


if __name__ == "__main__":
    import sys
    g = dict(globals())
    fails = 0
    for name, fn in g.items():
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                print(f"FAIL {name}: {e}")
                fails += 1
    sys.exit(fails)
