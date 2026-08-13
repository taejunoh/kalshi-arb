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


def test_skip_picks_never_reach_the_feed(client):
    """The seeded pick is a prop — a SKIP. Telegram has suppressed those since
    7/22; the app feed was the last surface still rendering a card that says
    "do not bet this" (user, 2026-08-05). It stays reachable by id."""
    r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["picks"] == []
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    assert client.get(f"/picks/{pid}",
                      headers={"Authorization": "Bearer secret"}).status_code == 200


def test_feed_pick_carries_the_odds_bars(client):
    """Bars ride along on the pick so the card never derives them (they encode
    EV_MIN, which is policy). Seeded ev=0.074 @ 2.2 -> fair≈0.4882."""
    ev = sqlite3.connect(os.environ["SOFTBOOK_EV_DB"])
    ev.execute("DELETE FROM softbook_ev")
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'spread','NYM',-1.5,'',0.45,2.0,"
               "'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit(); ev.close()
    picks = client.get("/picks", headers={"Authorization": "Bearer secret"}).json()["picks"]
    assert len(picks) == 1
    p = picks[0]
    assert p["status"] == "active" and p["should_bet"] is True
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
    # E1 (prop) and E2 (late-crosser) are both SKIP and no longer reach the feed.
    assert set(by_event) == {"E3"}
    assert by_event["E3"]["first_entry"] is True and by_event["E3"]["should_bet"] is True
    # …but the verdict itself is unchanged — both are still reachable by id.
    for pid, fe in (("E1|batter_total_bases|Over|1.5|Brandon Nimmo", True),
                    ("E2|h2h|NYM|0.0|", False)):
        d = client.get(f"/picks/{pid}", headers={"Authorization": "Bearer secret"}).json()
        assert d["first_entry"] is fe and d["should_bet"] is False


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


def _place_bet(client, **over):
    body = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
            "fill_odds": 2.2, "stake": 2.0}
    body.update(over)
    r = client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    return r.json()["id"]


def test_mybets_splits_open_settled_and_not_placed(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now') WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert body["open"] == []
    assert [b["id"] for b in body["settled"]] == [bid]
    assert body["settled"][0]["pnl"] == 2.4 and body["settled"][0]["won"] == 1
    assert body["not_placed"] == []


def test_mybets_excludes_not_placed_from_calendar_and_lists(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-06-12T22:00:00+00:00', not_placed=1,"
                " not_placed_at='2026-08-12T10:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert body["calendar"] == {}                       # PnL no longer counted
    assert body["settled"] == [] and body["open"] == []
    assert [b["id"] for b in body["not_placed"]] == [bid]


def test_mybets_excludes_unsettled_not_placed_from_open(client):
    """Regression: disowning a bet BEFORE it settles (the common case) must pull
    it out of open, not just out of settled. The prior not_placed test only
    covered an already-settled bet, so it never exercised the `open` bucket's
    `AND not_placed=0` clause — mutation testing showed deleting that clause
    left every test green."""
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET not_placed=1,"
                " not_placed_at='2026-08-12T10:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert body["open"] == []
    assert [b["id"] for b in body["not_placed"]] == [bid]


def _settle(bid, settled_at, pnl=2.4):
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=?, settled_at=? WHERE id=?",
                (pnl, settled_at, bid))
    pdb.commit(); pdb.close()


def _calendar(client):
    return client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()["calendar"]


def test_calendar_files_an_evening_settlement_on_the_day_the_user_lived_it(client):
    """The user is in New York. A 10pm ET Tuesday game settles at 02:00Z
    Wednesday, and keying the calendar on the UTC date filed it under Wednesday
    — a day the user did not experience. Measured on production, 80 of 206
    settled bets (39%) landed on the wrong day this way."""
    _settle(_place_bet(client), "2026-08-13T02:00:00+00:00")
    assert _calendar(client) == {"2026-08-12": 2.4}


def test_calendar_leaves_a_settlement_already_inside_its_eastern_day_alone(client):
    """Mid-afternoon UTC is the same calendar day in both frames. The
    re-attribution must move only the rows that actually straddle midnight."""
    _settle(_place_bet(client), "2026-08-12T18:00:00+00:00")
    assert _calendar(client) == {"2026-08-12": 2.4}


def test_calendar_follows_the_dst_transition_not_a_fixed_offset(client):
    """The reason this conversion is Python + zoneinfo and not
    date(settled_at,'-4 hours') in SQL: SQLite has no timezone database, so a
    fixed offset is an hour wrong for half the year.

    Both instants below sit in the 04:00-04:59Z window — the only hour where a
    -4 and a -5 offset disagree about the DATE, so this is the pair that
    actually discriminates. (Note: an evening instant such as 2026-01-15T02:00Z
    does NOT — it yields 2026-01-14 under EST, EDT and a fixed -4 alike, so it
    proves ET-vs-UTC but says nothing about DST.)"""
    _settle(_place_bet(client), "2026-08-13T04:30:00+00:00")     # EDT, UTC-4
    assert _calendar(client) == {"2026-08-13": 2.4}, "summer must use UTC-4"

    import softbook_app_db as adb2
    pdb = adb2.connect(); pdb.execute("DELETE FROM app_bet"); pdb.commit(); pdb.close()

    _settle(_place_bet(client), "2026-01-15T04:30:00+00:00")     # EST, UTC-5
    assert _calendar(client) == {"2026-01-14": 2.4}, "winter must use UTC-5"


def test_calendar_keys_an_evening_winter_settlement_in_eastern(client):
    """The user-story instant from the winter half: 9pm ET Jan 14 settles at
    02:00Z Jan 15. Keeps the EST leg of the user-visible behavior pinned even
    though it cannot tell EST from a fixed -4 (the test above does that)."""
    _settle(_place_bet(client), "2026-01-15T02:00:00+00:00")
    assert _calendar(client) == {"2026-01-14": 2.4}


def test_calendar_sums_two_utc_days_into_one_eastern_day(client):
    """Grouping moved from SQL's GROUP BY to Python, so the fold itself needs a
    test: 23:00Z and 02:00Z the next day are one Eastern evening and must land
    on one key with one total, not two keys."""
    _settle(_place_bet(client), "2026-08-12T23:00:00+00:00", pnl=1.5)
    _settle(_place_bet(client), "2026-08-13T02:00:00+00:00", pnl=2.4)
    assert _calendar(client) == {"2026-08-12": 3.9}


def test_calendar_accepts_a_trailing_z_and_a_naive_timestamp(client):
    """settled_at is written as datetime.now(timezone.utc).isoformat() ('+00:00'),
    but rows migrated from other sources spell UTC differently. A 'Z' suffix and
    a bare naive timestamp both mean UTC here — every writer in this repo is UTC
    — so both must convert, not fall through to the raw date."""
    _settle(_place_bet(client), "2026-08-13T02:00:00Z", pnl=1.0)
    assert _calendar(client) == {"2026-08-12": 1.0}

    import softbook_app_db as adb2
    pdb = adb2.connect(); pdb.execute("DELETE FROM app_bet"); pdb.commit(); pdb.close()

    _settle(_place_bet(client), "2026-08-13 02:00:00", pnl=1.0)
    assert _calendar(client) == {"2026-08-12": 1.0}


def test_one_unparseable_settled_at_does_not_take_down_the_endpoint(client):
    """A single corrupt row must not 500 the whole ledger screen. It keeps its
    PnL in the calendar under its leading date when it has one: the calendar is
    a sum of money the user can reconcile against the settled list below it, and
    silently dropping a row makes that total disagree for no visible reason.
    Filed a day off is the error we already had; vanished money is a new one."""
    _settle(_place_bet(client), "2026-08-13T02:00:00+00:00", pnl=2.4)
    _settle(_place_bet(client), "2026-08-20T99:99:99", pnl=5.0)
    r = client.get("/mybets", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["calendar"] == {"2026-08-12": 2.4, "2026-08-20": 5.0}


def test_a_settled_at_with_no_date_in_it_is_dropped_from_the_calendar(client):
    """…but when there is no day to file it under at all, there is no honest
    key to invent. Drop that row and keep serving the rest."""
    _settle(_place_bet(client), "2026-08-13T02:00:00+00:00", pnl=2.4)
    _settle(_place_bet(client), "not a timestamp", pnl=5.0)
    r = client.get("/mybets", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["calendar"] == {"2026-08-12": 2.4}


def test_et_month_is_eastern_at_the_month_boundary(client):
    """The month the app labels '이번 달'. Computed server-side so the timezone
    decision lives in one place with a real tz database behind it, rather than
    in Hermes where full-ICU (and therefore Intl's timeZone option) is not
    guaranteed. Fixed instants, never wall-clock: host- and date-independent."""
    import datetime as dt
    import softbook_app_api as api
    utc = dt.timezone.utc
    # 2026-08-31 21:00 ET — UTC has already rolled into September.
    assert api._et_month(dt.datetime(2026, 9, 1, 1, 0, tzinfo=utc)) == "2026-08"
    # 2026-07-31 23:00 ET — same shape one month earlier.
    assert api._et_month(dt.datetime(2026, 8, 1, 3, 0, tzinfo=utc)) == "2026-07"
    # 2025-12-31 23:30 EST — inside the 04:00-04:59Z window, so a fixed -4
    # offset would answer "2026-01" and roll the year over a day early.
    assert api._et_month(dt.datetime(2026, 1, 1, 4, 30, tzinfo=utc)) == "2025-12"


def test_mybets_reports_the_current_eastern_month(client, monkeypatch):
    """The endpoint must actually serve that month, and serve the ET one."""
    import datetime as dt
    import re
    from zoneinfo import ZoneInfo
    import softbook_app_api as api
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert re.fullmatch(r"\d{4}-\d{2}", body["month"]), body["month"]
    assert body["month"] == dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
    # …and that it is the ET helper's answer, not a UTC month that happens to
    # agree right now (they differ only 04:00-04:59Z, so the assertion above is
    # vacuous most of the day on a UTC CI host). Bites on every host, any hour.
    monkeypatch.setattr(api, "_et_month", lambda now=None: "1999-01")
    assert client.get("/mybets",
                      headers={"Authorization": "Bearer secret"}).json()["month"] == "1999-01"


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


def test_patch_bet_updates_the_three_editable_fields(client):
    bid = _place_bet(client)
    r = client.patch(f"/bets/{bid}", json={"book": "DraftKings", "stake": 25.0,
                                           "fill_odds": 2.05},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    row = body["open"][0]
    assert (row["book"], row["stake"], row["fill_odds"]) == ("DraftKings", 25.0, 2.05)


def test_patch_bet_rejects_fields_that_would_make_it_a_different_bet(client):
    bid = _place_bet(client)
    for payload in ({"market": "h2h"}, {"rec_odds": 9.9}, {"pnl": 100.0}):
        r = client.patch(f"/bets/{bid}", json=payload,
                         headers={"Authorization": "Bearer secret"})
        assert r.status_code == 422, payload


def test_patch_bet_on_a_settled_row_is_conflict(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, pnl=2.4 WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    r = client.patch(f"/bets/{bid}", json={"stake": 25.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 409


def test_patch_bet_on_a_not_placed_row_is_conflict(client):
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers={"Authorization": "Bearer secret"})
    r = client.patch(f"/bets/{bid}", json={"stake": 25.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 409


def test_patch_bet_rejects_explicit_null_stake(client):
    """Regression: exclude_unset=True only tells 'omitted' from 'set' — it does
    not stop an explicit null from getting through. {"stake": null} used to
    write stake=NULL, and settle's pnl = stake * (fill_odds - 1) has no None
    guard, so that one row's TypeError aborted the whole settle batch (settle
    commits once after its loop, not per-row)."""
    bid = _place_bet(client)
    r = client.patch(f"/bets/{bid}", json={"stake": None},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 422
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    row = body["open"][0]
    assert row["stake"] == 2.0


def test_patch_bet_rejects_explicit_null_fill_odds(client):
    """Regression: same hole as stake — fill_odds also feeds settle's
    pnl = stake * (fill_odds - 1) arithmetic with no None guard."""
    bid = _place_bet(client)
    r = client.patch(f"/bets/{bid}", json={"fill_odds": None},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 422


def test_patch_bet_allows_explicit_null_book(client):
    """book is descriptive metadata, not settle arithmetic — POST /bets already
    accepts a bet with no book, so PATCH clearing it back to null must still
    be allowed."""
    bid = _place_bet(client)
    r = client.patch(f"/bets/{bid}", json={"book": None},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_unmark_returns_the_pick_to_the_active_feed(client):
    """The whole point: a mis-marked pick becomes actionable again."""
    ev = sqlite3.connect(os.environ["SOFTBOOK_EV_DB"])
    ev.execute("DELETE FROM softbook_ev")
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'spread','NYM',-1.5,'',0.45,2.0,'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit(); ev.close()
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client, market="spread", side="NYM", point=-1.5, player="",
                     label="NYM -1.5")
    assert client.get("/picks", headers=h).json()["picks"] == []      # hidden
    r = client.post(f"/bets/{bid}/unmark", headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "pnl_delta": None, "settled_day": None}
    picks = client.get("/picks", headers=h).json()["picks"]
    assert [p["id"] for p in picks] == ["E1|spread|NYM|-1.5|"]         # back


def _seed_spread_pick(ev_path):
    """Replace the seeded prop with a bettable GAME pick (props are SKIP and never
    reach the feed), so 'is this pick back in the feed?' is answerable."""
    ev = sqlite3.connect(ev_path)
    ev.execute("DELETE FROM softbook_ev")
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'spread','NYM',-1.5,'',0.45,2.0,'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit(); ev.close()


def test_unmarking_one_of_two_bets_on_a_pick_keeps_it_hidden(client):
    """Two owned bets on one pick is reachable through a documented flow: one-tap
    the pick from the feed, then also scan the paper slip. Unmarking one of them
    must NOT drop the pick_state 'bet' override — doing so puts a one-tap-bettable
    card back in the feed while the second bet is still open, which is exactly the
    double-bet that cost real money on 2026-08-06 (Seattle ML bet twice, 50
    minutes apart, at a worse price) and that the Telegram '✅ 베팅 완료' stamp
    was built to prevent. The override drops only when the LAST one is unmarked."""
    _seed_spread_pick(os.environ["SOFTBOOK_EV_DB"])
    h = {"Authorization": "Bearer secret"}
    kw = dict(market="spread", side="NYM", point=-1.5, player="", label="NYM -1.5")
    b1 = _place_bet(client, **kw)
    b2 = _place_bet(client, **kw)
    assert client.post(f"/bets/{b1}/unmark", headers=h).status_code == 200
    feed = client.get("/picks", headers=h).json()
    assert feed["picks"] == []                       # still hidden
    assert feed["counts"].get("bet") == 1            # still marked as bet
    # …and the last one unmarked does return it.
    assert client.post(f"/bets/{b2}/unmark", headers=h).status_code == 200
    picks = client.get("/picks", headers=h).json()["picks"]
    assert [p["id"] for p in picks] == ["E1|spread|NYM|-1.5|"]


def test_unmark_of_a_settled_bet_reports_the_calendar_change(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-08-09T22:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    r = client.post(f"/bets/{bid}/unmark", headers=h).json()
    assert r["pnl_delta"] == -2.4 and r["settled_day"] == "2026-08-09"


def test_unmark_is_idempotent(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    for _ in range(2):
        assert client.post(f"/bets/{bid}/unmark", headers=h).status_code == 200
    import softbook_app_db as adb2
    pdb = adb2.connect()
    n = pdb.execute("SELECT count(*) FROM pick_state").fetchone()[0]
    rows = pdb.execute("SELECT count(*) FROM app_bet").fetchone()[0]
    pdb.close()
    assert n == 0 and rows == 1          # state gone, ledger row still there


def test_unmark_twice_on_a_settled_bet_reports_the_change_only_once(client):
    """Regression: unmark_bet's guard `if row["not_placed"] or not row["settled"]`
    relies on `row` being the PRE-mutation snapshot, so a repeat POST must see
    not_placed already 1 and report no further change. Deleting
    `row["not_placed"] or` from that guard left every existing unmark test
    green — the calendar test POSTs once, and the idempotency test POSTs twice
    but only checks status_code on an unsettled bet, so pnl_delta is trivially
    null either way. Under that regression a duplicate tap or a retried
    request reports pnl_delta a second time, and a client applying it to a
    cached calendar double-subtracts."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-08-09T22:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    r1 = client.post(f"/bets/{bid}/unmark", headers=h).json()
    assert r1["pnl_delta"] == -2.4 and r1["settled_day"] == "2026-08-09"
    r2 = client.post(f"/bets/{bid}/unmark", headers=h).json()
    assert r2["pnl_delta"] is None and r2["settled_day"] is None


def test_unmark_never_deletes_the_ledger_row(client):
    """LEDGER RULE. If this ever fails, the feature has become a delete button."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    row = adb2.get_app_bet(pdb, bid)
    pdb.close()
    assert row is not None and row["not_placed"] == 1
    assert row["not_placed_at"] is not None
    assert row["stake"] == 2.0 and row["fill_odds"] == 2.2   # not repriced


def test_delete_unmark_reports_the_restored_calendar_change(client):
    """The two directions of the toggle must be symmetric. POST goes out of its
    way to honor "a past number must never shift in silence" — DELETE puts the
    very same PnL BACK into that day, so it owes the caller the same disclosure
    (the app names it in a confirm dialog). Opposite sign of the POST's delta."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-08-09T22:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    assert client.post(f"/bets/{bid}/unmark", headers=h).json()["pnl_delta"] == -2.4
    r = client.delete(f"/bets/{bid}/unmark", headers=h).json()
    assert r == {"ok": True, "pnl_delta": 2.4, "settled_day": "2026-08-09"}


def test_delete_unmark_twice_reports_the_change_only_once(client):
    """Mirrors test_unmark_twice_on_a_settled_bet_...: `row` is the PRE-mutation
    snapshot, so a repeat DELETE must see not_placed already 0 and report no
    further change. A client applying a repeated delta to a cached calendar
    would double-ADD it."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-08-09T22:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    client.post(f"/bets/{bid}/unmark", headers=h)
    assert client.delete(f"/bets/{bid}/unmark", headers=h).json()["pnl_delta"] == 2.4
    r2 = client.delete(f"/bets/{bid}/unmark", headers=h).json()
    assert r2["pnl_delta"] is None and r2["settled_day"] is None


def test_delete_unmark_restores_the_bet_state(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)
    r = client.delete(f"/bets/{bid}/unmark", headers=h)
    assert r.status_code == 200
    # An unsettled bet moves no past day, so there is nothing to disclose.
    assert r.json() == {"ok": True, "pnl_delta": None, "settled_day": None}
    assert client.get("/picks", headers=h).json()["counts"].get("bet") == 1
    body = client.get("/mybets", headers=h).json()
    assert body["not_placed"] == [] and [b["id"] for b in body["open"]] == [bid]


def test_delete_unmark_on_a_never_unmarked_bet_is_a_noop(client):
    """Regression: remark_bet's `if row["not_placed"]:` guard makes DELETE a
    no-op when the bet was never unmarked. The only existing DELETE test
    (test_delete_unmark_restores_the_bet_state) always POSTs an unmark first,
    so nothing pins this path — a DELETE on a bet that was never disowned is
    correct by inspection but unpinned. If the guard is removed, remark_bet
    mutates unconditionally: harmless for not_placed (already 0) but it
    re-touches the pick_state 'bet' row's updated_at, which this test catches
    even though the row's presence and not_placed both look unchanged."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    before = pdb.execute(
        "SELECT updated_at FROM pick_state WHERE state='bet'").fetchone()[0]
    pdb.close()
    r = client.delete(f"/bets/{bid}/unmark", headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "pnl_delta": None, "settled_day": None}
    pdb = adb2.connect()
    row = adb2.get_app_bet(pdb, bid)
    n, after = pdb.execute(
        "SELECT count(*), max(updated_at) FROM pick_state WHERE state='bet'"
    ).fetchone()
    pdb.close()
    assert row["not_placed"] == 0
    assert n == 1 and after == before


def test_delete_unmark_does_not_overwrite_a_dismissed_pick_state(client):
    """The two halves of one toggle must agree about 'dismissed'. clear_bet_state
    is explicitly documented never to touch a dismissed row ("a separate user
    preference"), but remark_bet used to set_pick_state(..., 'bet') flat out — so
    dismissing a pick while its bet is disowned and then 되돌리기 rewrote the
    user's dismissal into 'bet'. Both states hide the pick, so the impact is
    small; the incoherence is the bug. The ledger half must still happen."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)          # 'bet' override gone
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    assert client.post(f"/picks/{pid}/dismiss", headers=h).status_code == 200
    assert client.delete(f"/bets/{bid}/unmark", headers=h).status_code == 200
    import softbook_app_db as adb2
    pdb = adb2.connect()
    states = pdb.execute("SELECT state FROM pick_state").fetchall()
    row = adb2.get_app_bet(pdb, bid)
    pdb.close()
    assert states == [("dismissed",)]                      # preference preserved
    assert row["not_placed"] == 0                          # bet counts again


def test_delete_unmark_restores_the_bet_state_when_not_dismissed(client):
    """The other side of the dismissed check: with no 'dismissed' row in the way,
    restoring still re-hides the pick behind a 'bet' override."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)
    client.delete(f"/bets/{bid}/unmark", headers=h)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    states = pdb.execute("SELECT state FROM pick_state").fetchall()
    pdb.close()
    assert states == [("bet",)]


def test_unmark_unknown_bet_is_404(client):
    h = {"Authorization": "Bearer secret"}
    assert client.post("/bets/9999/unmark", headers=h).status_code == 404
    assert client.delete("/bets/9999/unmark", headers=h).status_code == 404


def test_patch_unknown_bet_is_404(client):
    r = client.patch("/bets/9999", json={"stake": 1.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 404
