"""A pick card must show what it was alerted on AND what it is worth now.

2026-08-05: tennis totals alerted at EV +1.1~1.3% and were classified `cooled`
by the next 10-minute scan. That part was truthful — they really had dropped
out of High. What the card could not show was the *size* of the change, because
the feed only ever sees High rows: once a pick stops being High it stops
producing them, so its last High row is not its current price.

The same investigation surfaced a real bug in the active/cooled split, pinned
below: the reference was the newest High row rather than the newest scan.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import softbook_app_feed as feed  # noqa: E402

NOW = "2026-08-05T10:00:00+00:00"
S0910 = "2026-08-05T09:10:13+00:00"
S0940 = "2026-08-05T09:40:13+00:00"
S0950 = "2026-08-05T09:50:13+00:00"


def _row(recorded_at, ev=0.011, event="ev1", side="Under", point=20.5):
    return {"recorded_at": recorded_at, "sport": "tennis_wta_canadian_open",
            "event_id": event, "commence_time": "2026-08-05T18:00:00+00:00",
            "home": "Elise Mertens", "away": "Anna Bondár", "market": "total",
            "side": side, "point": point, "player": None,
            "best_soft_book": "FanDuel", "best_soft_price": 1.95,
            "snapshot_ev": ev, "n_books": 3, "conf": None}


# ── the active/cooled reference ───────────────────────────────────────

def test_still_high_in_the_latest_scan_is_active():
    rows = [_row(S0940), _row(S0950)]
    out = feed.compose(rows, {}, None, NOW, latest_scan=S0950)
    assert [p["status"] for p in out] == ["active"]


def test_dropped_out_of_high_is_cooled():
    """Alerted at 09:40, not High at 09:50 -> genuinely cooled."""
    out = feed.compose([_row(S0940)], {}, None, NOW, latest_scan=S0950)
    assert out[0]["status"] == "cooled"


def test_scan_with_no_high_anywhere_still_retires_the_previous_pick():
    """THE BUG. Observed 2026-08-05: Atmane@Khachanov was High at 08:40 (EV
    6.77%), re-scanned Med at 08:50 (1.99%), and the 08:50/09:00 scans produced
    zero High picks anywhere. With the reference derived from High rows only,
    `latest` never advanced and the app showed it active — at the stale 6.77% —
    for 20+ minutes after the system had already re-priced it."""
    rows = [_row(S0940)]
    stale = feed.compose(rows, {}, None, NOW)                      # old behaviour
    fixed = feed.compose(rows, {}, None, NOW, latest_scan=S0950)   # scan-aware
    assert stale[0]["status"] == "active", "old behaviour, kept as fallback"
    assert fixed[0]["status"] == "cooled"


def test_absent_latest_scan_falls_back_to_high_rows():
    """Callers that don't pass it (older tests, other surfaces) keep working."""
    out = feed.compose([_row(S0910), _row(S0940)], {}, None, NOW)
    assert {p["status"] for p in out} == {"active"}


def test_user_state_still_outranks_the_scan_reference():
    """Marking a pick as bet must survive it going cold."""
    key = feed._key(_row(S0940))
    out = feed.compose([_row(S0940)], {key: "bet"}, None, NOW, latest_scan=S0950)
    assert out[0]["status"] == "bet"


# ── alert vs now ──────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch):
    db = tmp_path / "ev.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE softbook_ev (
        recorded_at TEXT, tier TEXT, sport TEXT, event_id TEXT,
        commence_time TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, best_soft_book TEXT, best_soft_price REAL,
        snapshot_ev REAL, n_books INT, conf REAL)""")
    # alerted High at 09:10 (+1.3% @1.95), still High 09:40, decayed to Med by 09:50
    for at, tier, ev, price in ((S0910, "High", 0.013, 1.95),
                                (S0940, "High", 0.011, 1.93),
                                (S0950, "Med", 0.004, 1.88)):
        c.execute("INSERT INTO softbook_ev VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (at, tier, "tennis", "ev1", "2026-08-05T18:00:00+00:00",
                   "Mertens", "Bondár", "total", "Under", 20.5, None,
                   "FanDuel", price, ev, 3, None))
    c.commit()
    c.close()
    monkeypatch.setenv("SOFTBOOK_EV_DB", str(db))
    for m in list(sys.modules):
        if m.startswith("softbook_app_api"):
            del sys.modules[m]
    import softbook_app_api
    return softbook_app_api


def test_alert_snapshot_is_the_first_high_not_the_last(api):
    p = {"event_id": "ev1", "market": "total", "side": "Under",
         "point": 20.5, "player": None}
    api._alert_and_now([p])
    assert p["alerted_at"] == S0910
    assert p["alert_ev"] == pytest.approx(0.013)
    assert p["alert_odds"] == pytest.approx(1.95)


def test_now_snapshot_sees_below_high_tiers(api):
    """The whole point: the feed is High-only, so without this the card's
    'current' value would be the 09:40 High row, not the 09:50 Med reality."""
    p = {"event_id": "ev1", "market": "total", "side": "Under",
         "point": 20.5, "player": None}
    api._alert_and_now([p])
    assert p["now_at"] == S0950
    assert p["now_tier"] == "Med"
    assert p["now_ev"] == pytest.approx(0.004)
    assert p["now_odds"] == pytest.approx(1.88)


def test_the_pair_shows_the_decay(api):
    p = {"event_id": "ev1", "market": "total", "side": "Under",
         "point": 20.5, "player": None}
    api._alert_and_now([p])
    assert p["alert_ev"] > p["now_ev"], "alerted +1.3% -> now +0.4%"


def test_unknown_pick_is_left_alone_not_faked(api):
    p = {"event_id": "nope", "market": "h2h", "side": "X",
         "point": None, "player": None}
    api._alert_and_now([p])
    assert "alerted_at" not in p and "now_at" not in p


def test_point_is_matched_so_a_moved_line_is_a_different_pick(api):
    """21.5 and 20.5 are different bets; the alert history must not bleed."""
    p = {"event_id": "ev1", "market": "total", "side": "Under",
         "point": 21.5, "player": None}
    api._alert_and_now([p])
    assert "alerted_at" not in p


def test_empty_input_is_safe(api):
    assert api._alert_and_now([]) == []


# ── why a pick is cooled ──────────────────────────────────────────────

def test_repriced_below_high_is_faded(api):
    """Atmane@Khachanov: 6.77% High -> 1.99% Med. This one really did cool."""
    p = {"event_id": "ev1", "market": "total", "side": "Under", "point": 20.5,
         "player": None, "status": "cooled"}
    api._alert_and_now([p])
    assert p["cooled_reason"] == "faded"


def test_vanished_while_still_high_is_gone_not_faded(api, tmp_path, monkeypatch):
    """9 of 17 cooled picks on 2026-08-05 were this: last snapshot still High,
    the pick just stopped appearing. Calling that "cooled" misreports a bet
    whose last known EV never moved."""
    import sqlite3
    db = tmp_path / "ev2.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE softbook_ev (
        recorded_at TEXT, tier TEXT, sport TEXT, event_id TEXT,
        commence_time TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, best_soft_book TEXT, best_soft_price REAL,
        snapshot_ev REAL, n_books INT, conf REAL)""")
    c.execute("INSERT INTO softbook_ev VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (S0940, "High", "tennis", "gone1", "2026-08-05T18:00:00+00:00",
               "Mertens", "Bondár", "total", "Under", 20.5, None,
               "FanDuel", 1.95, 0.0105, 3, None))
    c.commit(); c.close()
    monkeypatch.setenv("SOFTBOOK_EV_DB", str(db))
    for m in list(sys.modules):
        if m.startswith("softbook_app_api"):
            del sys.modules[m]
    import softbook_app_api as fresh
    p = {"event_id": "gone1", "market": "total", "side": "Under", "point": 20.5,
         "player": None, "status": "cooled"}
    fresh._alert_and_now([p])
    assert p["now_tier"] == "High"
    assert p["cooled_reason"] == "gone"


def test_reason_is_only_set_on_cooled(api):
    p = {"event_id": "ev1", "market": "total", "side": "Under", "point": 20.5,
         "player": None, "status": "active"}
    api._alert_and_now([p])
    assert "cooled_reason" not in p
