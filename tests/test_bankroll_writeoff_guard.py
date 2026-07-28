"""Written-off paper positions must not credit the bankroll a second time.

On 2026-07-28 shutdown_fade's 66 open paper legs ($11,000, all on
KXNUMSHUTDOWNS-27JAN01-T2, settling 2027-01-01) were credited back to the paper
tracker by hand — the strategy is deprecated and its capital had frozen paper
research. The ledger rows stay open (bet records are never deleted), so on
2027-01-01 the normal `bankroll += payout` would hand back the same capital
twice. These tests pin the guard that prevents it.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.settlement import SettlementChecker


CUTOFF = "2026-07-28T11:50:00+00:00"


def _checker(strategies=("shutdown_fade",), cutoff=CUTOFF, bankroll=100.0):
    om = MagicMock()
    om.bankroll = bankroll
    om.config = MagicMock(
        BANKROLL_WRITEOFF_STRATEGIES=frozenset(strategies),
        BANKROLL_WRITEOFF_BEFORE=cutoff,
    )
    om._persist_bankroll = AsyncMock()
    return SettlementChecker(MagicMock(), MagicMock(), om, None)


def _trade(strategy="shutdown_fade", ts="2026-03-26T10:00:00+00:00"):
    return {"strategy": strategy, "timestamp": ts, "size": 166.67, "price": 0.454}


def test_written_off_trade_is_flagged():
    assert _checker()._is_written_off(_trade()) is True


def test_other_strategies_are_untouched():
    """ladder_fed's $750 was NOT written off — its credits must still land."""
    assert _checker()._is_written_off(_trade(strategy="ladder_fed")) is False


def test_entry_after_the_cutoff_still_credits():
    """If a written-off strategy ever resumes, its NEW positions were never
    released, so they must credit normally."""
    t = _trade(ts="2026-08-01T00:00:00+00:00")
    assert _checker()._is_written_off(t) is False


def test_sqlite_space_separated_timestamp_parses():
    """Ledger timestamps appear both as ISO-T and space-separated — a string
    compare would break here, so the guard must parse both."""
    assert _checker()._is_written_off(_trade(ts="2026-03-26 10:00:00")) is True


def test_naive_timestamp_is_treated_as_utc():
    assert _checker()._is_written_off(_trade(ts="2026-03-26T10:00:00")) is True


def test_z_suffix_timestamp_parses():
    assert _checker()._is_written_off(_trade(ts="2026-03-26T10:00:00Z")) is True


def test_unparseable_timestamp_fails_open_and_still_credits():
    """Suppression requires positive proof the entry predates the write-off.
    `trades.timestamp` is NOT NULL and written by us as ISO, so an unreadable
    value means a synthetic or corrupt row — and silently suppressing credits
    forever is a worse failure mode than one stale credit."""
    assert _checker()._is_written_off(_trade(ts="nonsense")) is False


def test_absent_timestamp_fails_open():
    t = _trade()
    del t["timestamp"]
    assert _checker()._is_written_off(t) is False


def test_no_configured_strategies_disables_the_guard():
    assert _checker(strategies=())._is_written_off(_trade()) is False


def test_missing_cutoff_disables_the_guard():
    """Without a cutoff there is no way to tell released legs from new ones —
    fail open rather than silently suppressing every future credit."""
    assert _checker(cutoff="")._is_written_off(_trade()) is False


def test_config_absent_does_not_raise():
    om = MagicMock(spec=["bankroll", "_persist_bankroll"])
    om.bankroll = 100.0
    checker = SettlementChecker(MagicMock(), MagicMock(), om, None)
    assert checker._is_written_off(_trade()) is False


def test_default_config_targets_the_shutdown_fade_writeoff():
    """The shipped defaults must actually cover the 2026-07-28 event."""
    from core.config import Config

    cfg = Config()
    assert "shutdown_fade" in cfg.BANKROLL_WRITEOFF_STRATEGIES
    assert cfg.BANKROLL_WRITEOFF_BEFORE


# ── the credit path itself ────────────────────────────────────────────

def _settle_once(trade, result, bankroll=1000.0, strategies=("shutdown_fade",)):
    import asyncio

    client = MagicMock()
    db = MagicMock()
    db.get_unsettled_trades = AsyncMock(return_value=[trade])
    db.settle_trade = AsyncMock()
    om = MagicMock()
    om.bankroll = bankroll
    om._persist_bankroll = AsyncMock()
    om.config = MagicMock(
        BANKROLL_WRITEOFF_STRATEGIES=frozenset(strategies),
        BANKROLL_WRITEOFF_BEFORE=CUTOFF,
    )
    checker = SettlementChecker(client, db, om, None)
    client.get_market.return_value = {"status": "finalized", "result": result}
    asyncio.run(checker.check_and_settle())
    return om, db


def _open_leg(strategy="shutdown_fade", ts="2026-03-26T10:00:00+00:00"):
    """A real shutdown_fade leg: NO @ $0.454, $166.67 — payout would be $367."""
    return {
        "id": 1, "market_id": "KXNUMSHUTDOWNS-27JAN01-T2", "strategy": strategy,
        "direction": "no", "price": 0.454, "size": 166.67, "fee": 26.70,
        "status": "paper_filled", "timestamp": ts,
    }


def test_written_off_win_settles_the_ledger_but_credits_nothing():
    om, db = _settle_once(_open_leg(), result="no")
    db.settle_trade.assert_called_once()  # ledger still records the true outcome
    assert om.bankroll == pytest.approx(1000.0, abs=0.01)
    om._persist_bankroll.assert_not_awaited()


def test_non_written_off_win_still_credits_full_payout():
    om, _ = _settle_once(_open_leg(strategy="ladder_fed"), result="no")
    assert om.bankroll == pytest.approx(1000.0 + 166.67 / 0.454, abs=0.01)


def test_written_off_loss_is_unaffected():
    """Losses never credited anyway — the guard must not change that path."""
    om, db = _settle_once(_open_leg(), result="yes")
    db.settle_trade.assert_called_once()
    assert om.bankroll == pytest.approx(1000.0, abs=0.01)
