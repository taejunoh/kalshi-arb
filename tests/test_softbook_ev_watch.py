"""ML + EV>=5% forward-observation flag — 2026-07-22.

An ADVISORY overlay, not a bet-rule change. The High first-entry paper ledger to
7/22 had ML picks with stated EV>=5% at 4-21 (-60.8% ROI) across 18 days, 5 sports
and BOTH months, while the same EV band was harmless in spread (4-0), total (6-6)
and props (-0.4%) — so the flag is ML-only, and a blanket EV cut would also throw
away the profitable spread cell.

The whole point of observing instead of gating: the paper ledger keeps booking
these picks no matter what the user does, so the forward sample accrues on its own.
That only holds if is_ev_watch gates NOTHING — hence the invariant tests below.

Single source (core.is_ev_watch) shared by the Telegram tag, the app /picks
ev_watch flag and the push worker, so the three surfaces cannot drift apart —
the same discipline core.is_bet is under after the 6/16 Telegram-vs-app split.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))                  # sibling test helpers
import softbook_ev_core as core                                # noqa: E402
import softbook_ev_recorder as rec                             # noqa: E402

from test_softbook_first_entry import db_, game_bet, prop_bet, snap   # noqa: E402

OPEN = "2026-06-13T14:00:00+00:00"


# ---------- the predicate ----------

def test_ml_at_or_above_threshold_is_watched():
    assert core.is_ev_watch("h2h", 0.05) is True           # boundary is inclusive
    assert core.is_ev_watch("h2h", 0.06) is True
    assert core.is_ev_watch("h2h", 0.08) is True           # EV_MAX, still ML


def test_ml_below_threshold_is_not_watched():
    assert core.is_ev_watch("h2h", 0.0499) is False
    assert core.is_ev_watch("h2h", 0.03) is False


def test_non_ml_markets_never_watched_even_at_high_ev():
    # spread went 4-0 and total 6-6 in this band — cutting them would destroy value.
    for market in ("spread", "total", "team_total", "pitcher_strikeouts",
                   "batter_total_bases", "player_points"):
        assert core.is_ev_watch(market, 0.08) is False, market


def test_missing_ev_is_not_watched():
    # never flag on absent data — an untagged BET beats a wrongly-held one.
    assert core.is_ev_watch("h2h", None) is False


# ---------- INVARIANT: advisory only, gates nothing ----------

def test_is_bet_is_unchanged_by_ev():
    """is_bet takes no ev at all — the bet rule did not move. If this ever fails,
    the forward sample stops accruing and the whole observation is void."""
    for ev in (0.01, 0.05, 0.08):
        assert core.is_bet(True, "h2h") is True             # verdict independent of ev
        assert core.is_ev_watch("h2h", ev) == (ev >= core.EV_WATCH_MIN)


def test_watched_pick_still_reads_as_a_bet_not_a_skip():
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.06)
    snap(db, b, OPEN, "High")                              # first-entry ML at EV 6%
    note = rec._first_entry_note(db, b)
    assert "BET" in note                                   # still a BET verdict
    assert "SKIP" not in note                              # NOT demoted to skip
    assert "보류" in note                                   # but flagged to hold
    assert "bet small" not in note                         # and no stake instruction


# ---------- the Telegram tag ----------

def test_ml_below_threshold_gets_the_plain_bet_tag():
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.04)
    snap(db, b, OPEN, "High")
    assert rec._first_entry_note(db, b) == rec.BET_TAG


def test_spread_at_high_ev_gets_the_plain_bet_tag():
    db = db_()
    b = game_bet(market="spread", ev=0.07)
    snap(db, b, OPEN, "High")
    assert rec._first_entry_note(db, b) == rec.BET_TAG


def test_late_crosser_ml_at_high_ev_still_skips():
    """The watch overlay must not resurrect a SKIP: late-crossers were already out
    and stay out, whatever their EV."""
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.07)
    snap(db, b, OPEN, "Med")
    snap(db, b, "2026-06-13T18:40:00+00:00", "High")
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "late-crosser" in note


def test_prop_at_high_ev_still_skips_as_prop():
    db = db_()
    b = prop_bet(ev=0.07)
    snap(db, b, OPEN, "High")
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "prop" in note


# ---------- the stake segment ----------

def test_stake_segment_is_suppressed_for_watched_picks():
    """'💵 $N' next to '보류 권장' would contradict itself — the alert body keys the
    stake off the exact BET_TAG, so a watched pick drops it like a SKIP does."""
    assert rec.WATCH_TAG != rec.BET_TAG
