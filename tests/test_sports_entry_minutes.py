from datetime import datetime, timedelta, timezone
import pytest
from strategies.sports_trader import _commence_time_to_minutes


def test_future_commence_time_returns_positive(monkeypatch):
    fixed_now = datetime(2026, 4, 14, 18, 0, 0, tzinfo=timezone.utc)
    _patch_now(monkeypatch, fixed_now)
    ct = (fixed_now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _commence_time_to_minutes(ct) == pytest.approx(30.0, abs=0.01)


def test_past_commence_time_returns_negative(monkeypatch):
    fixed_now = datetime(2026, 4, 14, 18, 0, 0, tzinfo=timezone.utc)
    _patch_now(monkeypatch, fixed_now)
    ct = (fixed_now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _commence_time_to_minutes(ct) == pytest.approx(-15.0, abs=0.01)


def test_missing_commence_time_returns_none():
    assert _commence_time_to_minutes(None) is None
    assert _commence_time_to_minutes("") is None


def test_malformed_commence_time_returns_none():
    assert _commence_time_to_minutes("not-a-date") is None
    assert _commence_time_to_minutes("2026-13-99T99:99:99Z") is None


def _patch_now(monkeypatch, fixed):
    import strategies.sports_trader as mod
    monkeypatch.setattr(mod, "_now_utc", lambda: fixed)
