"""Individual-sport (Tennis/MMA) name matching — added 2026-05-11.

Tennis/MMA Kalshi markets have a structure incompatible with the
team-sport _match_kalshi_to_odds (city-prefix). Use yes_sub_title against
Odds API home_team/away_team directly.
"""
from strategies.sports_trader import _match_individual_sport_to_odds


def test_exact_full_name_match_atp():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("carlos alcaraz", odds_games)
    assert g is not None
    assert g["home_team"] == "Carlos Alcaraz"


def test_exact_full_name_match_wta():
    odds_games = [{
        "home_team": "Coco Gauff",
        "away_team": "Mirra Andreeva",
        "commence_time": "2026-05-12T13:00:00Z",
    }]
    g = _match_individual_sport_to_odds("mirra andreeva", odds_games)
    assert g is not None
    assert g["away_team"] == "Mirra Andreeva"


def test_token_overlap_with_extra_pinnacle_token():
    """Pinnacle sometimes carries a longer form. Kalshi's 'Carlos Alcaraz'
    must still match Pinnacle's 'Carlos Alcaraz Garfia'."""
    odds_games = [{
        "home_team": "Carlos Alcaraz Garfia",
        "away_team": "Daniil Medvedev",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("carlos alcaraz", odds_games)
    assert g is not None


def test_token_overlap_with_extra_kalshi_token():
    """Inverse: Kalshi carries a longer form like 'Thiago Agustin Tirante'.
    Pinnacle 'Tirante' should still match."""
    odds_games = [{
        "home_team": "Tirante",
        "away_team": "Daniil Medvedev",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("thiago agustin tirante", odds_games)
    assert g is not None


def test_no_match_returns_none():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("rafael nadal", odds_games)
    assert g is None


def test_empty_odds_list_returns_none():
    g = _match_individual_sport_to_odds("carlos alcaraz", [])
    assert g is None


def test_empty_yes_sub_title_returns_none():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("", odds_games)
    assert g is None


def test_ufc_fighter_match():
    odds_games = [{
        "home_team": "Jeremiah Wells",
        "away_team": "Nicolas Dalby",
        "commence_time": "2026-05-16T22:00:00Z",
    }]
    g = _match_individual_sport_to_odds("jeremiah wells", odds_games)
    assert g is not None
    assert g["home_team"] == "Jeremiah Wells"


def test_disambiguates_same_player_two_events_by_ticker_date():
    """When a player has matches on different dates, the kalshi_ticker date
    narrows to the right game."""
    odds_games = [
        {
            "home_team": "Carlos Alcaraz",
            "away_team": "Jannik Sinner",
            "commence_time": "2026-05-12T15:00:00Z",
        },
        {
            "home_team": "Carlos Alcaraz",
            "away_team": "Daniil Medvedev",
            "commence_time": "2026-05-14T15:00:00Z",
        },
    ]
    g = _match_individual_sport_to_odds(
        "carlos alcaraz", odds_games,
        kalshi_ticker="KXATPMATCH-26MAY14ALCMED-ALC",
    )
    assert g is not None
    assert g["away_team"] == "Daniil Medvedev"
