"""Verify observer routes ATP/WTA/UFC markets through the individual-sport
matcher rather than the team-sport _match_kalshi_to_odds (which would never
match because tennis/MMA titles lack city names)."""


def test_atp_series_routes_to_individual_matcher():
    """The observer's `_matcher_for_series` returns the individual-sport
    matcher for KXATPMATCH (and team matcher for everything else)."""
    from strategies.sports_scan_observer import _matcher_for_series
    from strategies.sports_trader import _match_individual_sport_to_odds

    assert _matcher_for_series("KXATPMATCH") is _match_individual_sport_to_odds
    assert _matcher_for_series("KXWTAMATCH") is _match_individual_sport_to_odds
    assert _matcher_for_series("KXUFCFIGHT") is _match_individual_sport_to_odds


def test_team_sport_series_routes_to_team_matcher_sentinel():
    """For team-sport series the helper returns None — observer flow uses
    the instance method self._match_kalshi_to_odds."""
    from strategies.sports_scan_observer import _matcher_for_series

    assert _matcher_for_series("KXNBAGAME") is None
    assert _matcher_for_series("KXMLBGAME") is None
    assert _matcher_for_series("KXEPLGAME") is None
