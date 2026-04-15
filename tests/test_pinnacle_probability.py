"""Unit tests for _pinnacle_probability helper on SportsTraderStrategy base."""
from unittest.mock import MagicMock

from strategies.sports_trader import SportsTraderStrategy


def _make_strategy() -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = 100.0
    client = MagicMock()
    return SportsTraderStrategy(config, order_mgr, client)


def _game_with_books(books: list[dict]) -> dict:
    return {
        "home_team": "Lakers",
        "away_team": "Celtics",
        "bookmakers": books,
    }


def _h2h_bookmaker(key: str, home_odds: float, away_odds: float) -> dict:
    return {
        "key": key,
        "markets": [{
            "key": "h2h",
            "outcomes": [
                {"name": "Lakers", "price": home_odds},
                {"name": "Celtics", "price": away_odds},
            ],
        }],
    }


def test_pinnacle_extracts_probs_from_pinnacle_bookmaker():
    """Pinnacle present → normalized probs, vig removed."""
    strat = _make_strategy()
    # Pinnacle: Lakers 2.0 (50%), Celtics 1.95 (51.3%) — vig ~1.3%
    game = _game_with_books([
        _h2h_bookmaker("pinnacle", 2.0, 1.95),
        _h2h_bookmaker("draftkings", 1.9, 2.0),  # should be ignored
    ])
    result = strat._pinnacle_probability(game)
    assert result is not None
    assert result["home_team"] == "Lakers"
    assert result["away_team"] == "Celtics"
    # implied: 0.5 home, 0.5128 away → total 1.0128
    # normalized: 0.5/1.0128 ≈ 0.4937, 0.5128/1.0128 ≈ 0.5063
    assert abs(result["home_prob"] - 0.4937) < 0.001
    assert abs(result["away_prob"] - 0.5063) < 0.001
    assert abs(result["home_prob"] + result["away_prob"] - 1.0) < 1e-9


def test_pinnacle_returns_none_when_pinnacle_missing():
    """No pinnacle key in bookmakers → None (no fallback)."""
    strat = _make_strategy()
    game = _game_with_books([
        _h2h_bookmaker("draftkings", 1.9, 2.0),
        _h2h_bookmaker("fanduel", 1.95, 1.95),
    ])
    assert strat._pinnacle_probability(game) is None


def test_pinnacle_returns_none_for_invalid_odds():
    """Pinnacle present but odds ≤ 1.0 → None (invalid)."""
    strat = _make_strategy()
    game = _game_with_books([
        _h2h_bookmaker("pinnacle", 1.0, 0.5),  # invalid
    ])
    assert strat._pinnacle_probability(game) is None


def test_pinnacle_returns_none_for_empty_bookmakers():
    """No bookmakers at all → None."""
    strat = _make_strategy()
    game = _game_with_books([])
    assert strat._pinnacle_probability(game) is None


def test_pinnacle_payload_hash_stable_when_unchanged():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [
        {"id": "g1", "bookmakers": [
            {"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8},
                                             {"name": "B", "price": 2.1}]}]}]},
        {"id": "g2", "bookmakers": [
            {"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": "X", "price": 1.5},
                                             {"name": "Y", "price": 2.6}]}]}]},
    ]
    h1 = _compute_pinnacle_payload_hash(games)
    h2 = _compute_pinnacle_payload_hash(list(reversed(games)))
    assert h1 == h2


def test_pinnacle_payload_hash_changes_on_price_move():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [{"id": "g1", "bookmakers": [
        {"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8},
                                         {"name": "B", "price": 2.1}]}]}]}]
    h1 = _compute_pinnacle_payload_hash(games)
    games[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.85
    h2 = _compute_pinnacle_payload_hash(games)
    assert h1 != h2


def test_pinnacle_payload_hash_none_when_no_pinnacle():
    from strategies.sports_trader import _compute_pinnacle_payload_hash
    games = [{"id": "g1", "bookmakers": [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": []}]}]}]
    assert _compute_pinnacle_payload_hash(games) is None
