"""MLB + NHL starter fetcher — added 2026-05-12."""
from unittest.mock import patch, MagicMock
import pytest

from core import starter_fetcher
from core.starter_fetcher import get_starters, invalidate_cache


def setup_function():
    invalidate_cache()


def test_unknown_sport_returns_none_pair():
    h, a = get_starters("NBA", "2026-05-12", "Lakers", "Celtics")
    assert h is None and a is None


def test_mlb_returns_starters_from_api(monkeypatch):
    """Mock MLB Stats API; verify parsing of probablePitcher."""
    mock_resp = MagicMock()
    mock_resp.json = lambda: {
        "dates": [{
            "games": [{
                "teams": {
                    "home": {
                        "team": {"name": "New York Yankees"},
                        "probablePitcher": {"fullName": "Gerrit Cole"},
                    },
                    "away": {
                        "team": {"name": "Boston Red Sox"},
                        "probablePitcher": {"fullName": "Brayan Bello"},
                    },
                },
            }],
        }],
    }
    mock_resp.raise_for_status = lambda: None
    with patch("core.starter_fetcher.httpx.get", return_value=mock_resp):
        h, a = get_starters("MLB", "2026-05-12", "New York Yankees", "Boston Red Sox")
    assert h == "Gerrit Cole"
    assert a == "Brayan Bello"


def test_mlb_tbd_pitcher_returned_as_none(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json = lambda: {
        "dates": [{
            "games": [{
                "teams": {
                    "home": {
                        "team": {"name": "Yankees"},
                        "probablePitcher": {"fullName": "TBD"},
                    },
                    "away": {
                        "team": {"name": "Red Sox"},
                        "probablePitcher": {"fullName": "Will Warren"},
                    },
                },
            }],
        }],
    }
    mock_resp.raise_for_status = lambda: None
    with patch("core.starter_fetcher.httpx.get", return_value=mock_resp):
        h, a = get_starters("MLB", "2026-05-12", "Yankees", "Red Sox")
    assert h is None  # TBD → None
    assert a == "Will Warren"


def test_mlb_api_error_returns_none_pair(monkeypatch):
    """API failure should be graceful — return (None, None) not raise."""
    with patch("core.starter_fetcher.httpx.get", side_effect=Exception("network")):
        h, a = get_starters("MLB", "2026-05-12", "Yankees", "Red Sox")
    assert h is None and a is None


def test_mlb_unknown_matchup_returns_none(monkeypatch):
    """Team pair not in API response → (None, None)."""
    mock_resp = MagicMock()
    mock_resp.json = lambda: {"dates": []}
    mock_resp.raise_for_status = lambda: None
    with patch("core.starter_fetcher.httpx.get", return_value=mock_resp):
        h, a = get_starters("MLB", "2026-05-12", "Yankees", "Red Sox")
    assert h is None and a is None


def test_mlb_case_insensitive_team_match(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json = lambda: {
        "dates": [{
            "games": [{
                "teams": {
                    "home": {"team": {"name": "NEW YORK YANKEES"},
                              "probablePitcher": {"fullName": "Cole"}},
                    "away": {"team": {"name": "Boston Red Sox"},
                              "probablePitcher": {"fullName": "Bello"}},
                },
            }],
        }],
    }
    mock_resp.raise_for_status = lambda: None
    with patch("core.starter_fetcher.httpx.get", return_value=mock_resp):
        h, a = get_starters("MLB", "2026-05-12", "new york yankees", "boston red sox")
    assert h == "Cole" and a == "Bello"


def test_nhl_returns_goalies_from_landing(monkeypatch):
    """Mock NHL schedule + landing; verify goalie extraction."""
    sched_resp = MagicMock()
    sched_resp.raise_for_status = lambda: None
    sched_resp.json = lambda: {
        "gameWeek": [{
            "date": "2026-05-12",
            "games": [{
                "id": 2025030001,
                "homeTeam": {
                    "placeName": {"default": "Montréal"},
                    "commonName": {"default": "Canadiens"},
                },
                "awayTeam": {
                    "placeName": {"default": "Buffalo"},
                    "commonName": {"default": "Sabres"},
                },
            }],
        }],
    }
    landing_resp = MagicMock()
    landing_resp.status_code = 200
    landing_resp.json = lambda: {
        "matchup": {
            "goalieComparison": {
                "homeTeam": {"leaders": [{
                    "firstName": {"default": "Jakub"},
                    "lastName": {"default": "Dobes"},
                }]},
                "awayTeam": {"leaders": [{
                    "firstName": {"default": "Ukko-Pekka"},
                    "lastName": {"default": "Luukkonen"},
                }]},
            },
        },
    }
    calls = []
    def fake_get(url, *args, **kwargs):
        calls.append(url)
        if "schedule" in url:
            return sched_resp
        return landing_resp
    with patch("core.starter_fetcher.httpx.get", side_effect=fake_get):
        h, a = get_starters(
            "NHL", "2026-05-12", "Montréal Canadiens", "Buffalo Sabres",
        )
    assert h == "Jakub Dobes"
    assert a == "Ukko-Pekka Luukkonen"


def test_reversed_home_away_lookup_swaps_result(monkeypatch):
    """NHL playoff: Odds API may have home=Wild, away=Avalanche, but the
    NHL Stats API for the same date has home=Avalanche, away=Wild (actual
    venue). get_starters must still return (wild_starter, avalanche_starter)
    by the caller's convention."""
    sched_resp = MagicMock()
    sched_resp.raise_for_status = lambda: None
    sched_resp.json = lambda: {
        "gameWeek": [{
            "date": "2026-05-13",
            "games": [{
                "id": 2025030235,
                "homeTeam": {"placeName": {"default": "Colorado"},
                             "commonName": {"default": "Avalanche"}},
                "awayTeam": {"placeName": {"default": "Minnesota"},
                             "commonName": {"default": "Wild"}},
            }],
        }],
    }
    landing_resp = MagicMock()
    landing_resp.status_code = 200
    landing_resp.json = lambda: {
        "matchup": {
            "goalieComparison": {
                "homeTeam": {"leaders": [{
                    "firstName": {"default": "Scott"},
                    "lastName": {"default": "Wedgewood"},
                }]},
                "awayTeam": {"leaders": [{
                    "firstName": {"default": "Jesper"},
                    "lastName": {"default": "Wallstedt"},
                }]},
            },
        },
    }
    def fake_get(url, *a, **k):
        return sched_resp if "schedule" in url else landing_resp
    with patch("core.starter_fetcher.httpx.get", side_effect=fake_get):
        # Caller (Odds API): home=Wild, away=Avalanche (opposite of NHL API)
        h, a = get_starters(
            "NHL", "2026-05-13", "Minnesota Wild", "Colorado Avalanche",
        )
    # Caller's home (Wild) is NHL API's away → should get away-side
    # starter (Wallstedt). Caller's away should get home-side (Wedgewood).
    assert h == "Jesper Wallstedt"
    assert a == "Scott Wedgewood"


def test_nhl_empty_leaders_returns_none(monkeypatch):
    sched_resp = MagicMock()
    sched_resp.raise_for_status = lambda: None
    sched_resp.json = lambda: {
        "gameWeek": [{
            "date": "2026-05-12",
            "games": [{
                "id": 2025030001,
                "homeTeam": {"placeName": {"default": "Montréal"},
                             "commonName": {"default": "Canadiens"}},
                "awayTeam": {"placeName": {"default": "Buffalo"},
                             "commonName": {"default": "Sabres"}},
            }],
        }],
    }
    landing_resp = MagicMock()
    landing_resp.status_code = 200
    landing_resp.json = lambda: {"matchup": {"goalieComparison": {
        "homeTeam": {"leaders": []}, "awayTeam": {"leaders": []},
    }}}
    def fake_get(url, *args, **kwargs):
        return sched_resp if "schedule" in url else landing_resp
    with patch("core.starter_fetcher.httpx.get", side_effect=fake_get):
        h, a = get_starters("NHL", "2026-05-12", "Montréal Canadiens", "Buffalo Sabres")
    assert h is None and a is None


def test_cache_hits_avoid_duplicate_api_calls(monkeypatch):
    """Same date+sport requested twice → single API call."""
    mock_resp = MagicMock()
    mock_resp.json = lambda: {"dates": [{"games": [{
        "teams": {
            "home": {"team": {"name": "Yankees"}, "probablePitcher": {"fullName": "Cole"}},
            "away": {"team": {"name": "Red Sox"}, "probablePitcher": {"fullName": "Bello"}},
        }
    }]}]}
    mock_resp.raise_for_status = lambda: None
    calls = []
    def fake_get(*a, **k):
        calls.append(1)
        return mock_resp
    with patch("core.starter_fetcher.httpx.get", side_effect=fake_get):
        get_starters("MLB", "2026-05-12", "Yankees", "Red Sox")
        get_starters("MLB", "2026-05-12", "Yankees", "Red Sox")
    assert len(calls) == 1
