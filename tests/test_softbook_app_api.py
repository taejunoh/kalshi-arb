# tests/test_softbook_app_api.py
"""FastAPI app endpoints over the softbook pipeline + app tables. Uses an
in-memory-ish seeded SQLite copy via env DB paths and FastAPI's TestClient."""
import json
import os
import sqlite3
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    ev_db = tmp_path / "ev.db"
    paper_db = tmp_path / "paper.db"
    # seed softbook_ev with one High pick on the latest scan
    e = sqlite3.connect(ev_db)
    e.execute("""CREATE TABLE softbook_ev(
        recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
        home TEXT, away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        pinnacle_fair_prob REAL, pinnacle_price REAL, best_soft_book TEXT,
        best_soft_price REAL, snapshot_ev REAL, n_books INTEGER, n_ev INTEGER,
        conf REAL, tier TEXT)""")
    # recorded_at must be recent: the API filters datetime(recorded_at) >=
    # datetime('now','-2 days'), so a fixed past date would break this test over
    # time. Use SQLite's datetime('now') so it is always inside the window.
    e.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
              "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
              "'batter_total_bases','Over',1.5,'Brandon Nimmo',0.45,2.0,"
              "'theScore',2.2,0.074,9,5,60,'High')")
    e.commit(); e.close()
    monkeypatch.setenv("SOFTBOOK_EV_DB", str(ev_db))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(paper_db))
    monkeypatch.setenv("APP_API_TOKEN", "secret")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")  # disables JWT check in tests
    import importlib
    import softbook_app_api
    importlib.reload(softbook_app_api)
    return TestClient(softbook_app_api.app)


def test_unauthorized_without_bearer(client):
    assert client.get("/picks").status_code == 401


def test_picks_returns_active_high(client):
    r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    picks = r.json()["picks"]
    assert len(picks) == 1
    p = picks[0]
    assert p["status"] == "active" and p["player"] == "Brandon Nimmo"
    assert p["id"] == "E1|batter_total_bases|Over|1.5|Brandon Nimmo"


def test_pick_detail_by_id(client):
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.get(f"/picks/{pid}", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["label"] == "Brandon Nimmo TB Over 1.5" or r.json()["side"] == "Over"


def test_refresh_returns_fresh_quotes_and_deeplinks(client, monkeypatch):
    import softbook_app_api
    # stub the Odds API single-event call
    ev_payload = {"home_team": "MIA", "away_team": "NYM", "bookmakers": [
        {"key": "pinnacle", "markets": [{"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5, "price": 1.9},
            {"name": "Under", "description": "Brandon Nimmo", "point": 1.5, "price": 1.9}]}]},
        {"key": "fanduel", "markets": [{"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5, "price": 2.2,
             "link": "https://sportsbook.fanduel.com/addToBetslip?marketId=1&selectionId=2"}]}]},
    ]}
    monkeypatch.setattr(softbook_app_api.core, "event_odds",
                        lambda sport, eid, key, markets=None: (ev_payload, 4))
    monkeypatch.setattr(softbook_app_api.core, "read_key", lambda: "k")
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.get(f"/picks/{pid}/refresh", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = r.json()
    books = {b["book"]: b for b in body["books"]}
    assert "FanDuel" in books
    fd = books["FanDuel"]
    assert fd["odds"] == 2.2
    assert fd["deeplink"]["tier"] == "prefill"
    assert fd["deeplink"]["url"].startswith("https://sportsbook.fanduel.com/addToBetslip")
    assert body["credits"] == 4


def test_post_bet_records_and_marks_pick_bet(client):
    body = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
            "commence_time": "2099-01-01T00:00:00Z", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
            "rec_odds": 2.2, "fill_odds": 2.25, "stake": 2.0, "source": "one_tap"}
    r = client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.json()["id"] >= 1
    # pick now excluded from the active feed
    feed_r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert feed_r.json()["counts"].get("bet") == 1
    assert feed_r.json()["picks"] == []


def test_dismiss_marks_pick_dismissed(client):
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.post(f"/picks/{pid}/dismiss", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    feed_r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert feed_r.json()["counts"].get("dismissed") == 1
