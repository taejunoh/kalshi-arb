from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
    s = MacroEventStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._extract_event_key({"ticker": "KXCPI-26JUN-T0.3"}) == "CPI-26JUN"


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
