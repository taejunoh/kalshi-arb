"""Balance cache TTL + invalidation."""
import pytest
from unittest.mock import MagicMock
from core.kalshi_client import KalshiClient


def _mk():
    # Minimal client without real auth
    c = KalshiClient.__new__(KalshiClient)
    c._balance_cache_value = None
    c._balance_cache_ts = 0.0
    c._get = MagicMock()
    return c


def test_fresh_fetch_when_no_cache():
    c = _mk()
    c._get.return_value = {"balance": 50000}  # 500.00 in cents
    bal = c.get_balance()
    assert bal == 500.0
    c._get.assert_called_once()


def test_cached_when_within_ttl():
    c = _mk()
    c._get.return_value = {"balance": 50000}
    bal1 = c.get_balance(max_age_s=60)
    bal2 = c.get_balance(max_age_s=60)
    assert bal1 == bal2 == 500.0
    assert c._get.call_count == 1  # second call hit cache


def test_refetch_after_ttl_expires():
    import time
    c = _mk()
    c._get.return_value = {"balance": 50000}
    c.get_balance(max_age_s=0.01)
    time.sleep(0.02)
    c._get.return_value = {"balance": 60000}
    bal = c.get_balance(max_age_s=0.01)
    assert bal == 600.0
    assert c._get.call_count == 2


def test_max_age_zero_always_fresh():
    c = _mk()
    c._get.return_value = {"balance": 50000}
    c.get_balance()  # default max_age_s=0 -> always fresh
    c.get_balance()
    assert c._get.call_count == 2


def test_invalidate_forces_refetch():
    c = _mk()
    c._get.return_value = {"balance": 50000}
    c.get_balance(max_age_s=60)
    c.invalidate_balance_cache()
    c.get_balance(max_age_s=60)
    assert c._get.call_count == 2


def test_failed_fetch_returns_none_no_cache():
    c = _mk()
    c._get.side_effect = Exception("network")
    bal = c.get_balance(max_age_s=60)
    assert bal is None
    # No cache populated
    assert c._balance_cache_value is None
