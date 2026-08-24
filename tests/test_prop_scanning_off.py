"""Props are not requested any more — they cost credits and nothing reads them.

Measured 2026-08-24 against x-requests-last, not estimated: an MLB event costs
7 credits for game markets and 16 with props. At 10 MLB games inside the 72h
window and 144 scans a day that is ~13k credits/day, ~389k a month — 15% of the
entire scan — while the month sat at 90% of its 5M with six days to go.

Nothing consumed them. is_bet() has always returned False for props, so they
are never bet; Telegram stopped sending them 07-22, the app feed 08-05, the
dashboard +EV tab 08-05. What was left was paper-ledger rows for a question
already answered (first-entry props negative, the late-crosser "edge" a single
06-14 slate).

Reversible by env, because "we stopped collecting it" is the kind of decision
that should be undoable without a deploy.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_core as core  # noqa: E402


@pytest.fixture
def with_props(monkeypatch):
    monkeypatch.setenv("SOFTBOOK_SCAN_PROPS", "1")
    importlib.reload(core)
    yield core
    monkeypatch.delenv("SOFTBOOK_SCAN_PROPS", raising=False)
    importlib.reload(core)


# ── off by default ────────────────────────────────────────────────────

def test_prop_sports_request_game_markets_only():
    """MLB and NBA are the only sports that ever had props."""
    for sport in ("baseball_mlb", "basketball_nba"):
        assert core.markets_for(sport) == core.GAME_MARKETS


def test_no_prop_key_is_requested():
    asked = core.markets_for("baseball_mlb").split(",")
    assert not [k for k in asked if k in core.ALL_PROPS or k.endswith("_alternate")
                and k not in core.GAME_MARKETS.split(",")]


def test_sports_without_props_are_unchanged():
    for sport in ("baseball_kbo", "tennis_wta_monterrey_open", "soccer_epl"):
        assert core.markets_for(sport) == core.GAME_MARKETS


def test_it_is_actually_cheaper():
    """The saving is the point: 16 market keys down to 7 on MLB."""
    assert len(core.markets_for("baseball_mlb").split(",")) == 7


# ── the flag brings them back ─────────────────────────────────────────

def test_the_env_flag_restores_props(with_props):
    asked = with_props.markets_for("baseball_mlb").split(",")
    assert "pitcher_strikeouts" in asked
    assert len(asked) == 16


def test_the_flag_still_only_alternates_the_supported_keys(with_props):
    """pitcher_outs has no _alternate, and an unknown key 422s the whole event
    call — the reason PROP_ALTS exists at all."""
    asked = with_props.markets_for("baseball_mlb").split(",")
    assert "pitcher_outs" in asked and "pitcher_outs_alternate" not in asked


# ── the rest of the prop machinery is untouched ───────────────────────

def test_props_are_still_defined_and_still_never_bet():
    """Turning off collection must not change the VERDICT logic — existing
    prop rows keep grading the same way."""
    assert core.PROP_MARKETS and core.PROP_SHORT
    for market in core.PROP_SHORT:
        assert core.is_bet(True, market, "High") is False
        assert core.is_bet(True, market, "Med") is False
