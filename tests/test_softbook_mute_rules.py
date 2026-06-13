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
