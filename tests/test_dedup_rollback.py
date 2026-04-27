"""When _dispatch_entry's order fails because the live balance check
rejected the order BEFORE it reached Kalshi, the pre-dispatch dedup mark
must be rolled back so a future scan can re-attempt the signal once cash
is available. Other failure modes (TTL unfilled, generic Kalshi reject)
keep the dedup to avoid churning on the book."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from strategies.sports_trader import SportsTraderStrategy


def _make_strategy(tmp_file: str) -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    s = SportsTraderStrategy(config, MagicMock(), MagicMock())
    s.TRADED_TICKERS_FILE = tmp_file
    s._traded_tickers = {"KX-T": 1700000000.0}
    s._order_manager = MagicMock()
    return s


def _dispatch(s, ticker="KX-T"):
    return asyncio.run(
        s._dispatch_entry(
            ticker=ticker, side="yes", size=20.0, limit_price=0.4,
            minutes_to_start=120.0, matched_game={}, sport="NBA",
            pinnacle_prob=0.5, is_yes_home=True,
        )
    )


def test_rollback_on_insufficient_balance(tmp_path):
    s = _make_strategy(str(tmp_path / "t.json"))
    s._order_manager.market_order = AsyncMock(
        return_value={"status": "failed", "reason": "insufficient_balance"}
    )
    _dispatch(s)
    assert "KX-T" not in s._traded_tickers


def test_rollback_on_balance_check_failed(tmp_path):
    s = _make_strategy(str(tmp_path / "t.json"))
    s._order_manager.market_order = AsyncMock(
        return_value={"status": "failed", "reason": "balance_check_failed"}
    )
    _dispatch(s)
    assert "KX-T" not in s._traded_tickers


def test_no_rollback_on_ttl_unfilled(tmp_path):
    s = _make_strategy(str(tmp_path / "t.json"))
    s._order_manager.market_order = AsyncMock(
        return_value={"status": "skipped", "reason": "limit_ttl_unfilled"}
    )
    _dispatch(s)
    assert "KX-T" in s._traded_tickers


def test_no_rollback_on_generic_failed(tmp_path):
    """Kalshi accepted-then-failed (place_order returned None after the
    request reached the API): can't be sure the order isn't on the book,
    so keep the dedup."""
    s = _make_strategy(str(tmp_path / "t.json"))
    s._order_manager.market_order = AsyncMock(return_value={"status": "failed"})
    _dispatch(s)
    assert "KX-T" in s._traded_tickers


def test_no_rollback_on_filled(tmp_path):
    s = _make_strategy(str(tmp_path / "t.json"))
    s._order_manager.market_order = AsyncMock(
        return_value={"status": "filled", "trade_id": 42}
    )
    s._record_entry_clv = AsyncMock()
    _dispatch(s)
    assert "KX-T" in s._traded_tickers
