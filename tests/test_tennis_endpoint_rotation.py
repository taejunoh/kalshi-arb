"""Tennis endpoint rotation cron — added 2026-05-11."""
from unittest.mock import MagicMock, patch
import pytest

from scripts.tennis_endpoint_rotation import (
    fetch_active_endpoints,
    detect_stale_endpoints,
)


def test_detect_stale_endpoints_zero_events_two_days_triggers():
    """If any slug under a tennis series has zero events for >= 2 consecutive
    days, detect_stale_endpoints lists the series. Counter persists in a
    state file."""
    state = {"tennis_atp_italian_open": 2}  # 2 consecutive zero-event days
    sport_map = {"KXATPMATCH": ("tennis_atp_italian_open",)}
    current_counts = {"tennis_atp_italian_open": 0}  # zero again today
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" in stale
    # State for this endpoint is reset upon detection (avoids re-alert spam)
    assert new_state.get("tennis_atp_italian_open", 0) == 0


def test_detect_stale_endpoints_first_zero_day_does_not_trigger():
    state = {}
    sport_map = {"KXATPMATCH": ("tennis_atp_italian_open",)}
    current_counts = {"tennis_atp_italian_open": 0}
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" not in stale
    assert new_state["tennis_atp_italian_open"] == 1


def test_detect_stale_endpoints_nonzero_resets_counter():
    state = {"tennis_atp_italian_open": 1}
    sport_map = {"KXATPMATCH": ("tennis_atp_italian_open",)}
    current_counts = {"tennis_atp_italian_open": 15}
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" not in stale
    assert new_state.get("tennis_atp_italian_open", 0) == 0


def test_detect_stale_endpoints_partial_stale_in_multi_tournament():
    """When a series fans out across multiple tournaments, alert as soon as
    one slug trips the threshold even if siblings are still live."""
    state = {"tennis_atp_italian_open": 2}
    sport_map = {
        "KXATPMATCH": ("tennis_atp_italian_open", "tennis_atp_french_open"),
    }
    current_counts = {
        "tennis_atp_italian_open": 0,    # stale — tournament ended
        "tennis_atp_french_open": 12,    # active — Roland Garros running
    }
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" in stale
    assert new_state["tennis_atp_french_open"] == 0
    assert new_state["tennis_atp_italian_open"] == 0


def test_detect_stale_endpoints_ignores_non_tennis_series():
    """KXMLBGAME → baseball_mlb mapping should NOT be checked by this
    rotation cron (it's for tennis-only)."""
    state = {}
    sport_map = {"KXMLBGAME": "baseball_mlb"}
    current_counts = {"baseball_mlb": 0}
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXMLBGAME" not in stale
    assert "baseball_mlb" not in new_state


def test_fetch_active_endpoints_calls_odds_api():
    """fetch_active_endpoints hits /v4/sports?all=true and returns
    list of {key: ..., active: ..., title: ...}."""
    with patch("scripts.tennis_endpoint_rotation.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"key": "tennis_atp_french_open", "active": True, "title": "ATP French Open"},
                {"key": "tennis_atp_italian_open", "active": False, "title": "ATP Italian Open"},
            ],
        )
        # No raise_for_status needed since we mock 200
        mock_get.return_value.raise_for_status = MagicMock()
        result = fetch_active_endpoints("dummy_api_key")
        assert any(e["key"] == "tennis_atp_french_open" and e["active"] for e in result)
