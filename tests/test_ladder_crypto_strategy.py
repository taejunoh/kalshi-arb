from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.ladder_crypto import LadderCryptoStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        LADDER_STALE_ORDERBOOK_SEC=10.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_b_suffix():
    """Legacy ticker format: B<int> grid."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("B70250") == 70250.0
    assert s._parse_suffix("B1310") == 1310.0
    assert s._parse_suffix("BAD") is None
    assert s._parse_suffix("") is None


def test_parse_t_suffix_current_format():
    """Current Kalshi ticker format: T<float>, e.g. T89749.99."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("T89749.99") == 89749.99
    assert s._parse_suffix("T3059.99") == 3059.99
    assert s._parse_suffix("T1.7799") == 1.7799


def test_event_key_legacy_4part_format():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # KXBTC-26MAY09-1500-B70250 → 26MAY09-1500
    assert s._extract_event_key(
        {"ticker": "KXBTC-26MAY09-1500-B70250"}
    ) == "26MAY09-1500"


def test_event_key_current_3part_format():
    """Current Kalshi format: KXBTC-26MAY1017-T89749.99 → '26MAY1017'."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key(
        {"ticker": "KXBTC-26MAY1017-T89749.99"}
    ) == "26MAY1017"


def test_atm_window_filter_keeps_only_within_n():
    """Leg whose threshold is within ATM ± window survives; far OTM/ITM dropped."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    legs = [
        {"ticker": f"KXBTC-26MAY09-1500-B{p}",
         "yes_ask": 0.5, "yes_bid": 0.48, "no_ask": 0.52, "no_bid": 0.50,
         "yes_depth": 100.0, "no_depth": 100.0}
        for p in (70000, 70100, 70200, 70300, 70400, 70500, 70600)
    ]
    # ATM = 70300. Window = ±2 buckets ($100 each) → keep 70100..70500
    filtered = s._atm_window_filter(legs, atm_price=70300.0, window_n=2,
                                     bucket_size=100.0)
    keep = sorted(int(m["ticker"].rsplit("-B", 1)[-1]) for m in filtered)
    assert keep == [70100, 70200, 70300, 70400, 70500]


def test_universe_filter():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is True
    assert s._is_target_market({"ticker": "KXETH-26MAY09-1500-B1310"}) is True
    assert s._is_target_market({"ticker": "KXSOL-26MAY09-1500-B100"}) is False


def test_ladder_kind_distinguishes_b_and_t():
    """B-prefix → bucketed (single $X-$X+grid range); T-prefix → cumulative."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._ladder_kind("B70250") == "bucketed"
    assert s._ladder_kind("T89749.99") == "cumulative"
    assert s._ladder_kind("T71200") == "cumulative"
    assert s._ladder_kind("X12345") is None
    assert s._ladder_kind("") is None


def _b(ticker: str, yes: float, no: float, depth: float = 200.0) -> dict:
    return {"ticker": ticker, "yes_ask": yes, "yes_bid": yes,
            "no_ask": no, "no_bid": no,
            "yes_depth": depth, "no_depth": depth}


@pytest.mark.asyncio
async def test_b_ladder_does_not_fire_cumulative_fade_on_natural_peak():
    """Regression: bucketed legs with natural ATM-peaked slope must NOT be
    interpreted as cumulative inversions. This was the n=35 / -83.6% ROI
    failure mode — adjacent B-buckets 0.02 → 0.05 → 0.15 → 0.50 (peak)
    were being read as cumulative inversions, triggering YES buys on
    deep-OTM legs that lost ~100% of the time.
    """
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    scanner = MagicMock()
    # Spot ~$89,750 with $100 grid. yes_ask peaks at the ATM bucket.
    bucket_prices = [
        ("B89450", 0.02, 0.98),
        ("B89550", 0.05, 0.95),
        ("B89650", 0.15, 0.85),
        ("B89750", 0.50, 0.50),
        ("B89850", 0.15, 0.85),
        ("B89950", 0.05, 0.95),
        ("B90050", 0.02, 0.98),
    ]
    # Pad with far-OTM tail buckets so sum_mid lands inside the sanity window.
    tail_prices = [(f"B{p}", 0.005, 0.995) for p in
                   (89150, 89250, 89350, 90150, 90250, 90350, 90450, 90550)]
    markets = [_b(f"KXBTC-26MAY1208-{suf}", y, n)
               for suf, y, n in (bucket_prices + tail_prices)]
    scanner.get_unlocked_markets = MagicMock(return_value=markets)

    client = MagicMock()
    client.get_market_prices = MagicMock(
        side_effect=lambda t: next(((y, n) for s, y, n in (bucket_prices + tail_prices)
                                    if t.endswith(s)), (None, None))
    )
    client.get_order_book_depth = MagicMock(return_value=(200.0, 200.0))

    s = LadderCryptoStrategy(_cfg(), om, scanner, client)
    await s.scan_and_execute()
    # The pre-fix cumulative fade detector would have fired multiple YES
    # buys on B89450/B89550/etc. After the fix B-legs route through bucketed
    # math; sum_mid sits near 1.0 with no real mispricing → zero fires.
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_b_ladder_skips_when_observed_bucket_set_is_incomplete():
    """Bucketed sum-to-1 / implied-prob math is only sound on a (mostly)
    complete bucket set. If the scanner-cached view of an event covers
    only ATM ± a couple buckets (sum_mid << 1), no signal should fire —
    arb / fade math on partial sets generates false positives.
    """
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    scanner = MagicMock()
    bucket_prices = [
        ("B89550", 0.05, 0.95),
        ("B89650", 0.15, 0.85),
        ("B89750", 0.50, 0.50),
        # Missing the rest of the right tail — sum_mid will be ~0.70.
    ]
    markets = [_b(f"KXBTC-26MAY1208-{suf}", y, n)
               for suf, y, n in bucket_prices]
    scanner.get_unlocked_markets = MagicMock(return_value=markets)
    client = MagicMock()
    client.get_market_prices = MagicMock(
        side_effect=lambda t: next(((y, n) for s, y, n in bucket_prices
                                    if t.endswith(s)), (None, None))
    )
    client.get_order_book_depth = MagicMock(return_value=(200.0, 200.0))

    s = LadderCryptoStrategy(_cfg(), om, scanner, client)
    await s.scan_and_execute()
    assert om.market_order.await_count == 0


@pytest.mark.asyncio
async def test_b_and_t_markets_are_partitioned_into_distinct_groups():
    """A single Kalshi event publishes both B<int> and T<float> markets.
    They must be processed under different ladder mechanics — never mixed
    into the same group.
    """
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    scanner = MagicMock()
    markets = [
        _b("KXBTC-26MAY1208-B89750", 0.50, 0.50),
        _b("KXBTC-26MAY1208-T89799.99", 0.10, 0.90),
        _b("KXBTC-26MAY1208-T71200", 0.97, 0.03),
    ]
    scanner.get_unlocked_markets = MagicMock(return_value=markets)
    captured: list = []

    async def _fake_handle(group):
        captured.append(group)

    client = MagicMock()
    client.get_market_prices = MagicMock(
        side_effect=lambda t: {
            "KXBTC-26MAY1208-B89750": (0.50, 0.50),
            "KXBTC-26MAY1208-T89799.99": (0.10, 0.90),
            "KXBTC-26MAY1208-T71200": (0.97, 0.03),
        }.get(t, (None, None))
    )
    client.get_order_book_depth = MagicMock(return_value=(200.0, 200.0))

    s = LadderCryptoStrategy(_cfg(), om, scanner, client)
    s._handle_group = _fake_handle
    await s.scan_and_execute()

    kinds = {g.ladder_type for g in captured}
    assert kinds == {"bucketed", "cumulative"}
    # No group should contain both B and T legs.
    for g in captured:
        suffixes = [l.ticker.rsplit("-", 1)[-1][0] for l in g.legs]
        assert len(set(suffixes)) == 1
