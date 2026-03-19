import pytest
from core.market_scanner import MarketScanner


def test_cache_and_get_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    markets = [
        {"conditionId": "m1", "question": "BTC up 15 min?",
         "tokens": [{"outcome": "Yes", "token_id": "t1"}, {"outcome": "No", "token_id": "t2"}]},
    ]
    scanner.update_markets(markets)
    assert len(scanner.get_active_markets()) == 1
    assert scanner.get_market("m1")["conditionId"] == "m1"


def test_capital_lock():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    assert scanner.is_locked("m1") is False
    scanner.lock_market("m1")
    assert scanner.is_locked("m1") is True
    scanner.unlock_market("m1")
    assert scanner.is_locked("m1") is False


def test_get_unlocked_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {"m1": {"conditionId": "m1"}, "m2": {"conditionId": "m2"}}
    scanner._locked_markets = {"m1"}
    unlocked = scanner.get_unlocked_markets()
    assert len(unlocked) == 1
    assert unlocked[0]["conditionId"] == "m2"
