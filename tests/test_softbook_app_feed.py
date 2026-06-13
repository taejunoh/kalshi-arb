# tests/test_softbook_app_feed.py
"""Feed composition: turn the latest softbook_ev scan's High picks into the app
feed, applying pick_state overrides (bet/dismissed leave the feed), cooled/
expired classification, and filter_prefs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_feed as feed  # noqa: E402

NOW = "2026-06-12T18:00:00+00:00"


def _snap(event_id, market, side, point, player, ev, book="theScore",
          recorded_at=NOW, ct="2026-06-12T23:10:00Z", conf=60, n_books=9,
          sport="baseball_mlb", home="MIA", away="NYM", label=None):
    return {"event_id": event_id, "market": market, "side": side, "point": point,
            "player": player, "snapshot_ev": ev, "best_soft_book": book,
            "best_soft_price": 2.2, "recorded_at": recorded_at, "commence_time": ct,
            "conf": conf, "n_books": n_books, "sport": sport, "home": home,
            "away": away, "label": label or f"{player} {side} {point}"}


def test_only_latest_scan_high_picks_are_active():
    rows = [
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at="2026-06-12T17:40:00+00:00"),
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at=NOW),  # latest -> active
        _snap("E2", "h2h", "NYM", 0, "", 0.03, recorded_at=NOW),
    ]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    ids = {(p["event_id"], p["status"]) for p in out}
    assert ("E1", "active") in ids and ("E2", "active") in ids
    assert len([p for p in out if p["event_id"] == "E1"]) == 1  # deduped to latest


def test_bet_and_dismissed_leave_the_active_feed():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05),
            _snap("E2", "h2h", "NYM", 0, "", 0.03)]
    states = {("E1", "total", "Over", 8.5, ""): "bet",
              ("E2", "h2h", "NYM", 0.0, ""): "dismissed"}
    out = feed.compose(rows, states=states, prefs=None, now=NOW)
    assert [p for p in out if p["status"] == "active"] == []
    assert {p["status"] for p in out} == {"bet", "dismissed"}


def test_cooled_when_high_earlier_but_absent_from_latest_scan():
    rows = [
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at="2026-06-12T17:40:00+00:00"),
        _snap("E9", "h2h", "NYM", 0, "", 0.03, recorded_at=NOW),  # defines latest scan
    ]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    e1 = next(p for p in out if p["event_id"] == "E1")
    assert e1["status"] == "cooled"


def test_expired_when_commence_time_passed():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05, ct="2026-06-12T17:00:00Z")]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    assert out[0]["status"] == "expired"


def test_min_ev_filter_drops_low_ev_actives():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05),
            _snap("E2", "h2h", "NYM", 0, "", 0.015)]
    prefs = {"min_ev": 0.03, "sports_json": None, "muted_books_json": None, "sort": "ev"}
    out = feed.compose(rows, states={}, prefs=prefs, now=NOW)
    active = [p for p in out if p["status"] == "active"]
    assert [p["event_id"] for p in active] == ["E1"]


def test_muted_book_filter_drops_active():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05, book="theScore")]
    prefs = {"min_ev": 0, "sports_json": None,
             "muted_books_json": '["theScore"]', "sort": "ev"}
    out = feed.compose(rows, states={}, prefs=prefs, now=NOW)
    assert [p for p in out if p["status"] == "active"] == []


def test_sort_by_ev_desc():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.03),
            _snap("E2", "h2h", "NYM", 0, "", 0.07)]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    active = [p for p in out if p["status"] == "active"]
    assert [p["event_id"] for p in active] == ["E2", "E1"]
