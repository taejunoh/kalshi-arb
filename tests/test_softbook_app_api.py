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
    # 최소/본전 bars ride along on the pick so the card never derives them (they
    # encode EV_MIN, which is policy). Seeded ev=0.074 @ 2.2 -> fair≈0.4882.
    import softbook_ev_core as core
    assert (p["min_odds"], p["be_odds"]) == core.odds_bars(0.074, 2.2)


def test_picks_first_entry_flag(client):
    """first_entry True iff the (event,market,player)'s EARLIEST snapshot was High;
    should_bet = first-entry GAME only (props + late-crossers are False)."""
    ev = sqlite3.connect(os.environ["SOFTBOOK_EV_DB"])
    ev.execute("DELETE FROM softbook_ev")
    latest = ev.execute("SELECT datetime('now')").fetchone()[0]
    earlier = ev.execute("SELECT datetime('now','-40 minutes')").fetchone()[0]
    cols = ("(recorded_at,sport,event_id,commence_time,home,away,market,side,point,"
            "player,pinnacle_fair_prob,pinnacle_price,best_soft_book,best_soft_price,"
            "snapshot_ev,n_books,n_ev,conf,tier)")
    q = f"INSERT INTO softbook_ev {cols} VALUES ({','.join('?' * 19)})"
    # E1: a single High snapshot -> first-entry.
    ev.execute(q, (latest, "baseball_mlb", "E1", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "batter_total_bases", "Over", 1.5, "Brandon Nimmo", 0.45, 2.0,
                   "theScore", 2.2, 0.074, 9, 5, 60, "High"))
    # E2: earliest snapshot was below ML's bet tier -> late-crosser.
    ev.execute(q, (earlier, "baseball_mlb", "E2", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "h2h", "NYM", 0, "", 0.4, 2.0, "FanDuel", 2.1, 0.03, 8, 5, 55, "Low"))
    ev.execute(q, (latest, "baseball_mlb", "E2", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "h2h", "NYM", 0, "", 0.4, 2.0, "FanDuel", 2.1, 0.03, 8, 5, 55, "Med"))
    # E3: GAME first-entry (single High snapshot) -> first_entry AND should_bet True.
    ev.execute(q, (latest, "baseball_mlb", "E3", "2099-01-01T00:00:00Z", "MIA", "NYM",
                   "spread", "NYM", -1.5, "", 0.45, 2.0, "theScore", 2.1, 0.05, 9, 5, 60, "High"))
    ev.commit(); ev.close()
    picks = client.get("/picks", headers={"Authorization": "Bearer secret"}).json()["picks"]
    by_event = {p["event_id"]: p for p in picks}
    assert set(by_event) == {"E1", "E2", "E3"}
    assert by_event["E1"]["first_entry"] is True and by_event["E1"]["should_bet"] is False   # prop
    assert by_event["E2"]["first_entry"] is False and by_event["E2"]["should_bet"] is False  # late game
    assert by_event["E3"]["first_entry"] is True and by_event["E3"]["should_bet"] is True    # first-entry game


def test_picks_returns_recently_expired(client):
    """A High pick whose game already started is returned under 'expired' (not
    'picks'), so an alerted pick stays findable after commence. Bounded to recent
    commence — a long-ago-started game is dropped from the list (still counted)."""
    ev = sqlite3.connect(os.environ["SOFTBOOK_EV_DB"])
    ev.execute("DELETE FROM softbook_ev")
    cols = ("(recorded_at,sport,event_id,commence_time,home,away,market,side,point,"
            "player,pinnacle_fair_prob,pinnacle_price,best_soft_book,best_soft_price,"
            "snapshot_ev,n_books,n_ev,conf,tier)")
    q = f"INSERT INTO softbook_ev {cols} VALUES ({','.join('?' * 19)})"
    now = ev.execute("SELECT datetime('now')").fetchone()[0]
    # commence_time must be Z-suffixed (aware) to match production + the API's now.
    started = ev.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now','-2 hours')").fetchone()[0]
    long_ago = ev.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 hours')").fetchone()[0]
    # E1: started 2h ago -> expired AND inside the 24h window -> in the list.
    ev.execute(q, (now, "baseball_kbo", "E1", started, "Kiwoom Heroes", "Lotte Giants",
                   "h2h", "Lotte Giants", 0, "", 0.42, 2.0, "BetMGM", 2.35, 0.071, 8, 5, 60, "Med"))
    # E2: started 30h ago -> expired but OUTSIDE the window -> counted, not listed.
    ev.execute(q, (now, "baseball_mlb", "E2", long_ago, "MIA", "NYM",
                   "h2h", "NYM", 0, "", 0.45, 2.0, "FanDuel", 2.1, 0.05, 8, 5, 60, "Med"))
    ev.commit(); ev.close()
    r = client.get("/picks", headers={"Authorization": "Bearer secret"}).json()
    assert r["picks"] == []                          # neither is active (both commenced)
    exp_ids = [p["id"] for p in r["expired"]]
    assert "E1|h2h|Lotte Giants|0.0|" in exp_ids      # recent expired stays findable
    assert all(not pid.startswith("E2|") for pid in exp_ids)  # stale expired dropped
    assert r["counts"].get("expired") == 2            # both classified expired
    assert r["expired"][0]["should_bet"] is True      # enriched like active picks


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
                        lambda sport, eid, key, markets=None, links=False: (ev_payload, 4))
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


def test_mybets_groups_open_and_calendar(client):
    # place a bet, then settle one manually in the DB
    body = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "x", "book": "theScore", "fill_odds": 2.2, "stake": 2.0}
    client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-06-12T22:00:00+00:00'")
    pdb.commit(); pdb.close()
    r = client.get("/mybets", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["calendar"]["2026-06-12"] == 2.4
    assert isinstance(body["open"], list)


def test_settings_roundtrip(client):
    put = client.put("/settings", json={"min_ev": 0.03, "sports": ["baseball_mlb"],
                                        "muted_books": ["BallyBet"], "sort": "time"},
                     headers={"Authorization": "Bearer secret"})
    assert put.status_code == 200
    got = client.get("/settings", headers={"Authorization": "Bearer secret"}).json()
    assert got["min_ev"] == 0.03 and got["sort"] == "time"
    assert got["muted_books"] == ["BallyBet"]


def test_push_register_upserts_token(client):
    for _ in range(2):
        r = client.post("/push/register", json={"token": "ExponentPushToken[abc]"},
                        headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
    import softbook_app_db as adb2
    pdb = adb2.connect()
    n = pdb.execute("SELECT count(*) FROM push_token").fetchone()[0]
    pdb.close()
    assert n == 1
