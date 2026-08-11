"""Dismiss is reversible for five seconds — because 관심없음 sits next to 베팅.

A mis-tap on 관심없음 silently buried a pick (feed hides dismissed picks, and
nothing offered a way back). Undo beats a confirm dialog: a real dismiss costs
nothing extra, a mis-tap costs one tap.

The subtle part is the GROUP. Dismissing a pick also auto-dismisses same-game
reverse-middle opposites; an undo that restored only the tapped pick would
leave those buried with no visible cause. The group id is the shared
updated_at stamped across the whole dismiss request.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_app_db as adb  # noqa: E402

PICK = ("E1", "h2h", "Seattle Mariners", 0.0, "")
OPP = ("E1", "spread", "Detroit Tigers", -1.5, "")


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    adb.init_schema(c)
    return c


def test_undismiss_deletes_the_dismissed_row(db):
    adb.set_pick_state(db, PICK, "dismissed", "T1")
    assert adb.undismiss_group(db, PICK) == 1
    assert adb.pick_states(db) == {}


def test_the_auto_dismissed_opposite_comes_back_too(db):
    """Same request = same timestamp = same group."""
    adb.set_pick_state(db, PICK, "dismissed", "T1")
    adb.set_pick_state(db, OPP, "dismissed", "T1")
    assert adb.undismiss_group(db, PICK) == 2
    assert adb.pick_states(db) == {}


def test_an_earlier_separate_dismiss_is_not_swept_up(db):
    """Group by timestamp, not by event: a pick the user dismissed on purpose
    an hour ago must survive someone undoing a different dismiss."""
    adb.set_pick_state(db, OPP, "dismissed", "T0")          # earlier, deliberate
    adb.set_pick_state(db, PICK, "dismissed", "T1")
    assert adb.undismiss_group(db, PICK) == 1
    assert adb.pick_states(db) == {adb._norm(OPP): "dismissed"}


def test_a_bet_row_is_never_deleted(db):
    """Bets are records, not preferences — no undo path may remove one, even
    with a colliding timestamp."""
    adb.set_pick_state(db, PICK, "dismissed", "T1")
    adb.set_pick_state(db, OPP, "bet", "T1")
    assert adb.undismiss_group(db, PICK) == 1
    assert adb.pick_states(db) == {adb._norm(OPP): "bet"}


def test_undoing_a_non_dismissed_pick_does_nothing(db):
    assert adb.undismiss_group(db, PICK) == 0
    adb.set_pick_state(db, PICK, "bet", "T1")
    assert adb.undismiss_group(db, PICK) == 0
    assert adb.pick_states(db) == {adb._norm(PICK): "bet"}
