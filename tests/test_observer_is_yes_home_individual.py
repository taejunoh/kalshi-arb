"""is_yes_home assignment for individual-sport markets — collision-safe.

For team sports, the existing code uses ODDS_TO_KALSHI_SHORT + city-prefix.
For individual sports (ATP/WTA/UFC), the observer scores yes_sub_title's
overlap with home_team and away_team and picks the better match. This
guards against the "Diaz vs Diaz" / "Williams vs Williams" collision case
where both fighter/player names share a token.
"""
from strategies.sports_scan_observer import (
    _name_overlap_score,
    _individual_yes_is_home,
)


def test_exact_match_scores_one():
    assert _name_overlap_score("carlos alcaraz", "carlos alcaraz") == 1.0


def test_no_overlap_scores_zero():
    assert _name_overlap_score("carlos alcaraz", "jannik sinner") == 0.0


def test_subset_match_scores_between_half_and_one():
    # "carlos alcaraz" (2 tok) is subset of "carlos alcaraz garfia" (3 tok)
    # score = 0.5 + 0.5 * (2/3) ≈ 0.833
    s = _name_overlap_score("carlos alcaraz", "carlos alcaraz garfia")
    assert 0.5 < s < 1.0
    assert abs(s - (0.5 + 0.5 * 2 / 3)) < 1e-6


def test_empty_input_scores_zero():
    assert _name_overlap_score("", "carlos alcaraz") == 0.0
    assert _name_overlap_score("carlos alcaraz", "") == 0.0


def test_yes_is_home_when_exact_match_to_home():
    assert _individual_yes_is_home(
        "carlos alcaraz", "carlos alcaraz", "jannik sinner"
    ) is True


def test_yes_is_away_when_exact_match_to_away():
    assert _individual_yes_is_home(
        "jannik sinner", "carlos alcaraz", "jannik sinner"
    ) is False


def test_subset_match_picks_correct_side():
    # yes_sub "alcaraz" subset of both? No — only of "carlos alcaraz".
    # Away "jannik sinner" has no overlap. → home wins.
    assert _individual_yes_is_home(
        "alcaraz", "carlos alcaraz", "jannik sinner"
    ) is True


def test_diaz_vs_diaz_collision_resolves_to_tighter_match():
    """Both fighters share 'Diaz' token. yes_sub 'nate diaz' must match
    away 'Nate Diaz' over home 'Nick Diaz' (exact match beats partial)."""
    home_lc = "nick diaz"
    away_lc = "nate diaz"
    # Full name match should resolve cleanly
    assert _individual_yes_is_home("nate diaz", home_lc, away_lc) is False
    assert _individual_yes_is_home("nick diaz", home_lc, away_lc) is True


def test_single_token_collision_prefers_home_on_tie():
    """Ambiguous single-token yes_sub 'diaz' matches both home and away
    via subset (with equal score 0.5 + 0.5 * 1/2 = 0.75 each). The
    fallback resolves to home (deterministic, mirrors existing behavior)."""
    assert _individual_yes_is_home("diaz", "nick diaz", "nate diaz") is True


def test_no_overlap_returns_none():
    """Defensive — if yes_sub doesn't match either side, return None.
    Should not happen in production because the matcher already picked
    this game."""
    assert _individual_yes_is_home(
        "rafael nadal", "carlos alcaraz", "jannik sinner"
    ) is None


def test_extended_pinnacle_name_still_resolves():
    """Pinnacle sometimes has longer form ('Carlos Alcaraz Garfia').
    Kalshi short form 'Carlos Alcaraz' should still match home cleanly."""
    assert _individual_yes_is_home(
        "carlos alcaraz", "carlos alcaraz garfia", "daniil medvedev"
    ) is True
