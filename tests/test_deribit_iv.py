from unittest.mock import patch, MagicMock

import pytest

from core.deribit_iv import DeribitIVScraper, parse_instrument_name, extract_atm_iv


def test_parse_instrument_name():
    assert parse_instrument_name("BTC-13MAY26-93000-C") == ("BTC", "2026-05-13", 93000.0, "C")
    assert parse_instrument_name("ETH-20MAY26-3500-P") == ("ETH", "2026-05-20", 3500.0, "P")
    assert parse_instrument_name("BTC-INVALID") is None
    assert parse_instrument_name("") is None


def test_extract_atm_iv_picks_closest_strike():
    chain = [
        {"instrument_name": "BTC-13MAY26-92000-C", "mark_iv": 0.62},
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},  # closest call
        {"instrument_name": "BTC-13MAY26-94000-C", "mark_iv": 0.61},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.64},  # closest put
        {"instrument_name": "BTC-13MAY26-94000-P", "mark_iv": 0.66},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=92800.0)
    # closest strike to 92800 is 93000. atm_iv = (0.60 + 0.64) / 2 = 0.62
    assert "2026-05-13" in out
    assert abs(out["2026-05-13"] - 0.62) < 1e-6


def test_extract_atm_iv_handles_multiple_expiries():
    chain = [
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
        {"instrument_name": "BTC-20MAY26-93000-C", "mark_iv": 0.55},
        {"instrument_name": "BTC-20MAY26-93000-P", "mark_iv": 0.57},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert abs(out["2026-05-13"] - 0.61) < 1e-6
    assert abs(out["2026-05-20"] - 0.56) < 1e-6


def test_extract_atm_iv_skips_other_assets():
    chain = [
        {"instrument_name": "ETH-13MAY26-3500-C", "mark_iv": 0.75},
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert "2026-05-13" in out
    assert len(out) == 1


def test_extract_atm_iv_skips_expiry_with_only_one_side():
    chain = [
        {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
        # No put for this expiry
        {"instrument_name": "BTC-20MAY26-93000-C", "mark_iv": 0.55},
        {"instrument_name": "BTC-20MAY26-93000-P", "mark_iv": 0.57},
    ]
    out = extract_atm_iv(chain, asset="BTC", current_spot=93000.0)
    assert "2026-05-13" not in out
    assert "2026-05-20" in out


def test_scraper_returns_chain_on_http_200():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "result": [
            {"instrument_name": "BTC-13MAY26-93000-C", "mark_iv": 0.60},
            {"instrument_name": "BTC-13MAY26-93000-P", "mark_iv": 0.62},
        ]
    }
    with patch("core.deribit_iv.httpx.get", return_value=fake_response):
        scraper = DeribitIVScraper()
        chain = scraper.fetch_chain("BTC")
        assert chain is not None
        assert len(chain) == 2


def test_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.deribit_iv.httpx.get", return_value=fake_response):
        scraper = DeribitIVScraper()
        assert scraper.fetch_chain("BTC") is None
