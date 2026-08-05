"""The dashboard +EV tab lists the bets we actually place — nothing else.

The tab says "place each at the named sportsbook", so every card on it is an
instruction. It filtered on tier=='High' from 2026-06-23, which was the bet rule
then. It stopped being the bet rule twice over:

  * props never qualified — they are skipped at every tier;
  * on 2026-08-05 ML moved to Med, so the tab showed High ML (not bet) and hid
    Med ML (bet) — the exact complement of the truth for moneyline.

Observed live on 2026-08-05: the tab listed 4 picks — 1 High ML and 3 High
props — and not one of them was a bet.

The feed now asks the recorder, the same single source as the Telegram tag and
the app card, so the three surfaces cannot disagree about what a bet is.
"""
import json
import sqlite3

import pytest

import softbook_ev_core as core
import softbook_ev_recorder as rec
import softbook_ev_today as today


def _bet(tier, market, ev, *, away="AAA", home="BBB", player=None, side="AAA"):
    return {
        "tier": tier, "market": market, "ev": ev, "conf": 60.0,
        "sport": "baseball_mlb", "event_id": f"E-{tier}-{market}-{player or side}",
        "commence_time": "2099-01-01T00:00:00+00:00",
        "home": home, "away": away, "side": side, "point": None, "player": player,
        "label": f"{tier} {market}", "book": "FanDuel", "odds": 2.0,
        "fair": 0.55, "pin_price": 1.95, "n_books": 5, "n_ev": 3, "books_all": [],
    }


def _run_json(monkeypatch, tmp_path, bets, *, recorder=True):
    """main() with --json against a stubbed scan and a REAL in-memory recorder db
    (record() actually writes, so first-entry resolves through the live code path
    rather than a stub that could drift from it). Returns the written feed."""
    out_path = tmp_path / "ev_bets_latest.json"
    db = None
    if recorder:
        db = sqlite3.connect(":memory:")
        db.execute(rec.SCHEMA)
        db.execute(rec.ALERT_SCHEMA)
        rec._ensure_alert_cols(db)
    monkeypatch.setattr(today, "read_key", lambda: "KEY")
    monkeypatch.setattr(today, "active_sports", lambda key: ["baseball_mlb"])
    monkeypatch.setattr(today, "scan_sport", lambda sport, key, **kw: (list(bets), 1, None))
    monkeypatch.setattr(rec, "init_db", lambda: db)
    monkeypatch.setattr(rec, "wakeup_requote", lambda *a, **k: 0)   # network
    monkeypatch.setattr(rec, "alert_new_high", lambda *a, **k: 0)   # telegram
    monkeypatch.setattr(today.sys, "argv", ["prog", "--json", str(out_path)])
    today.main()
    return json.loads(out_path.read_text())


def _labels(feed):
    return {b["team"] for b in feed["bets"]}


# ── the screenshot ────────────────────────────────────────────────────

def test_the_observed_tab_would_now_be_empty(monkeypatch, tmp_path):
    """1 High ML + 3 High props, exactly what the live tab showed. None is a bet."""
    bets = [_bet("High", "h2h", 0.014)] + [
        _bet("High", "batter_total_bases", ev, player=p, side="Over",
             away=f"A{i}", home=f"H{i}")
        for i, (p, ev) in enumerate((("Narvaez", 0.079), ("Mangum", 0.065),
                                     ("Ortiz", 0.060)))]
    assert _run_json(monkeypatch, tmp_path, bets)["bets"] == []


# ── per market ────────────────────────────────────────────────────────

def test_med_ml_is_listed_and_high_ml_is_not(monkeypatch, tmp_path):
    """The inversion that motivated this: the tab used to do exactly the opposite."""
    feed = _run_json(monkeypatch, tmp_path, [
        _bet("Med", "h2h", 0.04, away="CCC", home="DDD"),
        _bet("High", "h2h", 0.05),
    ])
    assert _labels(feed) == {"Med h2h"}


@pytest.mark.parametrize("market", ["spread", "total"])
def test_spread_and_total_still_list_at_high_only(monkeypatch, tmp_path, market):
    feed = _run_json(monkeypatch, tmp_path, [
        _bet("High", market, 0.05, side="Over"),
        _bet("Med", market, 0.04, away="CCC", home="DDD", side="Over"),
    ])
    assert _labels(feed) == {f"High {market}"}


def test_props_are_never_listed_at_any_tier(monkeypatch, tmp_path):
    bets = [_bet(t, "batter_total_bases", 0.07, player="Nimmo", side="Over",
                 away=f"A{t}", home=f"H{t}") for t in ("High", "Med", "Low")]
    assert _run_json(monkeypatch, tmp_path, bets)["bets"] == []


def test_low_tier_is_never_listed(monkeypatch, tmp_path):
    bets = [_bet("Low", m, 0.03, side="Over") for m in ("h2h", "spread", "total")]
    assert _run_json(monkeypatch, tmp_path, bets)["bets"] == []


# ── it follows core, it does not restate it ───────────────────────────

def test_the_feed_agrees_with_core_bet_tiers(monkeypatch, tmp_path):
    """A tier policy change must move this tab with it, with no edit here."""
    bets = [_bet(t, m, 0.05, away=f"A{t}{m}", home=f"H{t}{m}", side="Over")
            for m in ("h2h", "spread", "total") for t in ("High", "Med", "Low")]
    feed = _run_json(monkeypatch, tmp_path, bets)
    assert _labels(feed) == {f"{t} {m}" for m in ("h2h", "spread", "total")
                             for t in core.bet_tiers(m)}


# ── failure policy ────────────────────────────────────────────────────

def test_no_recorder_lists_nothing_rather_than_unverified_picks(monkeypatch, tmp_path):
    """Same policy as rec._is_bet's own error path. An empty tab is honest; a tab
    of picks we could not verify is an instruction to bet them."""
    feed = _run_json(monkeypatch, tmp_path,
                     [_bet("Med", "h2h", 0.04), _bet("High", "spread", 0.05)],
                     recorder=False)
    assert feed["bets"] == []
    assert feed["generated_at"] is not None        # the file is still written
