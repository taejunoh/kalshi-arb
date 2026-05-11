import math
import pytest

from core.macro_consensus import bucket_prob_from_normal, normal_cdf


def test_normal_cdf_zero_mean_unit_variance():
    """N(0, 1) CDF: P(Z <= 0) = 0.5, P(Z <= 1.96) ≈ 0.975"""
    assert abs(normal_cdf(0.0, mu=0.0, sigma=1.0) - 0.5) < 1e-6
    assert abs(normal_cdf(1.96, mu=0.0, sigma=1.0) - 0.9750) < 1e-3
    assert abs(normal_cdf(-1.96, mu=0.0, sigma=1.0) - 0.0250) < 1e-3


def test_bucket_prob_centered_bucket_around_mean():
    """N(0.3, 0.1): P(0.2 <= X <= 0.4) ≈ 0.6827 (1-sigma window)"""
    p = bucket_prob_from_normal(low=0.2, high=0.4, mu=0.3, sigma=0.1)
    assert abs(p - 0.6827) < 1e-3


def test_bucket_prob_far_tail():
    """N(0.3, 0.1): P(0.6 <= X <= 0.7) ≈ 0.001 (3+ sigma)"""
    p = bucket_prob_from_normal(low=0.6, high=0.7, mu=0.3, sigma=0.1)
    assert p < 0.01


def test_bucket_prob_open_lower_bound():
    """low=None → P(X <= high), no lower clamp"""
    p = bucket_prob_from_normal(low=None, high=0.3, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_open_upper_bound():
    """high=None → P(X >= low), no upper clamp"""
    p = bucket_prob_from_normal(low=0.3, high=None, mu=0.3, sigma=0.1)
    assert abs(p - 0.5) < 1e-6


def test_bucket_prob_zero_sigma_returns_indicator():
    """Degenerate sigma=0 case: deterministic point mass at mu"""
    # mu inside bucket → 1.0
    assert bucket_prob_from_normal(0.2, 0.4, 0.3, 0.0) == 1.0
    # mu outside bucket → 0.0
    assert bucket_prob_from_normal(0.5, 0.6, 0.3, 0.0) == 0.0


from unittest.mock import patch, MagicMock
from core.macro_consensus import FedWatchScraper


def test_fedwatch_scraper_parses_meeting_probabilities():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "meetings": [
            {"date": "2026-06-17", "probabilities": {
                "C50": 0.05, "C25": 0.65, "H0": 0.28, "H25": 0.02,
            }},
        ]
    }
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        out = scraper.fetch_meeting("2026-06-17")
        assert out == {"C50": 0.05, "C25": 0.65, "H0": 0.28, "H25": 0.02}


def test_fedwatch_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


def test_fedwatch_scraper_returns_none_on_403_ip_block():
    """CME IP-blocks the FedWatch endpoint with HTTP 403 — verified live
    2026-05-11. Scraper must return None and not raise."""
    fake_response = MagicMock()
    fake_response.status_code = 403
    fake_response.text = '{"message": "This IP address is blocked..."}'
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


def test_fedwatch_scraper_returns_none_on_malformed_json():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.side_effect = ValueError("malformed")
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


def test_fedwatch_scraper_returns_none_on_missing_date():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"meetings": []}
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        scraper = FedWatchScraper()
        assert scraper.fetch_meeting("2026-06-17") is None


from core.macro_consensus import CPINowcastScraper, CPI_NOWCAST_DEFAULT_STD


# Real-shape sample matching nowcast_month.json (fusioncharts format).
# Outer list = one record per charted month; latest is the active one.
SAMPLE_NOWCAST_JSON_LATEST_RECORD = {
    "chart": {"subcaption": "2026-5", "yaxisname": "Month-over-month percent change"},
    "categories": [{"category": [{"label": "05/01"}, {"label": "05/04"}]}],
    "dataset": [
        {
            "seriesname": "CPI Inflation",
            "data": [
                {"value": "0.418054848749231", "tooltext": "..."},
                {"value": "0.418054848749231", "tooltext": "..."},
            ],
        },
        {"seriesname": "Core CPI Inflation", "data": [{"value": "0.212"}]},
        {"seriesname": "Actual CPI Inflation", "data": [{"value": ""}]},
    ],
}


def test_cpi_nowcast_scraper_extracts_latest_cpi_inflation_value():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = [SAMPLE_NOWCAST_JSON_LATEST_RECORD]
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        out = CPINowcastScraper().fetch()
        assert out is not None
        assert abs(out["cpi_mom_point"] - 0.41805) < 1e-4
        assert out["cpi_mom_std"] == CPI_NOWCAST_DEFAULT_STD


def test_cpi_nowcast_scraper_uses_only_latest_record():
    """Multiple records in the list — scraper must pick the last (most recent)."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = [
        {"dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.10"}]}]},
        {"dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.42"}]}]},
    ]
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        out = CPINowcastScraper().fetch()
        assert abs(out["cpi_mom_point"] - 0.42) < 1e-6


def test_cpi_nowcast_scraper_skips_empty_values():
    """The 'Actual' series ships with empty strings — must not be picked
    as CPI Inflation, and within CPI Inflation we walk back from the end
    until we find a non-empty value."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = [
        {"dataset": [{
            "seriesname": "CPI Inflation",
            "data": [{"value": "0.30"}, {"value": ""}, {"value": None}],
        }]}
    ]
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        out = CPINowcastScraper().fetch()
        assert abs(out["cpi_mom_point"] - 0.30) < 1e-6


def test_cpi_nowcast_scraper_returns_none_when_series_missing():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = [{"dataset": [{"seriesname": "PCE Inflation", "data": [{"value": "0.2"}]}]}]
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        assert CPINowcastScraper().fetch() is None


def test_cpi_nowcast_scraper_returns_none_on_unexpected_shape():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"not": "a list"}
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        assert CPINowcastScraper().fetch() is None


def test_cpi_nowcast_scraper_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 503
    with patch("core.macro_consensus.httpx.get", return_value=fake_response):
        assert CPINowcastScraper().fetch() is None
