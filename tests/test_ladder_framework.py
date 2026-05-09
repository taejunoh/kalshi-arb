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


from core.ladder_framework import group_legs_into_ladders


def _btc_market(suffix: str, yes_ask: float, yes_bid: float, depth: float = 100.0) -> dict:
    return {
        "ticker": f"KXBTC-26MAY09-1500-{suffix}",
        "yes_ask": yes_ask, "yes_bid": yes_bid,
        "no_ask": 1.0 - yes_bid, "no_bid": 1.0 - yes_ask,
        "yes_depth": depth, "no_depth": depth,
    }


def _btc_suffix_parser(suffix: str) -> float | None:
    if not suffix.startswith("B"):
        return None
    try:
        return float(suffix[1:])
    except ValueError:
        return None


def _btc_event_key(m: dict) -> str:
    parts = m["ticker"].split("-")
    return "-".join(parts[1:-1])  # 26MAY09-1500


def test_group_legs_groups_by_event_key_and_sorts_asc():
    markets = [
        _btc_market("B70450", 0.40, 0.38),
        _btc_market("B70250", 0.45, 0.43),
        _btc_market("B70350", 0.42, 0.40),
    ]
    groups = group_legs_into_ladders(
        markets,
        suffix_parser=_btc_suffix_parser,
        ladder_type="cumulative",
        event_key_extractor=_btc_event_key,
        direction="asc",
    )
    assert len(groups) == 1
    g = groups[0]
    assert g.event_key == "26MAY09-1500"
    assert g.series == "KXBTC"
    assert [l.threshold for l in g.legs] == [70250.0, 70350.0, 70450.0]
    assert g.direction == "asc"


def test_group_legs_skips_unparseable_suffix():
    markets = [
        _btc_market("B70250", 0.45, 0.43),
        {"ticker": "KXBTC-26MAY09-1500-INVALID",
         "yes_ask": 0.5, "yes_bid": 0.5, "no_ask": 0.5, "no_bid": 0.5,
         "yes_depth": 100.0, "no_depth": 100.0},
    ]
    groups = group_legs_into_ladders(
        markets, _btc_suffix_parser, "cumulative", _btc_event_key,
    )
    assert len(groups) == 1
    assert len(groups[0].legs) == 1


def test_group_legs_desc_direction_reverses_sort():
    # KXTRUMPAPPROVALBELOW: "≤ X%" — bigger X is more permissive (more likely)
    markets = [
        {"ticker": "KXAB-26MAY-37", "yes_ask": 0.55, "yes_bid": 0.53,
         "no_ask": 0.47, "no_bid": 0.45, "yes_depth": 50.0, "no_depth": 50.0},
        {"ticker": "KXAB-26MAY-33", "yes_ask": 0.20, "yes_bid": 0.18,
         "no_ask": 0.82, "no_bid": 0.80, "yes_depth": 50.0, "no_depth": 50.0},
    ]
    groups = group_legs_into_ladders(
        markets,
        suffix_parser=lambda s: float(s) if s.isdigit() else None,
        ladder_type="cumulative",
        event_key_extractor=lambda m: m["ticker"].rsplit("-", 1)[0],
        direction="desc",
    )
    g = groups[0]
    # desc: bigger threshold first. 37 > 33 → first leg is 37.
    assert [l.threshold for l in g.legs] == [37.0, 33.0]


def test_group_legs_separates_distinct_event_keys():
    markets = [
        _btc_market("B70250", 0.45, 0.43),
        # different close time
        {"ticker": "KXBTC-26MAY10-1500-B70250", "yes_ask": 0.40,
         "yes_bid": 0.38, "no_ask": 0.62, "no_bid": 0.60,
         "yes_depth": 100.0, "no_depth": 100.0},
    ]
    groups = group_legs_into_ladders(
        markets, _btc_suffix_parser, "cumulative", _btc_event_key,
    )
    assert len(groups) == 2
    assert {g.event_key for g in groups} == {"26MAY09-1500", "26MAY10-1500"}
