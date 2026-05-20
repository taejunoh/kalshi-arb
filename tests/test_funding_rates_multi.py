"""Unit tests for core/funding_rates_multi.py.

Mocks httpx.AsyncClient.get so the venue HTTP endpoints are not hit
during test runs.
"""
from __future__ import annotations
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from core.funding_rates_multi import (
    VENUES, get_all_funding, get_funding, _clear_cache_for_tests,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _deribit_payload(funding_8h: float) -> dict:
    return {"result": {"funding_8h": funding_8h}}


def _okx_payload(funding_rate_per_period: float) -> dict:
    # OKX returns fundingRate per 8h period (already 8h on USDT-SWAP)
    # as a string. funding_rates_multi.py is responsible for str->float.
    return {"data": [{"fundingRate": str(funding_rate_per_period)}]}


def _gate_payload(funding_rate_per_period: float) -> dict:
    # Gate.io V4 /contracts/{asset}_USDT returns the contract object
    # at the top level with funding_rate as a string and
    # funding_interval (seconds). 8h-native for BTC/ETH USDT perps.
    return {
        "name": "BTC_USDT",
        "funding_rate": str(funding_rate_per_period),
        "funding_interval": 28800,
        "mark_price": "70000",
    }


def _mexc_payload(funding_rate_per_period: float) -> dict:
    # MEXC V1 /contract/funding_rate/{asset}_USDT returns
    # success + data{fundingRate, collectCycle}. 8h-native.
    return {
        "success": True,
        "code": 0,
        "data": {
            "symbol": "BTC_USDT",
            "fundingRate": funding_rate_per_period,
            "collectCycle": 8,
            "nextSettleTime": 0,
        },
    }


def _make_fake_get(per_url_payload: dict[str, dict],
                   per_url_status: dict[str, int] | None = None):
    """Build a fake httpx.AsyncClient.get that dispatches on URL substring.

    per_url_payload keys are substrings to match (e.g. "deribit.com",
    "okx.com", "gateio.ws", "mexc.com").
    """
    per_url_status = per_url_status or {}

    async def fake_get(*a, **k):
        # When patching httpx.AsyncClient.get via AsyncMock(side_effect=...),
        # side_effect receives (url, ...) — self is NOT passed.
        url = a[0] if a else k.get("url", "")
        for needle, payload in per_url_payload.items():
            if needle in url:
                return _FakeResponse(per_url_status.get(needle, 200), payload)
        return _FakeResponse(404, {})
    return fake_get


@pytest.mark.asyncio
async def test_get_all_funding_returns_all_four_venues():
    """All four venues respond 200 → dict has 4 entries, all floats."""
    _clear_cache_for_tests()
    fake_get = _make_fake_get({
        "deribit.com": _deribit_payload(0.0005),
        "okx.com":     _okx_payload(0.0003),
        "gateio.ws":   _gate_payload(0.0007),
        "mexc.com":    _mexc_payload(0.0006),
    })
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await get_all_funding("BTC")
    assert set(result.keys()) == set(VENUES)
    assert result["deribit"] == pytest.approx(0.0005)
    assert result["okx"]     == pytest.approx(0.0003)
    assert result["gate"]    == pytest.approx(0.0007)
    assert result["mexc"]    == pytest.approx(0.0006)


@pytest.mark.asyncio
async def test_get_all_funding_one_venue_failure_others_succeed():
    """One venue returns 500 → its entry is None, others still populated."""
    _clear_cache_for_tests()
    fake_get = _make_fake_get(
        per_url_payload={
            "deribit.com": _deribit_payload(0.0005),
            "okx.com":     {},
            "gateio.ws":   _gate_payload(0.0007),
            "mexc.com":    _mexc_payload(0.0006),
        },
        per_url_status={"okx.com": 500},
    )
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await get_all_funding("BTC")
    assert result["deribit"] == pytest.approx(0.0005)
    assert result["okx"] is None
    assert result["gate"]    == pytest.approx(0.0007)
    assert result["mexc"]    == pytest.approx(0.0006)


@pytest.mark.asyncio
async def test_get_all_funding_xrp_skips_deribit_silently():
    """Deribit has no XRP-PERPETUAL — should return None for that venue
    without making the HTTP call (no warning log spam). OKX/Gate/MEXC
    should still be fetched and parsed."""
    _clear_cache_for_tests()

    # Track which URLs were actually called
    called_urls: list[str] = []

    fake_payloads = {
        # Deribit should NOT be called — if it is, this payload will surface
        "deribit.com": _deribit_payload(0.0001),
        "okx.com":     _okx_payload(0.0003),
        "gateio.ws":   _gate_payload(0.0004),
        "mexc.com":    _mexc_payload(0.0005),
    }

    async def fake_get(*a, **k):
        url = a[0] if a else k.get("url", "")
        called_urls.append(url)
        for needle, payload in fake_payloads.items():
            if needle in url:
                return _FakeResponse(200, payload)
        return _FakeResponse(404, {})

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await get_all_funding("XRP")

    # Deribit was never called → no URL containing 'deribit.com' should appear
    assert not any("deribit.com" in u for u in called_urls), (
        f"deribit.com was called for XRP — expected silent skip. URLs: {called_urls}"
    )
    # Other 3 venues were called
    assert any("okx.com" in u for u in called_urls)
    assert any("gateio.ws" in u for u in called_urls)
    assert any("mexc.com" in u for u in called_urls)
    # Result: deribit=None (skipped), others populated
    assert result["deribit"] is None
    assert result["okx"]  == pytest.approx(0.0003)
    assert result["gate"] == pytest.approx(0.0004)
    assert result["mexc"] == pytest.approx(0.0005)
