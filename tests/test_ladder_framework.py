import pytest
from dataclasses import FrozenInstanceError
from core.ladder_framework import LadderLeg, LadderGroup, ArbLeg, ArbTrade, FadeTrade


def test_ladder_leg_is_frozen():
    leg = LadderLeg(
        ticker="KXBTC-26MAY09-1500-B70250", threshold=70250.0,
        yes_ask=0.45, yes_bid=0.43, no_ask=0.57, no_bid=0.55,
        yes_depth=120.0, no_depth=80.0,
    )
    with pytest.raises(FrozenInstanceError):
        leg.threshold = 0


def test_ladder_group_stores_legs_as_provided():
    legs = (
        LadderLeg("T1", 70250.0, 0.45, 0.43, 0.57, 0.55, 120.0, 80.0),
        LadderLeg("T2", 70350.0, 0.42, 0.40, 0.60, 0.58, 100.0, 90.0),
    )
    g = LadderGroup(
        series="KXBTC", event_key="26MAY09-1500",
        ladder_type="cumulative", direction="asc", legs=legs,
    )
    assert g.legs[0].threshold < g.legs[1].threshold
    assert g.ladder_type == "cumulative"


def test_arb_trade_carries_legs_and_edge():
    trade = ArbTrade(
        group_key="KXBTC-26MAY09-1500",
        legs=(
            ArbLeg(ticker="T1", side="yes", size_usdc=25.0, price=0.45),
            ArbLeg(ticker="T2", side="no", size_usdc=25.0, price=0.50),
        ),
        expected_edge_usdc=2.50,
    )
    assert len(trade.legs) == 2
    assert trade.expected_edge_usdc == 2.50


def test_fade_trade_records_inversion_amount():
    f = FadeTrade(
        group_key="KXFED-FOMC-26JUN", ticker="KXFED-FOMC-26JUN-C25",
        side="yes", size_usdc=20.0, price=0.30, inversion_amount=0.07,
    )
    assert f.inversion_amount == 0.07
