"""Tests for Kalshi ticker → odds API matching with date disambiguation.

Covers the 2026-04-21 fix: when the same matchup (e.g. NHL playoff series
COL@LA games 1-7) returns multiple commence_times from the Odds API, the
matcher now uses the Kalshi ticker's scheduled datetime to pick the
nearest game rather than always returning the first one.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from strategies.sports_trader import (
    SportsTraderStrategy,
    _parse_kalshi_ticker_datetime,
)


def _make_strategy() -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    return SportsTraderStrategy(config, MagicMock(), MagicMock())


# ---------- _parse_kalshi_ticker_datetime ----------

def test_parse_date_only_ticker():
    """NBA/NHL tickers without HHMM anchor at noon UTC."""
    dt = _parse_kalshi_ticker_datetime("KXNBAGAME-26APR21BOSBUF-BOS")
    assert dt == datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def test_parse_date_time_ticker():
    """MLB tickers with HHMM use the embedded time."""
    dt = _parse_kalshi_ticker_datetime("KXMLBGAME-26APR221940PHICHC-CHC")
    assert dt == datetime(2026, 4, 22, 19, 40, tzinfo=timezone.utc)


def test_parse_invalid_ticker_returns_none():
    assert _parse_kalshi_ticker_datetime("not-a-kalshi-ticker") is None
    assert _parse_kalshi_ticker_datetime("") is None
    assert _parse_kalshi_ticker_datetime(None) is None


def test_parse_invalid_month_returns_none():
    assert _parse_kalshi_ticker_datetime("KXNBAGAME-26ZZZ21BOSBUF-BOS") is None


# ---------- _match_kalshi_to_odds (single match) ----------

def test_single_team_match_returns_that_game():
    strat = _make_strategy()
    games = [
        {"home_team": "Boston Celtics", "away_team": "Phoenix Suns",
         "commence_time": "2026-04-21T23:00:00Z"},
    ]
    result = strat._match_kalshi_to_odds("Phoenix at Boston", games)
    assert result is games[0]


def test_no_match_returns_none():
    strat = _make_strategy()
    games = [
        {"home_team": "Boston Celtics", "away_team": "Phoenix Suns",
         "commence_time": "2026-04-21T23:00:00Z"},
    ]
    assert strat._match_kalshi_to_odds("Detroit at Orlando", games) is None


# ---------- _match_kalshi_to_odds (multi-match disambiguation) ----------

def test_multi_match_picks_nearest_to_ticker_date():
    """When the Odds API returns multiple games for the same matchup
    (playoff series), the ticker's scheduled date should win."""
    strat = _make_strategy()
    games = [
        # Game 1, played April 19
        {"home_team": "Los Angeles Kings", "away_team": "Colorado Avalanche",
         "commence_time": "2026-04-19T01:00:00Z"},
        # Game 2, scheduled April 22
        {"home_team": "Los Angeles Kings", "away_team": "Colorado Avalanche",
         "commence_time": "2026-04-22T02:00:00Z"},
        # Game 3, scheduled April 26
        {"home_team": "Los Angeles Kings", "away_team": "Colorado Avalanche",
         "commence_time": "2026-04-26T03:00:00Z"},
    ]
    # Ticker for April 22 game → should pick games[1]
    chosen = strat._match_kalshi_to_odds(
        "Colorado at Los Angeles", games,
        kalshi_ticker="KXNHLGAME-26APR22COLLA-LA",
    )
    assert chosen is games[1]
    # Ticker for April 26 game → should pick games[2]
    chosen = strat._match_kalshi_to_odds(
        "Colorado at Los Angeles", games,
        kalshi_ticker="KXNHLGAME-26APR26COLLA-LA",
    )
    assert chosen is games[2]


def test_multi_match_no_ticker_falls_back_to_first():
    """Legacy behaviour preserved when ticker not provided (e.g. tests
    that call _match_kalshi_to_odds without the new arg)."""
    strat = _make_strategy()
    games = [
        {"home_team": "LA Kings", "away_team": "Colorado Avalanche",
         "commence_time": "2026-04-19T01:00:00Z"},
        {"home_team": "LA Kings", "away_team": "Colorado Avalanche",
         "commence_time": "2026-04-22T02:00:00Z"},
    ]
    chosen = strat._match_kalshi_to_odds("Colorado at LA", games)
    assert chosen is games[0]  # first match wins (legacy)


def test_multi_match_unparseable_ticker_falls_back_to_first():
    strat = _make_strategy()
    games = [
        {"home_team": "Phoenix Suns", "away_team": "Boston Celtics",
         "commence_time": "2026-04-19T01:00:00Z"},
        {"home_team": "Phoenix Suns", "away_team": "Boston Celtics",
         "commence_time": "2026-04-22T02:00:00Z"},
    ]
    chosen = strat._match_kalshi_to_odds(
        "Boston at Phoenix", games, kalshi_ticker="GARBAGE",
    )
    assert chosen is games[0]
