def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    db = adb.connect(); adb.init_schema(db)
    return adb, db


def test_mute_rule_crud_helpers(tmp_path, monkeypatch):
    adb, db = _db(tmp_path, monkeypatch)
    rid = adb.add_mute_rule(db, "player", "Nico Hoerner", "2026-06-13T00:00:00Z")
    rules = adb.list_mute_rules(db)
    assert any(r["id"] == rid and r["field"] == "player" and r["value"] == "Nico Hoerner" for r in rules)
    assert ("player", "Nico Hoerner") in adb.active_mute_rules(db)
    adb.delete_mute_rule(db, rid)
    assert adb.list_mute_rules(db) == []


def test_is_muted_row_matches_field_to_value(tmp_path):
    import softbook_app_feed as feed
    row = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
           "side": "Over", "point": 1.5, "player": "Nico Hoerner"}
    assert feed._is_muted_row(row, [("player", "Nico Hoerner")]) is True
    assert feed._is_muted_row(row, [("market", "batter_total_bases")]) is True
    assert feed._is_muted_row(row, [("event", "E1")]) is True
    assert feed._is_muted_row(row, [("sport", "basketball_nba")]) is False
    assert feed._is_muted_row(row, []) is False


def test_rules_endpoints(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("APP_API_TOKEN", "test-token")
    os.environ.pop("CF_ACCESS_TEAM_DOMAIN", None)
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    adb.init_schema(adb.connect())
    import softbook_app_api as api
    from fastapi.testclient import TestClient
    c = TestClient(api.app); c.headers.update({"Authorization": "Bearer test-token"})

    r = c.post("/rules", json={"field": "player", "value": "Nico Hoerner"})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert any(x["id"] == rid for x in c.get("/rules").json()["rules"])
    assert c.post("/rules", json={"field": "bogus", "value": "x"}).status_code == 400
    assert c.delete(f"/rules/{rid}").status_code == 200
    assert c.get("/rules").json()["rules"] == []
