import pytest
from core.polymarket_client import PolymarketClient


def test_filter_crypto_markets():
    client = PolymarketClient.__new__(PolymarketClient)
    markets = [
        {"question": "Will BTC go up in the next 15 minutes?", "conditionId": "1",
         "tokens": [{"outcome": "Yes", "token_id": "t1"}, {"outcome": "No", "token_id": "t2"}]},
        {"question": "Will Trump win?", "conditionId": "2",
         "tokens": [{"outcome": "Yes", "token_id": "t3"}, {"outcome": "No", "token_id": "t4"}]},
        {"question": "Will ETH go up in the next 5 minutes?", "conditionId": "3",
         "tokens": [{"outcome": "Yes", "token_id": "t5"}, {"outcome": "No", "token_id": "t6"}]},
    ]
    filtered = client._filter_crypto_markets(markets)
    assert len(filtered) == 2
    assert filtered[0]["conditionId"] == "1"
    assert filtered[1]["conditionId"] == "3"


def test_extract_asset():
    client = PolymarketClient.__new__(PolymarketClient)
    assert client._extract_asset({"question": "Will BTC go up?"}) == "BTC"
    assert client._extract_asset({"question": "Will ETH go down?"}) == "ETH"
    assert client._extract_asset({"question": "Will SOL go up?"}) == "SOL"
    assert client._extract_asset({"question": "Will Trump win?"}) is None


def test_get_token_ids():
    client = PolymarketClient.__new__(PolymarketClient)
    market = {
        "tokens": [
            {"outcome": "Yes", "token_id": "yes_token"},
            {"outcome": "No", "token_id": "no_token"},
        ]
    }
    yes_id, no_id = client.get_token_ids(market)
    assert yes_id == "yes_token"
    assert no_id == "no_token"
