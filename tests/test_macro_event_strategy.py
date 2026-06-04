from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.macro_event import MacroEventStrategy


def _cfg():
    return MagicMock(
        MACRO_EVENT_MIN_DEVIATION=0.10,
        MACRO_EVENT_MIN_DEPTH_USDC=20.0,
        MACRO_EVENT_BASE_SIZE_USDC=20.0,
        MACRO_EVENT_MAX_POSITION_PER_TICKER=30.0,
        MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        MACRO_EVENT_WINDOW_MAX_HOURS=336.0,
        MACRO_EVENT_WINDOW_MIN_MINUTES=30.0,
        MACRO_CONSENSUS_STALE_HOURS=24.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_universe_filter_includes_only_kxcpi_and_kxfeddecision():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXCPI-26JUN-T0.3"}) is True
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is True
    assert s._is_target_market({"ticker": "KXFED-26JUN-T4.25"}) is False
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False


def test_extract_event_key_for_kxfeddecision():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key({"ticker": "KXFEDDECISION-26JUN-C25"}) == "FOMC-26JUN"


def test_extract_event_key_for_kxcpi():
    # Kalshi names KXCPI markets by the data's REFERENCE month, but the
    # consensus cron keys macro_consensus rows by the RELEASE month (the
    # following month — e.g. June CPI is released in July). _extract_event_key
    # shifts +1 month so the lookup matches the stored rows. Asserting the old
    # passthrough (CPI-26JUN) here is what silently blocked every CPI fire.
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key({"ticker": "KXCPI-26JUN-T0.3"}) == "CPI-26JUL"
    assert s._extract_event_key({"ticker": "KXCPI-26MAY-T0.3"}) == "CPI-26JUN"
    # Year rollover: December reference -> January release of the next year.
    assert s._extract_event_key({"ticker": "KXCPI-26DEC-T0.3"}) == "CPI-27JAN"
    # Unparseable month token returns empty (no false match).
    assert s._extract_event_key({"ticker": "KXCPI-ZZZ-T0.3"}) == ""


def test_extract_bucket_returns_last_segment():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_bucket({"ticker": "KXFEDDECISION-26JUN-C25"}) == "C25"
    assert s._extract_bucket({"ticker": "KXCPI-26JUN-T0.3"}) == "T0.3"


def test_window_check_inside():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(hours=24)).isoformat(),
    }
    assert s._inside_window(market, now=now) is True


def test_window_check_too_close():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(minutes=10)).isoformat(),  # < 30 min
    }
    assert s._inside_window(market, now=now) is False


def test_window_check_too_far():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXFEDDECISION-26JUN-C25",
        "close_time": (now + timedelta(days=20)).isoformat(),  # > 14 days
    }
    assert s._inside_window(market, now=now) is False


def test_compute_edge_buy_yes_when_consensus_above_implied():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # Kalshi implied 0.25 (yes_ask 0.27, yes_bid 0.23 → mid 0.25)
    # Consensus 0.40
    edge = s._compute_edge(
        kalshi_yes_ask=0.27, kalshi_yes_bid=0.23,
        kalshi_no_ask=0.77, kalshi_no_bid=0.73,
        consensus=0.40,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.15) < 1e-6  # 0.40 - 0.25
    assert edge["entry_price"] == 0.27  # yes_ask


def test_compute_edge_buy_no_when_consensus_below_implied():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # Kalshi implied 0.55 (yes_ask 0.57, yes_bid 0.53 → mid 0.55)
    # Consensus 0.30 → buy NO
    edge = s._compute_edge(
        kalshi_yes_ask=0.57, kalshi_yes_bid=0.53,
        kalshi_no_ask=0.47, kalshi_no_bid=0.43,
        consensus=0.30,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert abs(edge["deviation"] + 0.25) < 1e-6  # negative deviation
    assert edge["entry_price"] == 0.47  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.27, kalshi_yes_bid=0.23,
        kalshi_no_ask=0.77, kalshi_no_bid=0.73,
        consensus=0.30,  # mid 0.25, dev 0.05 < 0.10
    )
    assert edge is None


def test_compute_edge_skips_when_ev_negative():
    """Even above MIN_DEVIATION, skip when entry + fee > consensus."""
    cfg = _cfg()
    cfg.MACRO_EVENT_MIN_DEVIATION = 0.05  # lower threshold so dev passes
    s = MacroEventStrategy(cfg, MagicMock(), MagicMock(), MagicMock())
    # Mid 0.20, ask 0.25, consensus 0.27 → dev 0.07 ≥ 0.05.
    # But EV = consensus - entry - fee = 0.27 - 0.25 - 0.07 = -0.05 → skip.
    edge = s._compute_edge(
        kalshi_yes_ask=0.25, kalshi_yes_bid=0.15,
        kalshi_no_ask=0.85, kalshi_no_bid=0.75,
        consensus=0.27,
    )
    assert edge is None


def test_size_for_edge_scales_with_deviation():
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # base = 20, clamp(|0.10|/0.10, 0.5, 3.0) = 1.0 → size 20
    assert s._size_for_edge(deviation=0.10) == 20.0
    # base × clamp(|0.30|/0.10, 0.5, 3.0) = 3.0 → 60, capped at 30 (per ticker)
    assert s._size_for_edge(deviation=0.30) == 30.0
    # base × clamp(|0.05|/0.10, 0.5, 3.0) = 0.5 → 10
    assert s._size_for_edge(deviation=0.05) == 10.0


@pytest.mark.asyncio
async def test_scan_and_execute_fires_yes_when_all_gates_pass():
    """End-to-end happy path: window OK, consensus fresh, dev > min, EV > 0,
    depth OK, no existing trade, exposure within cap → fire BUY YES."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    # OrderManager owns the DB in production; the strategy reaches into it
    # here for test isolation, mirroring how ladder_fed exposes _db indirectly
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))  # yes_ask, no_ask
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,  # cents
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["ticker"] == "KXCPI-26JUN-T0.3"
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "macro_event"
    # base 20 × clamp(0.15/0.10, 0.5, 3.0) = 30, capped at 30
    assert call_kwargs["size_usdc"] == 30.0


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_consensus_stale():
    cfg = _cfg()
    stale = datetime.now(timezone.utc) - timedelta(hours=48)  # > 24
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40, "fetched_at": stale.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_exposure_cap_exceeded():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    # 60 already open from another strategy + 30 size → 90 > 80 cap
    db.total_open_exposure = AsyncMock(return_value=60.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_and_execute_skips_when_depth_insufficient():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_macro_consensus = AsyncMock(return_value={
        "prob": 0.40,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.27, 0.73))
    client.get_order_book_depth = MagicMock(return_value=(5.0, 5.0))  # < 20

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{
        "ticker": "KXCPI-26JUN-T0.3",
        "yes_ask": 27, "yes_bid": 23, "no_ask": 77, "no_bid": 73,
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }])

    s = MacroEventStrategy(cfg, om, scanner, client)
    await s.scan_and_execute()

    om.market_order.assert_not_called()
