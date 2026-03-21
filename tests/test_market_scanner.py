import pytest
from core.market_scanner import MarketScanner


def test_cache_and_get_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    markets = [
        {"ticker": "KXBTC-25MAR20-T70000", "status": "open"},
    ]
    scanner.update_markets(markets)
    assert len(scanner.get_active_markets()) == 1
    assert scanner.get_market("KXBTC-25MAR20-T70000")["ticker"] == "KXBTC-25MAR20-T70000"


def test_capital_lock():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {}
    scanner._locked_markets = set()
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is False
    scanner.lock_market("KXBTC-25MAR20-T70000")
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is True
    scanner.unlock_market("KXBTC-25MAR20-T70000")
    assert scanner.is_locked("KXBTC-25MAR20-T70000") is False


def test_get_unlocked_markets():
    scanner = MarketScanner.__new__(MarketScanner)
    scanner._markets = {
        "KXBTC-25MAR20-T70000": {"ticker": "KXBTC-25MAR20-T70000"},
        "KXETH-25MAR20-T4000": {"ticker": "KXETH-25MAR20-T4000"},
    }
    scanner._locked_markets = {"KXBTC-25MAR20-T70000"}
    unlocked = scanner.get_unlocked_markets()
    assert len(unlocked) == 1
    assert unlocked[0]["ticker"] == "KXETH-25MAR20-T4000"
