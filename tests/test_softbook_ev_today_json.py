"""The dashboard +EV (live) tab feed (softbook_ev_today.py --json) must carry ONLY
High-tier picks. Med/Low are never actionable (bet rule = first-entry High game-only)
and the user dropped them from the live tab on 2026-06-23 — they survive only in ev.db
(all tiers, for CLV-by-tier re-tuning) and the +EV History tab. This pins that the json
writer filters to High and that nothing Med/Low leaks into the tab feed."""
import json

import softbook_ev_today as today
import softbook_ev_recorder as rec


def _bet(tier, market, ev, *, away="AAA", home="BBB", player=None, side="AAA"):
    return {
        "tier": tier, "market": market, "ev": ev, "conf": 60.0,
        "sport": "baseball_mlb", "event_id": f"E-{tier}-{market}",
        "commence_time": "2099-01-01T00:00:00+00:00",
        "home": home, "away": away, "side": side, "point": None, "player": player,
        "label": f"{tier} {market}", "book": "FanDuel", "odds": 2.0,
        "fair": 0.55, "pin_price": 1.95, "n_books": 5, "n_ev": 3, "books_all": [],
    }


def _run_json(monkeypatch, tmp_path, bets):
    """Run main() with --json against a stubbed scan + no-op recorder; return the
    written feed dict."""
    out_path = tmp_path / "ev_bets_latest.json"
    monkeypatch.setattr(today, "read_key", lambda: "KEY")
    monkeypatch.setattr(today, "active_sports", lambda key: ["baseball_mlb"])
    monkeypatch.setattr(today, "scan_sport", lambda sport, key, **kw: (list(bets), 1, None))
    # recorder writes ALL tiers to ev.db / sends alerts — irrelevant here, stub out
    monkeypatch.setattr(rec, "init_db", lambda: None)
    monkeypatch.setattr(rec, "record", lambda *a, **k: 0)
    monkeypatch.setattr(rec, "wakeup_requote", lambda *a, **k: 0)
    monkeypatch.setattr(rec, "alert_new_high", lambda *a, **k: 0)
    monkeypatch.setattr(today.sys, "argv", ["prog", "--json", str(out_path)])
    today.main()
    return json.loads(out_path.read_text())


def test_json_feed_is_high_only(monkeypatch, tmp_path):
    bets = [
        _bet("High", "h2h", 0.05),
        _bet("Med", "h2h", 0.04, away="CCC", home="DDD"),
        _bet("Low", "total", 0.03, away="EEE", home="FFF", side="Over"),
        _bet("High", "spread", 0.02, away="GGG", home="HHH"),
    ]
    feed = _run_json(monkeypatch, tmp_path, bets)
    tiers = {b["tier"] for b in feed["bets"]}
    assert tiers == {"High"}, f"non-High leaked into the +EV tab feed: {tiers}"
    assert len(feed["bets"]) == 2


def test_json_feed_empty_when_no_high(monkeypatch, tmp_path):
    bets = [_bet("Med", "h2h", 0.04), _bet("Low", "total", 0.03, side="Over")]
    feed = _run_json(monkeypatch, tmp_path, bets)
    assert feed["bets"] == []
