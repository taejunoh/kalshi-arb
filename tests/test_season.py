"""Tests for core.season.is_playoff sport-window classifier."""
from core.season import is_playoff


def test_nba_playoffs_window():
    # Inside window (April 21)
    assert is_playoff("KXNBAGAME-26APR21BOSBUF", "2026-04-21T20:00:00+00:00") is True
    # Boundary start (April 14)
    assert is_playoff("KXNBAGAME-26APR14XYZ", "2026-04-14T20:00:00+00:00") is True
    # Just before window (April 13)
    assert is_playoff("KXNBAGAME-26APR13XYZ", "2026-04-13T20:00:00+00:00") is False
    # After finals (June 26)
    assert is_playoff("KXNBAGAME-26JUN26XYZ", "2026-06-26T20:00:00+00:00") is False


def test_nhl_playoffs_window():
    assert is_playoff("KXNHLGAME-26APR21BOSBUF", "2026-04-21T20:00:00+00:00") is True
    assert is_playoff("KXNHLGAME-26JUL01XYZ", "2026-07-01T20:00:00+00:00") is False


def test_mlb_postseason_window():
    # Mid-October falls in MLB postseason
    assert is_playoff("KXMLBGAME-26OCT15XYZ", "2026-10-15T20:00:00+00:00") is True
    # April is regular-season MLB
    assert is_playoff("KXMLBGAME-26APR21XYZ", "2026-04-21T20:00:00+00:00") is False
    # November after World Series end
    assert is_playoff("KXMLBGAME-26NOV15XYZ", "2026-11-15T20:00:00+00:00") is False


def test_unknown_sport_returns_none():
    """Non-sports tickers should return None so callers can distinguish
    "regular season" from "not a sport we classify"."""
    assert is_playoff("KXFEDMENTION-X", "2026-04-21T20:00:00+00:00") is None
    assert is_playoff("KXBTC-Y", "2026-04-21T20:00:00+00:00") is None


def test_invalid_inputs_return_none():
    assert is_playoff(None, "2026-04-21T20:00:00+00:00") is None
    assert is_playoff("KXNBAGAME-X", None) is None
    assert is_playoff("KXNBAGAME-X", "not-a-timestamp") is None
