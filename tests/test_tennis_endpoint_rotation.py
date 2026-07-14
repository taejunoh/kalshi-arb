"""Tennis endpoint rotation cron — added 2026-05-11."""
import logging
from unittest.mock import MagicMock, patch
import pytest

from scripts.tennis_endpoint_rotation import (
    fetch_active_endpoints,
    detect_stale_endpoints,
    mapped_tennis_slugs,
    unmapped_active_tennis,
    _redact_api_key,
    _RedactApiKeyFilter,
)


# --- API-key redaction in logs (2026-07-13) --------------------------------
# httpx logs the full request URL at INFO ("HTTP Request: GET
# https://...?apiKey=XXX ...") and embeds it in exception strings, so without
# scrubbing, the Odds API key lands in journald/stdout.

_SECRET = "80c1c0063d19f8c75bd60b611bfa48d6"


def test_redact_masks_apikey_in_url():
    line = (f'HTTP Request: GET https://api.the-odds-api.com/v4/sports'
            f'?apiKey={_SECRET}&all=true "HTTP/1.1 200 OK"')
    out = _redact_api_key(line)
    assert _SECRET not in out
    assert "apiKey=***" in out
    assert "all=true" in out          # rest of the URL is preserved


def test_redact_masks_apikey_at_url_end():
    """The /events endpoint puts apiKey last (no trailing &)."""
    out = _redact_api_key(f"events_count failed: GET .../events?apiKey={_SECRET}")
    assert _SECRET not in out
    assert out.endswith("apiKey=***")


def test_redact_leaves_key_free_text_untouched():
    assert _redact_api_key("No stale tennis endpoints.") == "No stale tennis endpoints."


def test_filter_redacts_httpx_style_record():
    """httpx logs via msg+args (url is in args) — the filter must format then
    scrub so the key never reaches the handler's output."""
    filt = _RedactApiKeyFilter()
    rec = logging.LogRecord(
        name="httpx", level=logging.INFO, pathname=__file__, lineno=1,
        msg='HTTP Request: %s "%s"',
        args=(f"GET https://api.the-odds-api.com/v4/sports?apiKey={_SECRET}&all=true",
              "HTTP/1.1 200 OK"),
        exc_info=None,
    )
    assert filt.filter(rec) is True
    formatted = rec.getMessage()
    assert _SECRET not in formatted
    assert "apiKey=***" in formatted


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


def test_mapped_tennis_slugs_collects_only_tennis_series():
    sport_map = {
        "KXATPMATCH": (),  # empty mid-rotation (2026-06-08 clay→grass)
        "KXWTAMATCH": ("tennis_wta_queens_club_champ",),
        "KXMLBGAME": "baseball_mlb",  # non-tennis — must be ignored
    }
    assert mapped_tennis_slugs(sport_map) == {"tennis_wta_queens_club_champ"}


def test_unmapped_active_tennis_flags_new_endpoint():
    """An active Odds API tennis endpoint not present in SPORT_MAP is surfaced
    so it can be wired up (the empty-ATP / clay→grass re-discovery path).
    Inactive endpoints, already-mapped slugs, and non-tennis sports are all
    excluded."""
    active = [
        {"key": "tennis_wta_queens_club_champ", "active": True, "title": "WTA Queen's"},
        {"key": "tennis_atp_halle", "active": True, "title": "ATP Halle"},
        {"key": "tennis_atp_french_open", "active": False, "title": "ATP French Open"},
        {"key": "soccer_epl", "active": True, "title": "EPL"},
    ]
    sport_map = {
        "KXWTAMATCH": ("tennis_wta_queens_club_champ",),
        "KXATPMATCH": (),
    }
    out = unmapped_active_tennis(active, sport_map)
    assert [e["key"] for e in out] == ["tennis_atp_halle"]


def test_unmapped_active_tennis_empty_when_all_active_mapped():
    active = [{"key": "tennis_wta_queens_club_champ", "active": True, "title": "Q"}]
    sport_map = {"KXWTAMATCH": ("tennis_wta_queens_club_champ",), "KXATPMATCH": ()}
    assert unmapped_active_tennis(active, sport_map) == []


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
