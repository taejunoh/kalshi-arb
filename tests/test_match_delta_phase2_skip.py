"""Phase 2: the match-delta cap now REJECTS instead of only logging.

Phase 1 measured 939,610 observations over 6 days. The bug's mass sits in the
20-48h band (an adjacent-day game in a series) and the sign is decisive: MLB
99.7% / NPB 100% / WNBA 100% / KBO 97.7% matched an EARLIER game — the Odds API
had not published tomorrow's game yet. ATP/WTA invert it (99.7% LATER: the
finished match drops off the feed, leaving only the next round). Same root
cause, opposite sign, so the gate keys on |delta|.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.sports_trader import (  # noqa: E402
    _MATCH_MAX_DELTA_S_DATE_ONLY, _MATCH_MAX_DELTA_S_PRECISE,
    _match_individual_sport_to_odds, _ticker_scheduled_utc, enforce_match_cap,
)

CLOCK_TICKER = "KXMLBGAME-26JUL281840BALDET-DET"      # HHMM -> 6h cap
DATE_TICKER = "KXNBAGAME-26JUL28OKCPHX-OKC"           # date-only -> 20h cap


def _game(ticker: str, offset_h: float) -> dict:
    base = _ticker_scheduled_utc(ticker)
    assert base is not None
    return {"commence_time": (base + timedelta(hours=offset_h)).isoformat()}


# ── the gate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("offset_h", [0, 1.5, -1.5, 5.9, -5.9])
def test_matches_inside_the_precise_cap_pass(offset_h):
    g = _game(CLOCK_TICKER, offset_h)
    assert enforce_match_cap(CLOCK_TICKER, g) is g


@pytest.mark.parametrize("offset_h", [6.1, -6.1, 24, -24, 47, -47])
def test_matches_outside_the_precise_cap_are_rejected(offset_h):
    assert enforce_match_cap(CLOCK_TICKER, _game(CLOCK_TICKER, offset_h)) is None


def test_adjacent_day_earlier_game_is_rejected():
    """MLB's 124,568-observation failure mode: yesterday's game in the series."""
    assert enforce_match_cap(CLOCK_TICKER, _game(CLOCK_TICKER, -24)) is None


def test_next_round_later_match_is_rejected_too():
    """Tennis inverts the sign — the cap must be symmetric, not one-sided."""
    assert enforce_match_cap(DATE_TICKER, _game(DATE_TICKER, +26)) is None


def test_date_only_tickers_get_the_wider_cap():
    """19h is fine for a date-only ticker and fatal for a clocked one."""
    assert enforce_match_cap(DATE_TICKER, _game(DATE_TICKER, 19)) is not None
    assert enforce_match_cap(CLOCK_TICKER, _game(CLOCK_TICKER, 19)) is None


def test_the_two_caps_are_the_values_the_data_chose():
    """48h — the reverted April 2026 value — rejects 2.19% and sits just above
    the bug's mass. Guard against someone 'restoring' it."""
    assert _MATCH_MAX_DELTA_S_PRECISE == 6 * 3600
    assert _MATCH_MAX_DELTA_S_DATE_ONLY == 20 * 3600


# ── fail-open paths ───────────────────────────────────────────────────

def test_no_match_stays_no_match():
    assert enforce_match_cap(CLOCK_TICKER, None) is None


def test_unparseable_ticker_fails_open():
    """We skip matches we can prove are wrong, not ones we cannot check."""
    g = {"commence_time": "2026-07-28T18:40:00+00:00"}
    assert enforce_match_cap("NOT-A-TICKER", g) is g


def test_missing_commence_time_fails_open():
    g = {"home_team": "x"}
    assert enforce_match_cap(CLOCK_TICKER, g) is g


def test_absent_ticker_fails_open():
    g = {"commence_time": "2026-07-28T18:40:00+00:00"}
    assert enforce_match_cap(None, g) is g


# ── wired into the matcher, not just available ────────────────────────

def _tennis(offset_h: float) -> list[dict]:
    tk = "KXATPMATCH-26JUL28MAJPAU-PAU"
    base = _ticker_scheduled_utc(tk)
    return [{"home_team": "Pauline Major", "away_team": "Somebody Else",
             "commence_time": (base + timedelta(hours=offset_h)).isoformat()}]


def test_individual_matcher_applies_the_cap():
    tk = "KXATPMATCH-26JUL28MAJPAU-PAU"
    near = _match_individual_sport_to_odds("Pauline Major", _tennis(2), kalshi_ticker=tk)
    far = _match_individual_sport_to_odds("Pauline Major", _tennis(30), kalshi_ticker=tk)
    assert near is not None
    assert far is None


def test_individual_matcher_without_a_ticker_is_unchanged():
    """Legacy callers pass no ticker; there is nothing to check against."""
    out = _match_individual_sport_to_odds("Pauline Major", _tennis(30))
    assert out is not None


def test_team_matcher_applies_the_cap():
    strat = MagicMock()
    from strategies.sports_trader import SportsTraderStrategy

    tk = "KXMLBGAME-26JUL281840BALDET-DET"
    base = _ticker_scheduled_utc(tk)

    def games(offset_h):
        return [{"home_team": "Detroit Tigers", "away_team": "Baltimore Orioles",
                 "commence_time": (base + timedelta(hours=offset_h)).isoformat()}]

    match = SportsTraderStrategy._match_kalshi_to_odds
    assert match(strat, "Baltimore at Detroit", games(1), kalshi_ticker=tk) is not None
    assert match(strat, "Baltimore at Detroit", games(-24), kalshi_ticker=tk) is None


def test_negative_control_soccer_style_intraday_spread_survives():
    """MLS was 0.0% over cap across 250,425 observations — the gate must not
    start rejecting the sports it was never about."""
    tk = "KXMLSGAME-26AUG01MIACLB-CLB"
    base = _ticker_scheduled_utc(tk)
    strat = MagicMock()
    from strategies.sports_trader import SportsTraderStrategy

    for off in (-4, 0, 4):
        g = [{"home_team": "Columbus Crew", "away_team": "Inter Miami",
              "commence_time": (base + timedelta(hours=off)).isoformat()}]
        out = SportsTraderStrategy._match_kalshi_to_odds(
            strat, "Inter Miami at Columbus", g, kalshi_ticker=tk)
        assert out is not None, off
