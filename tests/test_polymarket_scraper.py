from unittest.mock import patch, MagicMock

import pytest

from core.polymarket_scraper import PolymarketScraper, parse_market_response


def test_parse_market_response_active_yes_no():
    raw = {
        "slug": "will-trump-win-2028",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.42", "0.58"],
        "volume24hr": "125000",
        "active": True,
        "closed": False,
    }
    out = parse_market_response(raw)
    assert out is not None
    assert out["slug"] == "will-trump-win-2028"
    assert abs(out["yes_price"] - 0.42) < 1e-6
    assert abs(out["no_price"] - 0.58) < 1e-6
    assert out["volume_24h"] == 125000.0


def test_parse_market_response_rejects_closed():
    raw = {
        "slug": "x", "outcomes": ["Yes", "No"], "outcomePrices": ["0.5", "0.5"],
        "active": True, "closed": True,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_rejects_inactive():
    raw = {
        "slug": "x", "outcomes": ["Yes", "No"], "outcomePrices": ["0.5", "0.5"],
        "active": False, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_decodes_string_encoded_fields():
    """Real gamma-api returns outcomes + outcomePrices as JSON-encoded strings."""
    raw = {
        "slug": "trump-2028",
        "outcomes": '["Yes", "No"]',          # JSON-encoded string
        "outcomePrices": '["0.42", "0.58"]',  # JSON-encoded string
        "volume24hr": "100000",
        "active": True, "closed": False,
    }
    out = parse_market_response(raw)
    assert out is not None
    assert abs(out["yes_price"] - 0.42) < 1e-6
    assert abs(out["no_price"] - 0.58) < 1e-6


def test_parse_market_response_rejects_multi_outcome():
    """B1 v0 only handles binary Yes/No markets."""
    raw = {
        "slug": "x",
        "outcomes": ["Trump", "Biden", "Other"],
        "outcomePrices": ["0.40", "0.45", "0.15"],
        "active": True, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_parse_market_response_rejects_malformed_prices():
    raw = {
        "slug": "x",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["not_a_number", "0.5"],
        "active": True, "closed": False,
    }
    assert parse_market_response(raw) is None


def test_fetch_single_returns_market_on_http_200():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "slug": "test", "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.42", "0.58"], "volume24hr": "100",
        "active": True, "closed": False,
    }
    with patch("core.polymarket_scraper.httpx.get", return_value=fake_response):
        scraper = PolymarketScraper()
        out = scraper.fetch_single("test")
        assert out is not None
        assert out["slug"] == "test"


def test_fetch_single_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch("core.polymarket_scraper.httpx.get", return_value=fake_response):
        scraper = PolymarketScraper()
        assert scraper.fetch_single("test") is None


def test_fetch_batch_returns_dict_keyed_by_slug():
    """fetch_batch issues one ?slug= call per entry — the plural form is
    broken on Polymarket's side. Each response is a list of length 1
    (gamma-api wraps singular slug result in a list)."""
    def per_slug(url, timeout):
        slug = url.split("slug=")[1]
        fixtures = {
            "a": [{"slug": "a", "outcomes": ["Yes", "No"], "outcomePrices": ["0.3", "0.7"],
                   "volume24hr": "100", "active": True, "closed": False}],
            "b": [{"slug": "b", "outcomes": ["Yes", "No"], "outcomePrices": ["0.6", "0.4"],
                   "volume24hr": "200", "active": True, "closed": False}],
            "c-closed": [{"slug": "c-closed", "outcomes": ["Yes", "No"],
                          "outcomePrices": ["0.5", "0.5"], "volume24hr": "50",
                          "active": True, "closed": True}],
        }
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = fixtures.get(slug, [])
        return m

    with patch("core.polymarket_scraper.httpx.get", side_effect=per_slug):
        scraper = PolymarketScraper()
        out = scraper.fetch_batch(["a", "b", "c-closed"])
        assert "a" in out
        assert "b" in out
        assert "c-closed" not in out  # closed market filtered
        assert out["a"]["yes_price"] == 0.3


def test_fetch_batch_drops_response_with_different_slug():
    """Defensive: if a per-slug call returns a market whose slug doesn't
    match the request (e.g., gamma-api fell back to a popular market),
    drop it instead of caching under the wrong key."""
    def per_slug(url, timeout):
        m = MagicMock()
        m.status_code = 200
        # gamma-api returned an unrelated market regardless of what we asked
        m.json.return_value = [{"slug": "unrelated-popular-market",
                                "outcomes": ["Yes", "No"],
                                "outcomePrices": ["0.5", "0.5"],
                                "volume24hr": "999999",
                                "active": True, "closed": False}]
        return m

    with patch("core.polymarket_scraper.httpx.get", side_effect=per_slug):
        scraper = PolymarketScraper()
        out = scraper.fetch_batch(["a-requested"])
        assert out == {}
