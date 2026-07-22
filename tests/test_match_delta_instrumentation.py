"""Phase-1 instrumentation for the matched_game mismatch (2026-07-22).

Phase 1 measures the bug WITHOUT fixing it. The single most important
property of this change is therefore behaviour invariance: every ticker must
still be matched to exactly the same odds-API game as before, so that no
live strategy changes what it trades. The first section of this file is that
lock; the rest verifies the measurement itself.

All fixtures use fixed tickers and fixed commence_times and compare them to
each other — nothing here depends on `now`, so these tests cannot become
date bombs (see feedback_test_date_bomb_rolling_window).
"""
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

from core.clv_tracker import parse_game_start_utc
from strategies.sports_trader import (
    SportsTraderStrategy,
    _match_individual_sport_to_odds,
    _match_cap_seconds,
    _match_delta_seconds,
    _parse_kalshi_ticker_datetime,
    _ticker_has_clock,
    _ticker_scheduled_utc,
    measure_match_delta,
    _MATCH_DELTA_LOG_PREFIX,
    _MATCH_MAX_DELTA_S_DATE_ONLY,
    _MATCH_MAX_DELTA_S_PRECISE,
)

HOUR = 3600.0


def _make_strategy() -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    return SportsTraderStrategy(config, MagicMock(), MagicMock())


def _game(home: str, away: str, commence: str) -> dict:
    return {"home_team": home, "away_team": away, "commence_time": commence}


# Reconstruction of the real production incident, from EC2 odds_observations.
# The ticker for the Jul 4 DET@TEX game was matched to the Jul 2 game of the
# same series for 1,406 observations (162 of them labelled phase='live')
# because the Odds API had not published the Jul 4 game yet.
DETTEX_JUL02_GAME = _game("Texas Rangers", "Detroit Tigers",
                          "2026-07-03T00:07:00Z")   # Jul 2, 20:07 ET
DETTEX_JUL04_TICKER = "KXMLBGAME-26JUL041605DETTEX-TEX"   # Jul 4, 16:05 ET
DETTEX_JUL05_TICKER = "KXMLBGAME-26JUL051530DETTEX-TEX"   # Jul 5, 15:30 ET
DETTEX_TITLE = "Detroit at Texas"


# ---------------------------------------------------------------------------
# BEHAVIOUR INVARIANCE LOCK — phase 1 must not change any match outcome.
# ---------------------------------------------------------------------------

def test_lone_wrong_candidate_is_still_returned():
    """THE phase-1 safety property.

    This is the dominant production failure: only the (wrong) currently-live
    game is in the feed, so `len(candidates) == 1` short-circuits and returns
    it with no distance check. Phase 2 will return None here. Phase 1 must
    keep returning the wrong game, or live strategies change behaviour.
    """
    strat = _make_strategy()
    matched = strat._match_kalshi_to_odds(
        DETTEX_TITLE, [DETTEX_JUL02_GAME], kalshi_ticker=DETTEX_JUL04_TICKER,
    )
    assert matched is DETTEX_JUL02_GAME, (
        "phase 1 changed a match outcome — live trading behaviour is NOT "
        "invariant; this must stay broken until phase 2"
    )


def test_lone_wrong_candidate_still_returned_for_individual_sports():
    """Same lock for the tennis/MMA matcher (a duplicate of the same code)."""
    two_weeks_later = _game("Carlos Alcaraz", "Jannik Sinner",
                            "2026-06-01T13:00:00Z")
    matched = _match_individual_sport_to_odds(
        "Carlos Alcaraz", [two_weeks_later],
        kalshi_ticker="KXATPMATCH-26MAY18ALCSIN-ALC",
    )
    assert matched is two_weeks_later


def test_multi_candidate_selection_unchanged():
    """With several candidates the pick must still follow the OLD (buggy)
    reference time, i.e. `_parse_kalshi_ticker_datetime`, not the corrected
    one. Pinning it to an independent recomputation catches any accidental
    rewiring of the selection path onto the new helper.
    """
    strat = _make_strategy()
    games = [
        DETTEX_JUL02_GAME,
        _game("Texas Rangers", "Detroit Tigers", "2026-07-04T00:05:00Z"),
        _game("Texas Rangers", "Detroit Tigers", "2026-07-04T20:05:00Z"),
        _game("Texas Rangers", "Detroit Tigers", "2026-07-05T19:30:00Z"),
    ]
    legacy_ref = _parse_kalshi_ticker_datetime(DETTEX_JUL04_TICKER)
    expected = min(games, key=lambda g: abs(
        (datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
         - legacy_ref).total_seconds()))

    matched = strat._match_kalshi_to_odds(
        DETTEX_TITLE, games, kalshi_ticker=DETTEX_JUL04_TICKER,
    )
    assert matched is expected


def test_legacy_parser_is_untouched():
    """The old parser still returns its (wrong) UTC-stamped value. It feeds
    the selection path, so changing it would change matching."""
    assert _parse_kalshi_ticker_datetime("KXNBAGAME-26APR21BOSBUF-BOS") == \
        datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    assert _parse_kalshi_ticker_datetime(DETTEX_JUL04_TICKER) == \
        datetime(2026, 7, 4, 16, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Corrected reference time — must agree with core/clv_tracker.py
# ---------------------------------------------------------------------------

def test_corrected_parser_matches_clv_tracker_edt():
    """Summer (EDT, UTC-4). clv_tracker is the reference implementation."""
    for ticker in (DETTEX_JUL04_TICKER, "KXMLBGAME-26APR221940PHICHC-CHC"):
        assert _ticker_scheduled_utc(ticker) == parse_game_start_utc(ticker), ticker


def test_corrected_parser_matches_clv_tracker_est():
    """Winter (EST, UTC-5) — locks the DST branch, not just the offset."""
    ticker = "KXNHLGAME-26JAN151900BOSNYR-BOS"
    assert _ticker_scheduled_utc(ticker) == parse_game_start_utc(ticker)
    assert _ticker_scheduled_utc(ticker) == \
        datetime(2026, 1, 16, 0, 0, tzinfo=timezone.utc)


def test_corrected_parser_is_four_hours_later_than_legacy_in_summer():
    """Defect B, stated as an explicit offset so a regression is obvious."""
    delta = (_ticker_scheduled_utc(DETTEX_JUL04_TICKER)
             - _parse_kalshi_ticker_datetime(DETTEX_JUL04_TICKER))
    assert delta.total_seconds() == 4 * HOUR


def test_date_only_ticker_anchors_at_18_et():
    """Defect C: 18:00 ET (22:00 UTC in summer), not 12:00 UTC."""
    assert _ticker_scheduled_utc("KXNBAGAME-26APR21BOSBUF-BOS") == \
        datetime(2026, 4, 21, 22, 0, tzinfo=timezone.utc)


def test_corrected_parser_rejects_garbage():
    for bad in ("not-a-kalshi-ticker", "", None, "KXMLBGAME-26XXX041605DETTEX-TEX"):
        assert _ticker_scheduled_utc(bad) is None


def test_ticker_has_clock():
    assert _ticker_has_clock(DETTEX_JUL04_TICKER) is True
    assert _ticker_has_clock("KXNPBGAME-26MAY130500SAIFUK-SAI") is True
    assert _ticker_has_clock("KXNBAGAME-26APR21BOSBUF-BOS") is False
    assert _ticker_has_clock("KXATPMATCH-26MAY18CUEDEL-CUE") is False
    assert _ticker_has_clock("") is False


def test_cap_depends_on_ticker_precision():
    assert _match_cap_seconds(DETTEX_JUL04_TICKER) == _MATCH_MAX_DELTA_S_PRECISE
    assert _match_cap_seconds("KXNBAGAME-26APR21BOSBUF-BOS") == \
        _MATCH_MAX_DELTA_S_DATE_ONLY


# ---------------------------------------------------------------------------
# Delta measurement
# ---------------------------------------------------------------------------

def test_delta_is_zero_for_a_correct_match():
    correct = _game("Texas Rangers", "Detroit Tigers", "2026-07-04T20:05:00Z")
    assert _match_delta_seconds(DETTEX_JUL04_TICKER, correct) == 0.0


def test_delta_tolerates_small_schedule_slop():
    """A 1-minute rain-delay style offset stays tiny and inside the cap."""
    slightly_off = _game("Texas Rangers", "Detroit Tigers",
                         "2026-07-04T20:06:00Z")
    delta = _match_delta_seconds(DETTEX_JUL04_TICKER, slightly_off)
    assert delta == 60.0
    assert abs(delta) < _match_cap_seconds(DETTEX_JUL04_TICKER)


def test_delta_reproduces_the_production_incident():
    """Trade 11598: Jul 4 ticker matched to the Jul 2 game → ~-44h.
    Trade 11599: Jul 5 ticker matched to the SAME Jul 2 game → ~-67h.
    Negative = matched an EARLIER game, the observed failure direction."""
    d4 = _match_delta_seconds(DETTEX_JUL04_TICKER, DETTEX_JUL02_GAME)
    d5 = _match_delta_seconds(DETTEX_JUL05_TICKER, DETTEX_JUL02_GAME)
    assert d4 == -(43 * HOUR + 58 * 60)
    assert d5 == -(67 * HOUR + 23 * 60)
    assert d4 < 0 and d5 < 0
    assert abs(d4) > _match_cap_seconds(DETTEX_JUL04_TICKER)
    assert abs(d5) > _match_cap_seconds(DETTEX_JUL05_TICKER)


def test_delta_is_none_when_uncomputable():
    """NULL must mean 'unknown', never 0 — otherwise unparseable rows would
    masquerade as perfect matches in the weekly aggregate."""
    assert _match_delta_seconds(DETTEX_JUL04_TICKER, None) is None
    assert _match_delta_seconds(DETTEX_JUL04_TICKER, {}) is None
    assert _match_delta_seconds("garbage", DETTEX_JUL02_GAME) is None
    assert _match_delta_seconds(
        DETTEX_JUL04_TICKER, {"commence_time": "not-a-date"}) is None


def test_delta_accepts_naive_commence_time_as_utc():
    naive = _game("Texas Rangers", "Detroit Tigers", "2026-07-04T20:05:00")
    assert _match_delta_seconds(DETTEX_JUL04_TICKER, naive) == 0.0


# ---------------------------------------------------------------------------
# measure_match_delta — logging behaviour
# ---------------------------------------------------------------------------

def test_over_cap_logs_greppable_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="sports_trader"):
        delta = measure_match_delta(
            DETTEX_JUL04_TICKER, DETTEX_JUL02_GAME, "sports_underdog_v31",
        )
    assert delta == -(43 * HOUR + 58 * 60)
    assert _MATCH_DELTA_LOG_PREFIX in caplog.text
    assert "strategy=sports_underdog_v31" in caplog.text
    assert f"ticker={DETTEX_JUL04_TICKER}" in caplog.text
    assert "delta_h=-43.97" in caplog.text
    assert "phase1=logged_only" in caplog.text


def test_within_cap_does_not_log(caplog):
    correct = _game("Texas Rangers", "Detroit Tigers", "2026-07-04T20:05:00Z")
    with caplog.at_level(logging.WARNING, logger="sports_trader"):
        delta = measure_match_delta(DETTEX_JUL04_TICKER, correct, "s")
    assert delta == 0.0
    assert _MATCH_DELTA_LOG_PREFIX not in caplog.text


def test_warn_false_suppresses_log_but_still_measures(caplog):
    """The observer path: one row per ticker per scan would flood the journal
    at the observed ~31% mismatch rate."""
    with caplog.at_level(logging.WARNING, logger="sports_trader"):
        delta = measure_match_delta(
            DETTEX_JUL04_TICKER, DETTEX_JUL02_GAME, "observer", warn=False,
        )
    assert delta == -(43 * HOUR + 58 * 60)
    assert _MATCH_DELTA_LOG_PREFIX not in caplog.text


def test_measure_returns_none_without_raising_on_bad_input():
    assert measure_match_delta("garbage", DETTEX_JUL02_GAME, "s") is None
    assert measure_match_delta(DETTEX_JUL04_TICKER, None, "s") is None
