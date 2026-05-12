"""MLB + NHL starting pitcher/goalie fetcher with in-memory TTL cache.

Provides per-game starter lookups for the observer. Cache TTL ~10 min so
we don't hammer free public APIs each 60s scan cycle. Returns None when:
- API errors
- Game not found
- Probable pitcher/goalie 'TBD' or not announced

Game keys are (sport, date_str, home_team_name, away_team_name) — exact
match required. Team-name normalization is the caller's responsibility
(observer already has home_team/away_team from Odds API).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("starter_fetcher")

CACHE_TTL_SECONDS = 600   # 10 minutes — starters change infrequently

# (sport, date_str) -> {(home_team_lc, away_team_lc): (starter_home, starter_away)}
_CACHE: dict[tuple[str, str], dict[tuple[str, str], tuple[Optional[str], Optional[str]]]] = {}
_CACHE_TIMESTAMPS: dict[tuple[str, str], float] = {}


def _is_cache_valid(key: tuple[str, str]) -> bool:
    ts = _CACHE_TIMESTAMPS.get(key)
    return ts is not None and (time.time() - ts) < CACHE_TTL_SECONDS


def _fetch_mlb_starters(date_str: str) -> dict[tuple[str, str], tuple[Optional[str], Optional[str]]]:
    """Fetch all MLB games' probable pitchers for a given date.
    Returns {(home_team_lc, away_team_lc): (home_pitcher, away_pitcher)}.
    Pitchers may be None if not announced."""
    out: dict[tuple[str, str], tuple[Optional[str], Optional[str]]] = {}
    try:
        r = httpx.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": date_str,
                    "hydrate": "probablePitcher,team"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"MLB schedule fetch failed for {date_str}: {e}")
        return out

    for d in data.get("dates", []):
        for g in d.get("games", []):
            teams = g.get("teams", {}) or {}
            home_name = ((teams.get("home", {}) or {}).get("team", {}) or {}).get("name", "")
            away_name = ((teams.get("away", {}) or {}).get("team", {}) or {}).get("name", "")
            if not home_name or not away_name:
                continue
            home_pp = (teams.get("home", {}) or {}).get("probablePitcher") or {}
            away_pp = (teams.get("away", {}) or {}).get("probablePitcher") or {}
            home_name_p = home_pp.get("fullName") or None
            away_name_p = away_pp.get("fullName") or None
            if home_name_p == "TBD":
                home_name_p = None
            if away_name_p == "TBD":
                away_name_p = None
            out[(home_name.lower(), away_name.lower())] = (home_name_p, away_name_p)
    return out


def _fetch_nhl_starters(date_str: str) -> dict[tuple[str, str], tuple[Optional[str], Optional[str]]]:
    """Fetch all NHL games' projected goalies for a date.
    Returns {(home_team_lc, away_team_lc): (home_goalie, away_goalie)}."""
    out: dict[tuple[str, str], tuple[Optional[str], Optional[str]]] = {}
    try:
        r = httpx.get(
            f"https://api-web.nhle.com/v1/schedule/{date_str}",
            timeout=15,
        )
        r.raise_for_status()
        sched = r.json()
    except Exception as e:
        logger.warning(f"NHL schedule fetch failed for {date_str}: {e}")
        return out

    # Find the day's games
    game_weeks = sched.get("gameWeek", []) or []
    games = []
    for wk in game_weeks:
        if wk.get("date") == date_str:
            games = wk.get("games", []) or []
            break
    if not games and game_weeks:
        games = game_weeks[0].get("games", []) or []

    for g in games:
        game_id = g.get("id")
        home = g.get("homeTeam", {}) or {}
        away = g.get("awayTeam", {}) or {}
        home_place = (home.get("placeName", {}) or {}).get("default", "")
        home_common = (home.get("commonName", {}) or {}).get("default", "")
        away_place = (away.get("placeName", {}) or {}).get("default", "")
        away_common = (away.get("commonName", {}) or {}).get("default", "")
        home_name = f"{home_place} {home_common}".strip()
        away_name = f"{away_place} {away_common}".strip()
        if not home_name or not away_name or not game_id:
            continue
        # Fetch landing for projected goalie
        home_g = None
        away_g = None
        try:
            r2 = httpx.get(
                f"https://api-web.nhle.com/v1/gamecenter/{game_id}/landing",
                timeout=15,
            )
            if r2.status_code == 200:
                land = r2.json()
                gc = (land.get("matchup", {}) or {}).get("goalieComparison", {}) or {}
                for side, dst in (("homeTeam", "home"), ("awayTeam", "away")):
                    leaders = (gc.get(side, {}) or {}).get("leaders", []) or []
                    if not leaders:
                        continue
                    leader = leaders[0]
                    first = (leader.get("firstName", {}) or {}).get("default", "")
                    last = (leader.get("lastName", {}) or {}).get("default", "")
                    name = f"{first} {last}".strip()
                    if dst == "home":
                        home_g = name or None
                    else:
                        away_g = name or None
        except Exception as e:
            logger.warning(f"NHL gamecenter fetch failed for {game_id}: {e}")
        out[(home_name.lower(), away_name.lower())] = (home_g, away_g)
    return out


def get_starters(sport: str, date_str: str, home_team: str, away_team: str) -> tuple[Optional[str], Optional[str]]:
    """Return (starter_home, starter_away) for the requested game, or (None, None)
    if not found. Per-day cache with TTL CACHE_TTL_SECONDS.

    `sport` is "MLB" or "NHL". Other sports return (None, None).
    Team names are matched case-insensitively against the API's team-name fields.
    """
    if sport not in ("MLB", "NHL"):
        return (None, None)
    cache_key = (sport, date_str)
    if not _is_cache_valid(cache_key):
        if sport == "MLB":
            _CACHE[cache_key] = _fetch_mlb_starters(date_str)
        else:
            _CACHE[cache_key] = _fetch_nhl_starters(date_str)
        _CACHE_TIMESTAMPS[cache_key] = time.time()
    day_map = _CACHE.get(cache_key, {})
    return day_map.get((home_team.lower(), away_team.lower()), (None, None))


def invalidate_cache() -> None:
    """For tests."""
    _CACHE.clear()
    _CACHE_TIMESTAMPS.clear()
