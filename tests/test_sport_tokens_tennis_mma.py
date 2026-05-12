"""Sport tokens for Tennis (ATP/WTA) + MMA (UFC) — added 2026-05-11."""
from strategies.sports_trader import (
    SPORTS_SERIES,
    SPORT_GAME_TOKENS,
    SPORT_SHORT_BY_TOKEN,
)


def test_kxatpmatch_in_sports_series():
    assert "KXATPMATCH" in SPORTS_SERIES


def test_kxwtamatch_in_sports_series():
    assert "KXWTAMATCH" in SPORTS_SERIES


def test_kxufcfight_in_sports_series():
    assert "KXUFCFIGHT" in SPORTS_SERIES


def test_atpmatch_in_sport_game_tokens():
    assert "ATPMATCH" in SPORT_GAME_TOKENS


def test_wtamatch_in_sport_game_tokens():
    assert "WTAMATCH" in SPORT_GAME_TOKENS


def test_ufcfight_in_sport_game_tokens():
    assert "UFCFIGHT" in SPORT_GAME_TOKENS


def test_atpmatch_short_is_atp():
    assert SPORT_SHORT_BY_TOKEN["ATPMATCH"] == "ATP"


def test_wtamatch_short_is_wta():
    assert SPORT_SHORT_BY_TOKEN["WTAMATCH"] == "WTA"


def test_ufcfight_short_is_ufc():
    assert SPORT_SHORT_BY_TOKEN["UFCFIGHT"] == "UFC"


def test_no_substring_collision_in_sport_game_tokens():
    """For first-match-wins extraction, no token may be a prefix substring
    of a later token. ATPMATCH/WTAMATCH/UFCFIGHT must not collide."""
    for i, ti in enumerate(SPORT_GAME_TOKENS):
        for tj in SPORT_GAME_TOKENS[i+1:]:
            # If ti is a substring of tj, tj must come first (already broken)
            assert ti not in tj, (
                f"Token order bug: '{ti}' at position {i} is substring of '{tj}' "
                f"at later position — first-match extraction would misclassify"
            )


from strategies.sports_trader import SportsTraderStrategy


def test_atp_is_observation_only():
    assert "ATP" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_wta_is_observation_only():
    assert "WTA" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_ufc_is_observation_only():
    assert "UFC" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS
