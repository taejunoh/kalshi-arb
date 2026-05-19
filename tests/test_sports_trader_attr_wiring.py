"""Verify scan_and_execute stashes per-market context for subclasses."""
from strategies.sports_trader import SportsTraderStrategy


class _CaptureStrategy(SportsTraderStrategy):
    """Test double — captures attribute snapshot at evaluate() time."""
    STRATEGY_NAME = "capture_test"
    OBSERVATION_ONLY_SPORTS = frozenset()

    def __init__(self):
        # Bypass parent __init__ requirements
        self._captured = {}
        self._traded_tickers = {}
        self._last_tournament_slug = None
        self._last_yes_sub_title = None
        self._last_mts = None

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, **kwargs):
        self._captured = {
            "slug": getattr(self, "_last_tournament_slug", "<unset>"),
            "title": getattr(self, "_last_yes_sub_title", "<unset>"),
            "mts": getattr(self, "_last_mts", "<unset>"),
        }
        return None


def test_stash_market_context_populates_attributes():
    s = _CaptureStrategy()
    market = {
        "ticker": "KXATPMATCH-X",
        "yes_sub_title": "Will X win the X vs Y: Round Of 64 match?",
    }
    # tournament_slug rides on the matched odds game via _tournament_slug
    # (injected by _fetch_odds_multi). Kalshi market dicts don't carry it.
    game = {
        "commence_time": "2026-06-01T12:00:00+00:00",
        "_tournament_slug": "tennis_atp_french_open",
    }
    s._stash_market_context(market, game)
    assert s._last_tournament_slug == "tennis_atp_french_open"
    assert "Round Of 64" in s._last_yes_sub_title
    # mts depends on now() — just confirm it's a finite number or None
    assert s._last_mts is None or isinstance(s._last_mts, (int, float))


def test_stash_market_context_handles_missing_game():
    s = _CaptureStrategy()
    market = {"ticker": "X", "yes_sub_title": None}
    s._stash_market_context(market, None)
    assert s._last_tournament_slug is None
    assert s._last_yes_sub_title is None
    assert s._last_mts is None


def test_stash_market_context_handles_missing_keys():
    """game dict without _tournament_slug + market dict without keys."""
    s = _CaptureStrategy()
    s._stash_market_context({"ticker": "X"}, {"commence_time": None})
    assert s._last_tournament_slug is None
    assert s._last_yes_sub_title is None
    assert s._last_mts is None
