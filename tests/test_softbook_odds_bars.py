# tests/test_softbook_odds_bars.py
"""core.odds_bars is the single source for the 최소/본전 prices.

Both surfaces that show these bars — the Telegram alert and the app card — must
read the same function. The app used to mirror the formula AND the EV_MIN
constant in TypeScript, so moving the policy server-side would have left the card
showing a stale floor with nothing to catch it. These tests pin the math and the
single-source property.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import softbook_ev_core as core
import softbook_ev_recorder as rec


def test_odds_bars_math_underdog():
    # ev=0.074 @ 2.2 -> fair = 1.074/2.2 = 0.488181...
    mn, be = core.odds_bars(0.074, 2.2)
    assert be == 1 / (1.074 / 2.2)
    assert mn == (1 + core.EV_MIN) / (1.074 / 2.2)
    # min is always the WORSE-for-the-book / better-for-us price: you need a
    # longer price than breakeven to still clear the ROI>=1% bar.
    assert mn > be


def test_odds_bars_math_favorite():
    mn, be = core.odds_bars(0.05, 1.91)
    assert rec._american(be) == "-122"
    assert rec._american(mn) == "-119"
    assert mn > be


def test_odds_bars_none_when_inputs_unusable():
    assert core.odds_bars(None, 2.2) is None
    assert core.odds_bars(0.05, None) is None
    assert core.odds_bars(0.05, 1.0) is None      # not a valid decimal price
    assert core.odds_bars(0.05, 0.4) is None


def test_odds_bars_scales_with_ev_min():
    """min is defined by policy: raising EV_MIN must move the floor, and nothing
    may hold a private copy of the constant that stays put."""
    lo = core.odds_bars(0.04, 2.06)[0]
    core_ev_min = core.EV_MIN
    try:
        core.EV_MIN = 0.03
        hi = core.odds_bars(0.04, 2.06)[0]
    finally:
        core.EV_MIN = core_ev_min
    assert hi > lo


def test_telegram_line_reads_odds_bars():
    # fair = 1.04/2.06 = 0.50485; min = 1.01/fair -> +100; be = 1/fair -> -102
    line = rec._min_odds_line(0.04, 2.06)
    assert "최소 +100" in line and "본전 -102" in line
    mn, be = core.odds_bars(0.04, 2.06)
    assert rec._american(mn) in line and rec._american(be) in line


def test_telegram_line_empty_when_no_bars():
    """The line is string-concatenated into the alert body, so an unusable price
    must degrade to nothing rather than raise mid-send."""
    assert rec._min_odds_line(None, 2.06) == ""
    assert rec._min_odds_line(0.04, None) == ""
    assert rec._min_odds_line(0.04, 1.0) == ""


class _FakeConn:
    def close(self):
        pass


@pytest.fixture()
def enrich(monkeypatch):
    """_enrich_first_entry with its DB lookups stubbed. Uses monkeypatch so the
    stubs are UNDONE afterwards: api.core is the same module object as core, so
    a bare assignment here would leak into every later test in the session (it
    did — it broke the push-worker and scan-endpoint suites)."""
    import softbook_app_api as api

    monkeypatch.setattr(api, "_ev_conn", lambda: _FakeConn())
    monkeypatch.setattr(api.core, "market_first_entry_sides",
                        lambda c, e, m, p: (True, {"MIA"}))
    return api._enrich_first_entry


def test_app_api_serves_the_same_bars_as_telegram(enrich):
    """The single-source property, asserted end to end: whatever the alert prints,
    the app's served decimals format to. If someone reintroduces a second
    implementation on either side, this fails."""
    ev, odds = 0.074, 2.2
    picks = [{"event_id": "E1", "market": "h2h", "side": "MIA", "player": None,
              "ev": ev, "odds": odds}]
    enrich(picks)

    p = picks[0]
    assert p["min_odds"] is not None and p["be_odds"] is not None
    line = rec._min_odds_line(ev, odds)
    assert rec._american(p["min_odds"]) in line
    assert rec._american(p["be_odds"]) in line


def test_app_api_nulls_bars_when_price_unknown(enrich):
    """No ev/price -> explicit nulls, so the app hides the line instead of
    rendering a bar derived from garbage."""
    picks = [{"event_id": "E1", "market": "h2h", "side": "MIA", "player": None,
              "ev": None, "odds": None}]
    enrich(picks)

    assert picks[0]["min_odds"] is None and picks[0]["be_odds"] is None
