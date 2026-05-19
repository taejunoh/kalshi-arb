"""_fetch_odds_multi — multi-tournament fan-out helper (2026-05-18).

Tennis series map to a tuple of concurrently-active Odds API endpoints.
_fetch_odds_multi pulls each, tags every game with `_tournament_slug`, and
returns a merged list. Both the live scan loop and the observer rely on
the per-game tag to look up TENNIS_SLUG_META.
"""
import asyncio

from strategies.sports_trader import SportsTraderStrategy


class _Stub(SportsTraderStrategy):
    """Minimal subclass with a fake _fetch_odds that just echoes the slug."""

    STRATEGY_NAME = "stub"

    def __init__(self, recipes):
        # bypass parent __init__
        self._fetched: list[str] = []
        self._recipes = recipes  # slug -> list[game dicts]

    async def _fetch_odds(self, sport):  # type: ignore[override]
        self._fetched.append(sport)
        return list(self._recipes.get(sport, []))


def test_single_str_slug_returns_tagged_games():
    s = _Stub({"baseball_mlb": [{"home_team": "A", "away_team": "B"}]})
    out = asyncio.run(s._fetch_odds_multi("baseball_mlb"))
    assert s._fetched == ["baseball_mlb"]
    assert len(out) == 1
    assert out[0]["_tournament_slug"] == "baseball_mlb"
    # Original dict not mutated (helper returns a copy)
    assert "_tournament_slug" not in s._recipes["baseball_mlb"][0]


def test_tuple_slug_fans_out_with_distinct_tags():
    s = _Stub({
        "tennis_atp_french_open": [{"home_team": "Alcaraz", "away_team": "Nadal"}],
        "tennis_atp_hamburg_open": [
            {"home_team": "Sinner", "away_team": "Medvedev"},
            {"home_team": "Zverev", "away_team": "Rublev"},
        ],
    })
    out = asyncio.run(s._fetch_odds_multi(
        ("tennis_atp_french_open", "tennis_atp_hamburg_open"),
    ))
    assert s._fetched == ["tennis_atp_french_open", "tennis_atp_hamburg_open"]
    assert len(out) == 3
    slugs_seen = {g["_tournament_slug"] for g in out}
    assert slugs_seen == {"tennis_atp_french_open", "tennis_atp_hamburg_open"}

    by_player = {g["home_team"]: g["_tournament_slug"] for g in out}
    assert by_player["Alcaraz"] == "tennis_atp_french_open"
    assert by_player["Sinner"] == "tennis_atp_hamburg_open"
    assert by_player["Zverev"] == "tennis_atp_hamburg_open"


def test_empty_tuple_yields_empty_list_no_fetch():
    s = _Stub({})
    out = asyncio.run(s._fetch_odds_multi(()))
    assert out == []
    assert s._fetched == []


def test_missing_slug_quietly_contributes_no_games():
    s = _Stub({"tennis_wta_french_open": [{"home_team": "Swiatek", "away_team": "Sabalenka"}]})
    out = asyncio.run(s._fetch_odds_multi(
        ("tennis_wta_french_open", "tennis_wta_strasbourg"),
    ))
    assert len(out) == 1
    assert out[0]["_tournament_slug"] == "tennis_wta_french_open"
