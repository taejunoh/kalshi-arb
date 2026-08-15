"""The feed reads what is already staked on a game before offering more of it.

2026-08-14, both live: BAL ML and TB -1.5 on the same game were both bet, and
NY Liberty spread alerted three times as the line walked -7.5 → -8.5 → -9.5.
Telegram warns about both (_opposite_note, _dup_note); the app card knew
nothing, because it drew every pick in isolation.

Measured over the paper ledger before choosing the treatment — 314
opposite-direction pairs:

    first pick  ROI +1.3%
    second pick ROI -8.5%     23% of pairs lose BOTH legs

So opposite-direction is not advice, it is a bet not to offer: dropped from the
feed. Same-direction re-bets measured identically to their first legs (-$195 vs
-$195 over 8 real pairs), so blocking has no case; the cost there is unintended
sizing, which is a note.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from softbook_app_api import _annotate_conflicts  # noqa: E402


def _pick(market, side, point=0, **kw):
    return {"event_id": "E1", "market": market, "side": side, "point": point,
            "player": "", "home": "Tampa Bay Rays", "away": "Baltimore Orioles",
            "label": f"{side} {point}", **kw}


def _bet(market, side, point=0, label=None):
    return {"market": market, "side": side, "point": point, "player": "",
            "home": "Tampa Bay Rays", "away": "Baltimore Orioles",
            "label": label or f"{side} {point}"}


# ── opposite direction: hidden ────────────────────────────────────────

def test_the_observed_pair_is_flagged_for_hiding():
    """BAL ML with TB -1.5 already staked: they cannot both win."""
    picks = [_pick("h2h", "Baltimore Orioles")]
    _annotate_conflicts(picks, {"E1": [_bet("spread", "Tampa Bay Rays", -1.5)]})
    assert picks[0]["conflict_hide"]


def test_it_names_the_bet_it_conflicts_with():
    picks = [_pick("h2h", "Baltimore Orioles")]
    _annotate_conflicts(picks, {"E1": [_bet("spread", "Tampa Bay Rays", -1.5,
                                            label="TB -1.5")]})
    assert picks[0]["conflict_hide"] == "TB -1.5"


def test_a_compatible_pair_is_left_alone():
    """Same side across markets can both win — nothing to flag."""
    picks = [_pick("h2h", "Tampa Bay Rays")]
    _annotate_conflicts(picks, {"E1": [_bet("spread", "Tampa Bay Rays", -1.5)]})
    assert "conflict_hide" not in picks[0]


def test_bets_on_other_games_are_irrelevant():
    picks = [_pick("h2h", "Baltimore Orioles")]
    _annotate_conflicts(picks, {"E2": [_bet("spread", "Tampa Bay Rays", -1.5)]})
    assert "conflict_hide" not in picks[0] and "conflict_note" not in picks[0]


# ── same direction: noted, never hidden ───────────────────────────────

def test_a_line_move_rebet_is_noted_not_hidden():
    """The NY Liberty case: -7.5 already staked, -8.5 now offered."""
    picks = [_pick("spread", "New York Liberty", -8.5)]
    _annotate_conflicts(picks, {"E1": [_bet("spread", "New York Liberty", -7.5)]})
    assert "conflict_hide" not in picks[0]
    assert "-7.5" in picks[0]["conflict_note"] or "−7.5" in picks[0]["conflict_note"]


def test_the_identical_line_is_noted_too():
    picks = [_pick("h2h", "Tampa Bay Rays")]
    _annotate_conflicts(picks, {"E1": [_bet("h2h", "Tampa Bay Rays")]})
    assert picks[0]["conflict_note"] == "이미 베팅한 픽"
    assert "conflict_hide" not in picks[0]


# ── failure policy ────────────────────────────────────────────────────

def test_a_broken_conflict_check_never_drops_a_pick(monkeypatch):
    """A pick vanishing silently is worse than a conflict going unflagged."""
    import softbook_app_api as api

    def boom(a, b):
        raise ValueError("bad shape")

    monkeypatch.setattr(api.rec, "_no_both_win", boom)
    picks = [_pick("h2h", "Baltimore Orioles")]
    api._annotate_conflicts(picks, {"E1": [_bet("spread", "Tampa Bay Rays", -1.5)]})
    assert "conflict_hide" not in picks[0]


def test_no_placed_bets_means_no_annotations():
    picks = [_pick("h2h", "Baltimore Orioles"), _pick("spread", "Tampa Bay Rays", -1.5)]
    _annotate_conflicts(picks, {})
    assert all("conflict_hide" not in p and "conflict_note" not in p for p in picks)


def test_it_agrees_with_the_telegram_opposite_note():
    """One source for "can these both win" — a second implementation here would
    let the app and Telegram disagree about the same game."""
    import softbook_ev_recorder as rec
    a = _pick("h2h", "Baltimore Orioles")
    b = _bet("spread", "Tampa Bay Rays", -1.5)
    picks = [dict(a)]
    _annotate_conflicts(picks, {"E1": [b]})
    assert bool(picks[0].get("conflict_hide")) is rec._no_both_win(a, {**b, **{
        "home": a["home"], "away": a["away"]}})
