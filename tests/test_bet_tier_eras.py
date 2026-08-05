"""A rule change must not restate the published record.

keepmypick.com re-derives its whole track record from this module every morning.
Until 2026-08-05 it scored every historical pick under whatever the rule was
*that morning*, so the ML High->Med change would have moved the public record
from 140-160 / +2.13% to 332-316 / +13.90% overnight — no bet settling, ROI up
6x, sample doubled.

The direction is not a coincidence and it is not specific to this change: you
change a rule because the new one looks better on the data you already have, so
re-scoring history under it always flatters. Published that way it is an
in-sample fit wearing a track record's clothes. The site's own copy already
promised "when the rules change, the record is segmented by date instead" — the
code just wasn't doing it.

So: bets are scored under the rule live on the day they were placed, and these
tests fail if that stops being true.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_core as core  # noqa: E402

ML_MOVED = "2026-08-05"


# ── the era table ─────────────────────────────────────────────────────

def test_current_policy_is_the_newest_era_itself():
    """BET_TIERS *is* the first entry, not a copy of it — otherwise the two
    drift and the live rule stops matching the one history is scored against."""
    assert core.BET_TIERS is core.BET_TIER_ERAS[0][1]


def test_eras_are_newest_first():
    starts = [start for start, _ in core.BET_TIER_ERAS]
    assert starts == sorted(starts, reverse=True)


def test_the_oldest_era_is_open_ended():
    """Every bet must land in some era, however old the ledger gets."""
    assert core.BET_TIER_ERAS[-1][0] == "0000-00-00"
    assert core.bet_tiers_at("h2h", "1970-01-01") == frozenset({"High"})


# ── scoring by date ───────────────────────────────────────────────────

def test_ml_before_the_change_is_scored_at_high():
    assert core.bet_tiers_at("h2h", "2026-08-04") == frozenset({"High"})


def test_ml_on_and_after_the_change_is_scored_at_med():
    assert core.bet_tiers_at("h2h", ML_MOVED) == frozenset({"Med"})
    assert core.bet_tiers_at("h2h", "2026-12-31") == frozenset({"Med"})


@pytest.mark.parametrize("market", ["spread", "total"])
@pytest.mark.parametrize("day", ["2026-06-01", "2026-08-04", ML_MOVED])
def test_untouched_markets_read_high_in_every_era(market, day):
    assert core.bet_tiers_at(market, day) == frozenset({"High"})


def test_current_tiers_match_the_newest_era():
    for market in ("h2h", "spread", "total"):
        assert core.bet_tiers(market) == core.bet_tiers_at(market, "2999-01-01")


# ── first-entry honours the as-of date ────────────────────────────────

def _db(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE softbook_ev(event_id TEXT, market TEXT, player TEXT,"
              " side TEXT, tier TEXT, recorded_at TEXT)")
    c.executemany("INSERT INTO softbook_ev VALUES (?,?,?,?,?,?)", rows)
    return c


def test_a_high_ml_pick_is_first_entry_under_the_old_rule():
    """The heart of it: this pick counted then, so it still counts now."""
    db = _db([("E1", "h2h", "", "HOME", "High", "2026-07-01T10:00:00")])
    _, sides = core.market_first_entry_sides(db, "E1", "h2h", "", as_of="2026-07-01")
    assert sides == frozenset({"HOME"})


def test_the_same_pick_would_not_qualify_under_todays_rule():
    """Which is exactly why as_of exists — without it this pick vanishes from
    the record retroactively."""
    db = _db([("E1", "h2h", "", "HOME", "High", "2026-07-01T10:00:00")])
    _, sides = core.market_first_entry_sides(db, "E1", "h2h", "")
    assert sides == frozenset()


def test_a_med_ml_pick_qualifies_only_from_the_change_onward():
    db = _db([("E2", "h2h", "", "HOME", "Med", "2026-07-01T10:00:00")])
    _, before = core.market_first_entry_sides(db, "E2", "h2h", "", as_of="2026-08-04")
    _, after = core.market_first_entry_sides(db, "E2", "h2h", "", as_of=ML_MOVED)
    assert before == frozenset()
    assert after == frozenset({"HOME"})


def test_no_as_of_means_the_rule_now():
    """Live surfaces pass nothing and must get today's policy, not an era."""
    db = _db([("E3", "h2h", "", "HOME", "Med", "2026-08-05T10:00:00")])
    _, sides = core.market_first_entry_sides(db, "E3", "h2h", "")
    assert sides == frozenset({"HOME"})


# ── the prefetch set must span eras ───────────────────────────────────

def test_alertable_tiers_covers_every_era_not_just_the_current_one():
    """Historical scoring runs through the same `tier IN (...)` prefetch. If it
    only carried today's tiers, pre-change High ML rows would be filtered out
    before the era logic ever saw them — the silent rewrite by another route."""
    alertable = core.alertable_tiers()
    for _start, table in core.BET_TIER_ERAS:
        for tiers in table.values():
            assert tiers <= alertable
    assert core.BET_TIERS_DEFAULT <= alertable


def test_every_era_is_disclosed_on_the_public_page():
    """core.BET_TIER_ERAS and the exporter's RULE_CHANGES are separate lists —
    one drives scoring, the other the disclosure. A change landing in the first
    without the second is a silent rule change on a public record."""
    import keepmypick_results_export as kmp
    dated = {start for start, _ in core.BET_TIER_ERAS if start != "0000-00-00"}
    assert dated == {c["date"] for c in kmp.RULE_CHANGES}
