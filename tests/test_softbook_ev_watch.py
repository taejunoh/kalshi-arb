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
import softbook_ev_recorder as rec
import softbook_push_worker as push                             # noqa: E402

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
        assert core.is_bet(True, "h2h", "Med") is True      # verdict independent of ev
        assert core.is_ev_watch("h2h", ev) == (ev >= core.EV_WATCH_MIN)


def test_ml_in_the_watch_band_now_gets_the_plain_bet_tag():
    """The hold advisory is retired (user, 2026-08-05). ML at EV>=5% carried
    "베팅 보류 권장" from 7/22 because HIGH ML in that band ran 4-21 (-60.8%).
    High ML is no longer bet at all, and on the cohort we do bet the pathology
    does not reproduce: Med, n=32, WR 50.0% vs BE 42.0%, +$22.23 on $89.95.
    n=32 decides nothing either way — the band is 6.4% of Med ML volume, is not
    negative, and would need ~8 months to become decidable, so it rides with the
    rest rather than carrying a warning nobody can act on."""
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.06)
    snap(db, b, OPEN)
    assert rec._first_entry_note(db, b) == rec.BET_TAG


def test_ml_below_threshold_gets_the_plain_bet_tag():
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.04)
    snap(db, b, OPEN)
    assert rec._first_entry_note(db, b) == rec.BET_TAG


def test_spread_at_high_ev_gets_the_plain_bet_tag():
    db = db_()
    b = game_bet(market="spread", ev=0.07)
    snap(db, b, OPEN)
    assert rec._first_entry_note(db, b) == rec.BET_TAG


def test_late_crosser_ml_at_high_ev_still_skips():
    """The watch overlay must not resurrect a SKIP: late-crossers were already out
    and stay out, whatever their EV."""
    db = db_()
    b = game_bet(market="h2h", side="Detroit Tigers", point=None, ev=0.07)
    snap(db, b, OPEN, "Low")                   # below ML's bet tier at the open
    snap(db, b, "2026-06-13T18:40:00+00:00")   # crosses into it only after the move
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "late-crosser" in note


def test_prop_at_high_ev_still_skips_as_prop():
    db = db_()
    b = prop_bet(ev=0.07)
    snap(db, b, OPEN)
    note = rec._first_entry_note(db, b)
    assert "SKIP" in note and "prop" in note


# ---------- the stake segment ----------

def test_no_hold_tag_survives_anywhere():
    """The retirement has to be total: a leftover WATCH_TAG would still reach
    Telegram, and a leftover is_ev_watch call would still swap the push title."""
    assert not hasattr(rec, "WATCH_TAG")
    for mod, name in ((rec, "softbook_ev_recorder"), (push, "softbook_push_worker")):
        src = open(mod.__file__).read()
        assert "is_ev_watch" not in src, name


def test_the_band_survives_as_an_analysis_cell():
    """core.is_ev_watch stays so the band can still be re-measured on demand —
    it just no longer drives any user-facing surface."""
    assert core.is_ev_watch("h2h", 0.06) is True
