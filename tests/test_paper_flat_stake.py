"""Paper staking: one flat unit per bet, and the book never starves.

Two problems, one change.

SIZING. Paper sized off a running bankroll, differently per strategy (5% here,
min($4, 2%) there, Kelly elsewhere, a flat $30 in macro_event). The promotion
gate is Wilson-LB(win rate) vs breakeven — a test that weights every bet
equally — so a stake-weighted ROI sat on a different assumption than the win
rate it was quoted beside. It also let a strategy's early losses shrink its own
later bets, biasing its sample. A flat unit makes ROI mean-return-per-bet.

STARVATION. Entries were skipped when the bankroll ran dry. On 2026-08-05 that
fired 2,010 times in 24 hours with the tracker at -$34.58: the bot was
discarding research signal, and the strategies it starved were whoever shared
the pool with the spender. 20 strategies share the paper book against 6 live.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.order_manager import OrderManager  # noqa: E402


def _om(bankroll=5000.0, stake=10.0, notional=5000.0, cap=100):
    cfg = MagicMock()
    cfg.INITIAL_BANKROLL = bankroll
    cfg.PAPER_FLAT_STAKE_USDC = stake
    cfg.PAPER_NOTIONAL_USDC = notional
    cfg.PAPER_MAX_REPLENISH_PER_DAY = cap
    cfg.real_fee.return_value = 0.07
    om = OrderManager.__new__(OrderManager)
    om.config = cfg
    om.bankroll = bankroll
    om._replenish_day = None
    om._replenish_count = 0
    om._db = MagicMock()
    om._db.has_existing_trade = AsyncMock(return_value=False)
    om._db.record_trade = AsyncMock(return_value=1)
    om._db.record_order = AsyncMock()
    om._persist_bankroll = AsyncMock()
    return om


# ── flat unit ─────────────────────────────────────────────────────────

def test_paper_market_order_books_the_flat_unit_not_the_requested_size():
    om = _om()
    r = asyncio.run(om._paper_market_order(
        "KXT-1", "yes", 250.0, 0.50, "sports_x", "KXT-1"))
    assert r["size_usdc"] == 10.0, "a 250 request must book as one unit"


def test_paper_limit_order_books_the_flat_unit_too():
    om = _om()
    r = asyncio.run(om._paper_limit_order(
        "KXT-2", "yes", 0.50, 30.0, "macro_event", "KXT-2"))
    assert r["size_usdc"] == 10.0, "macro_event's fixed $30 must be normalised"


def test_every_strategy_books_the_same_unit():
    """The point of the change: paper rows become comparable across strategies.
    Previously these three sized 5% / min($4,2%) / a flat $30."""
    om = _om()
    sizes = {s: asyncio.run(om._paper_market_order(
        f"K-{s}", "yes", req, 0.5, s, f"K-{s}"))["size_usdc"]
        for s, req in (("sports_a", 250.0), ("sports_b", 4.0), ("macro_event", 30.0))}
    assert set(sizes.values()) == {10.0}


def test_bankroll_is_debited_by_the_unit_actually_booked():
    om = _om(bankroll=1000.0)
    asyncio.run(om._paper_market_order("K", "yes", 250.0, 0.50, "sports_x", "K"))
    assert om.bankroll == pytest.approx(1000.0 - 10.0 - 0.07)


def test_stake_is_configurable():
    om = _om(stake=25.0)
    r = asyncio.run(om._paper_market_order("K", "yes", 1.0, 0.5, "s", "K"))
    assert r["size_usdc"] == 25.0


# ── never starves ─────────────────────────────────────────────────────

def test_empty_book_is_replenished_rather_than_skipped():
    """The 2,010-skips-a-day behaviour, inverted."""
    om = _om(bankroll=-34.58)
    assert om._ensure_paper_funds(10.0, "sports_x") is True
    assert om.bankroll == 5000.0


def test_a_funded_book_is_left_alone():
    om = _om(bankroll=4000.0)
    assert om._ensure_paper_funds(10.0, "sports_x") is True
    assert om.bankroll == 4000.0, "no gratuitous top-up"


def test_replenish_is_capped_per_day():
    """Retains what the old guard was really protecting: market_maker drained
    paper to -$817 through a 30-second quoting loop. Unbounded top-up would
    restore that."""
    om = _om(bankroll=0.0, cap=3)
    for _ in range(3):
        om.bankroll = 0.0
        assert om._ensure_paper_funds(10.0, "market_maker") is True
    om.bankroll = 0.0
    assert om._ensure_paper_funds(10.0, "market_maker") is False
    assert om._replenish_count == 3


def test_the_cap_resets_on_a_new_day():
    om = _om(bankroll=0.0, cap=1)
    assert om._ensure_paper_funds(10.0, "s") is True
    om.bankroll = 0.0
    assert om._ensure_paper_funds(10.0, "s") is False
    om._replenish_day = "1999-01-01"          # pretend the day rolled over
    om.bankroll = 0.0
    assert om._ensure_paper_funds(10.0, "s") is True


def test_capped_entry_reports_the_real_reason():
    """'insufficient_bankroll' would now be a lie — the book is topped up on
    demand; a refusal means a strategy is looping."""
    om = _om(bankroll=0.0, cap=0)
    assert om._ensure_paper_funds(10.0, "market_maker") is False
