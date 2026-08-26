"""Kalshi's 2026-08-22 title change: "A vs B Winner?" -> "<Team> wins".

Team sports match by finding BOTH team names in the title, so all of them
broke at once — MLB, NBA, WNBA, NHL, NPB and KBO went to zero
odds_observations for four days while their markets stayed fully liquid.
Tennis/UFC match on yes_sub_title and were untouched.

The fix rebuilds the pair from the two sides of the event and keeps the
two-team requirement, which is the property that guards against the
wrong-game contamination of 2026-07-22. Every name pair below was captured
from live Kalshi + Odds API responses on 2026-08-26.
"""
from unittest.mock import MagicMock

from strategies.sports_trader import (
    SportsTraderStrategy, build_opponent_sub_titles, _same_team,
)


def _strategy():
    s = SportsTraderStrategy.__new__(SportsTraderStrategy)
    s.config = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07)
    return s


def _mkt(event, ticker, sub):
    return {"event_ticker": event, "ticker": ticker, "yes_sub_title": sub}


def _game(home, away, commence):
    return {"home_team": home, "away_team": away, "commence_time": commence}


# ── name equivalence ──────────────────────────────────────────────────────

def test_kalshi_shorter_than_odds():
    assert _same_team("Minnesota Twins", "Minnesota")
    assert _same_team("St. Louis Cardinals", "St. Louis")


def test_kalshi_longer_than_odds():
    """NPB/KBO invert it — the Odds API is the terse side there."""
    assert _same_team("Chiba Lotte", "chiba lotte marines")
    assert _same_team("SSG", "ssg landers")
    assert _same_team("KT", "kt wiz")


def test_alias_table_covers_the_irregulars():
    assert _same_team("Chicago White Sox", "Chicago WS")
    assert _same_team("Los Angeles Dodgers", "Los Angeles D")


def test_same_city_different_team_is_rejected():
    """The substring matching this replaces would have said yes."""
    assert not _same_team("Chicago Cubs", "Chicago WS")
    assert not _same_team("Chicago White Sox", "Chicago C")
    assert not _same_team("New York Yankees", "New York M")


def test_punctuation_is_ignored():
    assert _same_team("St Louis Cardinals", "St. Louis")


def test_the_two_names_that_share_no_token_need_an_alias():
    """Of 107 team names across six leagues these are the only two the
    token rule cannot bridge, because the labels share no word at all."""
    assert _same_team("Athletics", "A's")
    assert _same_team("St. Louis City SC", "Saint Louis")


# ── opponent map ──────────────────────────────────────────────────────────

def test_opponent_map_pairs_the_two_sides():
    ms = [_mkt("E1", "E1-MIN", "Minnesota"), _mkt("E1", "E1-CWS", "Chicago WS")]
    assert build_opponent_sub_titles(ms) == {
        "E1-MIN": "Chicago WS", "E1-CWS": "Minnesota"}


def test_draw_market_is_excluded_but_the_two_sides_still_pair():
    """A three-way soccer event still has exactly two teams; only the draw
    market has no opponent to name."""
    ms = [_mkt("E2", "E2-CHE", "Chelsea"), _mkt("E2", "E2-ARS", "Arsenal"),
          _mkt("E2", "E2-TIE", "Tie")]
    assert build_opponent_sub_titles(ms) == {
        "E2-CHE": "Arsenal", "E2-ARS": "Chelsea"}


def test_lone_market_has_no_opponent():
    assert build_opponent_sub_titles([_mkt("E3", "E3-A", "A")]) == {}


# ── end to end ────────────────────────────────────────────────────────────

_TICKER = "KXMLBGAME-26AUG262105MINATH-MIN"


def test_new_title_format_matches_via_sub_titles():
    s = _strategy()
    games = [_game("Minnesota Twins", "Athletics", "2026-08-27T01:05:00Z")]
    assert s._match_kalshi_to_odds("Minnesota wins", games,
                                   kalshi_ticker=_TICKER) is None, \
        "title alone must not match — that is the outage"
    got = s._match_kalshi_to_odds(
        "Minnesota wins", games, kalshi_ticker=_TICKER,
        yes_sub_title="Minnesota", opponent_sub_title="A's")
    assert got is games[0]


def test_old_title_format_still_matches_unchanged():
    """Title matching runs first; the fallback must not alter it."""
    s = _strategy()
    games = [_game("Philadelphia Phillies", "St. Louis Cardinals",
                   "2026-08-27T01:05:00Z")]
    got = s._match_kalshi_to_odds(
        "St. Louis vs Philadelphia Winner?", games,
        kalshi_ticker="KXMLBGAME-26AUG262105STLPHI-STL")
    assert got is games[0]


def test_fallback_still_rejects_the_wrong_game_by_time():
    """The cap is what stopped 32% wrong-game contamination. A same-matchup
    game a day earlier must stay rejected on an HHMM ticker (6h cap)."""
    s = _strategy()
    yesterday = _game("Minnesota Twins", "Athletics", "2026-08-26T01:05:00Z")
    assert s._match_kalshi_to_odds(
        "Minnesota wins", [yesterday], kalshi_ticker=_TICKER,
        yes_sub_title="Minnesota", opponent_sub_title="A's") is None


def test_fallback_picks_the_nearest_of_a_series():
    s = _strategy()
    games = [_game("Minnesota Twins", "Athletics", "2026-08-26T01:05:00Z"),
             _game("Minnesota Twins", "Athletics", "2026-08-27T01:05:00Z")]
    got = s._match_kalshi_to_odds(
        "Minnesota wins", games, kalshi_ticker=_TICKER,
        yes_sub_title="Minnesota", opponent_sub_title="A's")
    assert got is games[1]


def test_one_team_alone_is_not_enough():
    """Without the opponent the fallback stays off — a single name could be
    either half of a doubleheader or the wrong city's team."""
    s = _strategy()
    games = [_game("Minnesota Twins", "Athletics", "2026-08-27T01:05:00Z")]
    assert s._match_kalshi_to_odds("Minnesota wins", games,
                                   kalshi_ticker=_TICKER,
                                   yes_sub_title="Minnesota") is None
