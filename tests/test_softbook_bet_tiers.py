"""ML moves to Med; every other market stays High (user, 2026-08-05).

Split by confidence tier, the "ML era break" the 07-28 review flagged is High's
alone. First-entry paper ledger, Jun-Aug: High n=151 WR 41.1% vs BE 42.7%
(Jun +15.9 / Jul -36.1 / Aug +51.1), Med n=497 WR 50.9% vs BE 43.9%, positive in
all three months. Med clears at Wilson LB 46.5% > BE, and still clears with
Wimbledon -- 43% of its PnL and the one chunk that cannot recur -- removed.

The risk this file guards is not the arithmetic, it is DIVERGENCE. The tier lived
as a literal 'High' in nine places across the recorder, the app API, the push
worker and the first-entry definition. Miss one and Telegram, the app and the
paper ledger disagree about what a bet is -- which is exactly what happened on
6/16 and why core.is_bet exists at all.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_core as core  # noqa: E402


# ── the policy ────────────────────────────────────────────────────────

def test_ml_is_bet_at_med():
    assert core.is_bet(True, "h2h", "Med") is True


def test_ml_is_no_longer_bet_at_high():
    """The whole point of the change: High ML sat below its own breakeven."""
    assert core.is_bet(True, "h2h", "High") is False


def test_ml_is_not_bet_at_low():
    assert core.is_bet(True, "h2h", "Low") is False


@pytest.mark.parametrize("market", ["spreads", "totals"])
def test_other_game_markets_are_untouched_at_high(market):
    """The evidence is ML-specific and says nothing about spread/total."""
    assert core.is_bet(True, market, "High") is True
    assert core.is_bet(True, market, "Med") is False


def test_props_still_skip_at_every_tier():
    for tier in ("High", "Med", "Low"):
        for market in core.PROP_SHORT:
            assert core.is_bet(True, market, tier) is False


def test_late_crossers_still_skip_at_the_new_tier():
    """Med did not loosen the first-entry cut -- a post-steam crosser is still
    a crosser."""
    assert core.is_bet(False, "h2h", "Med") is False


def test_tier_is_required_not_defaulted():
    """A defaulted tier would let an un-updated caller keep the old policy
    silently -- the divergence this module exists to prevent."""
    with pytest.raises(TypeError):
        core.is_bet(True, "h2h")


def test_alertable_tiers_covers_every_market_actually_bet():
    """The SQL prefetch set must not drop a tier some market is bet at."""
    alertable = core.alertable_tiers()
    for market in ("h2h", "spreads", "totals"):
        assert core.bet_tiers(market) <= alertable


def test_alertable_tiers_sql_binds_one_param_per_tier():
    sql, params = core.alertable_tiers_sql()
    assert sql.count("?") == len(params) == len(core.alertable_tiers())
    assert set(params) == set(core.alertable_tiers())


# ── first-entry follows the bet tier ──────────────────────────────────

def _db(rows):
    """softbook_ev with (event_id, market, player, side, tier, recorded_at)."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE softbook_ev(event_id TEXT, market TEXT, player TEXT,"
              " side TEXT, tier TEXT, recorded_at TEXT)")
    c.executemany("INSERT INTO softbook_ev VALUES (?,?,?,?,?,?)", rows)
    return c


def test_ml_first_entry_reads_med_at_the_earliest_snapshot():
    """The trap that would have silently killed the change: first-entry used to
    mean 'was High at the open'. Left alone, no Med ML pick could ever be
    first-entry, so every one would tag SKIP and the switch would look like it
    simply turned ML off."""
    db = _db([("E1", "h2h", "", "HOME", "Med", "2026-08-05T10:00:00")])
    seen, sides = core.market_first_entry_sides(db, "E1", "h2h", "")
    assert seen and sides == frozenset({"HOME"})
    assert core.is_bet("HOME" in sides, "h2h", "Med") is True


def test_ml_open_at_high_is_not_a_med_first_entry():
    """A line that opened High and only drifted into Med is not an early
    entry at the tier we bet -- same logic as the late-crosser cut."""
    db = _db([("E2", "h2h", "", "HOME", "High", "2026-08-05T10:00:00")])
    _, sides = core.market_first_entry_sides(db, "E2", "h2h", "")
    assert sides == frozenset()


def test_spreads_first_entry_still_reads_high():
    db = _db([("E3", "spreads", "", "HOME", "High", "2026-08-05T10:00:00")])
    _, sides = core.market_first_entry_sides(db, "E3", "spreads", "")
    assert sides == frozenset({"HOME"})
    assert core.is_bet("HOME" in sides, "spreads", "High") is True


def test_opposite_side_crossing_into_med_later_is_still_a_crosser():
    """Side-awareness survives the tier change (the 6/17 fix)."""
    db = _db([("E4", "h2h", "", "HOME", "Med", "2026-08-05T10:00:00"),
              ("E4", "h2h", "", "AWAY", "Low", "2026-08-05T10:00:00"),
              ("E4", "h2h", "", "AWAY", "Med", "2026-08-05T18:00:00")])
    _, sides = core.market_first_entry_sides(db, "E4", "h2h", "")
    assert sides == frozenset({"HOME"}), "AWAY crossed after the line moved"
    assert core.is_bet("AWAY" in sides, "h2h", "Med") is False


def test_no_snapshot_is_reported_unseen_not_empty():
    """`seen`=False is how callers tell 'no data' from 'data, no first-entry' --
    they tag nothing rather than mislabelling on a transient miss."""
    seen, sides = core.market_first_entry_sides(_db([]), "E9", "h2h", "")
    assert (seen, sides) == (False, frozenset())


# ── no surface is left on the old literal ─────────────────────────────

def test_no_surface_still_hardcodes_tier_high():
    """Ledgers B (paper_bet_high) and its tripwire are named historical series
    and keep their literal deliberately; the live alert path must not."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live = ("softbook_ev_core.py", "softbook_ev_recorder.py",
            "softbook_app_api.py", "softbook_push_worker.py",
            "softbook_ev_measure.py", "softbook_ev_watch_eval.py")
    for name in live:
        with open(os.path.join(root, "scripts", name)) as f:
            body = f.read()
        assert "tier='High'" not in body and 'tier="High"' not in body, name
