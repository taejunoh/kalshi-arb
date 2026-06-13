import os
import sqlite3
import pytest

os.environ.setdefault("APP_API_TOKEN", "test-token")
os.environ.pop("CF_ACCESS_TEAM_DOMAIN", None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    paper = str(tmp_path / "paper.db")
    ev = str(tmp_path / "ev.db")
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", paper)
    monkeypatch.setenv("SOFTBOOK_EV_DB", ev)
    edb = sqlite3.connect(ev)
    edb.execute(
        "CREATE TABLE softbook_ev(recorded_at TEXT, sport TEXT, event_id TEXT, "
        "commence_time TEXT, home TEXT, away TEXT, market TEXT, side TEXT, point REAL, "
        "player TEXT, tier TEXT)")
    edb.execute(
        "INSERT INTO softbook_ev(recorded_at, sport, event_id, commence_time, home, away) "
        "VALUES (datetime('now'), 'baseball_mlb', 'EVT123', datetime('now','+2 hours'), "
        "'Colorado Rockies', 'Chicago Cubs')")
    edb.commit(); edb.close()

    import softbook_app_db as adb
    adb.init_schema(adb.connect())

    import softbook_app_api as api
    monkeypatch.setattr(api.scan, "parse_bet_slip", lambda *a, **k: {
        "sport": "baseball_mlb", "home": "Colorado Rockies", "away": "Chicago Cubs",
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Nico Hoerner", "book": "FanDuel", "fill_odds": 2.0, "stake": 2.0,
        "label": "Nico Hoerner TB Over 1.5", "notes": "single leg",
    })
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    c.headers.update({"Authorization": "Bearer test-token"})
    return c


def test_scan_returns_draft_and_matches_event(client):
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0", "media_type": "image/jpeg"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["bet"]["event_id"] == "EVT123"
    assert body["bet"]["source"] == "scan"
    assert body["bet"]["player"] == "Nico Hoerner"
    assert body["bet"]["fill_odds"] == 2.0


def test_scan_unmatched_event_still_returns_draft(client, monkeypatch):
    import softbook_app_api as api
    monkeypatch.setattr(api.scan, "parse_bet_slip", lambda *a, **k: {
        "sport": None, "home": "Some Team", "away": "Other Team",
        "market": "h2h", "side": "Some Team", "point": None, "player": None,
        "book": "DraftKings", "fill_odds": 1.8, "stake": None,
        "label": "Some Team ML", "notes": "",
    })
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["bet"]["event_id"] is None
    assert body["bet"]["source"] == "scan"


def test_scan_parse_failure_is_422(client, monkeypatch):
    import softbook_app_api as api
    def _boom(*a, **k):
        raise ValueError("unreadable")
    monkeypatch.setattr(api.scan, "parse_bet_slip", _boom)
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0"})
    assert r.status_code == 422
