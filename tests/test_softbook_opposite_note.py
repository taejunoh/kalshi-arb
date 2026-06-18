"""Same-game opposite-direction High pairing — added 2026-06-12.

Both sides of a game can be individually +EV vs Pinnacle (each is priced
against its own exact-line de-vig), but a reverse-middle pair can never both
win and dies together in the gap (home by 1-5 / total 154-158). User's rule:
flag the pair on the alert and recommend only the higher-EV side.

① _no_both_win: geometry of the reverse middle (margin axis + O/U axis).
② _opposite_note: cross-market pair (h2h vs spread) from same-scan batch and
   from previously alerted lines, with the EV pick.
③ _dup_note: same-market opposite side (Under 153.5 vs Over 158.5 across
   scans) is a LATE-CROSSER (side-aware first-entry SKIPs it) → recommend the
   first-entry side only, no hedge (the old "take higher-EV side" was refuted).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_recorder as rec                             # noqa: E402

HOME, AWAY = "Dallas Wings", "Phoenix Mercury"
CT = "2026-06-12T01:10:00Z"


def bet(market, side, point=None, player=None, ev=0.03, book="BetRivers",
        odds=2.0, eid="E1"):
    return {"event_id": eid, "sport": "basketball_wnba", "commence_time": CT,
            "home": HOME, "away": AWAY, "market": market, "side": side,
            "point": point, "player": player, "book": book, "odds": odds,
            "ev": ev, "n_books": 5, "n_ev": 4}


def db_():
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    db.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(db)
    return db


def snap(db, b, rec_at="2026-06-12T20:00:00+00:00", tier="High"):
    db.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
               (rec_at, b["sport"], b["event_id"], b["commence_time"], HOME, AWAY,
                b["market"], b["side"], b["point"], b.get("player") or "",
                0.5, 2.0, b["book"], b["odds"], b["ev"], 5, 4, 61.0, tier))


def mark_alerted(db, b, at="2026-06-12T20:00:05+00:00"):
    db.execute("INSERT INTO high_alerted(event_id,market,side,point,player,"
               "alerted_at,book,odds) VALUES (?,?,?,?,?,?,?,?)",
               (b["event_id"], b["market"], b["side"], b["point"] or 0,
                b.get("player") or "", at, b["book"], b["odds"]))


# ---------- ① _no_both_win geometry ----------

def test_home_spread_vs_away_ml_is_reverse_middle():
    # the real Jun 11 case: Wings -5.5 + Mercury ML — home by 1-5 kills both
    assert rec._no_both_win(bet("spread", HOME, -5.5), bet("h2h", AWAY))


def test_home_ml_vs_away_plus_spread_is_a_real_middle_not_flagged():
    # home ML + away +5.5: home by 1-5 wins BOTH — that's a middle, no warning
    assert not rec._no_both_win(bet("h2h", HOME), bet("spread", AWAY, 5.5))


def test_home_ml_vs_away_laying_points_is_reverse_middle():
    # home ML + away -2.5: away by 1-2 kills both
    assert rec._no_both_win(bet("h2h", HOME), bet("spread", AWAY, -2.5))


def test_under_low_over_high_is_reverse_middle():
    # the real Jun 12 case: Under 153.5 + Over 158.5 — total 154-158 kills both
    assert rec._no_both_win(bet("total", "Under", 153.5), bet("total", "Over", 158.5))


def test_under_high_over_low_is_a_real_middle_not_flagged():
    assert not rec._no_both_win(bet("total", "Under", 158.5), bet("total", "Over", 153.5))


def test_same_direction_not_flagged():
    assert not rec._no_both_win(bet("h2h", HOME), bet("spread", HOME, -5.5))


def test_different_players_props_not_flagged():
    assert not rec._no_both_win(bet("player_points", "Over", 20.5, player="A"),
                                bet("player_points", "Under", 15.5, player="B"))


def test_margin_vs_total_axes_never_pair():
    assert not rec._no_both_win(bet("spread", HOME, -5.5), bet("total", "Under", 153.5))


# ---------- ② _opposite_note ----------

def test_batch_pair_notes_both_sides_with_ev_pick():
    db = db_()
    spread = bet("spread", HOME, -5.5, ev=0.045, book="BetRivers", odds=1.91)
    ml = bet("h2h", AWAY, ev=0.018, book="Caesars", odds=3.22)
    batch = [spread, ml]
    n_spread = rec._opposite_note(db, spread, batch)
    n_ml = rec._opposite_note(db, ml, batch)
    assert "반대방향" in n_spread and "이쪽" in n_spread        # 4.5% >= 1.8%
    assert "Caesars +222" in n_spread and "+1.8%" in n_spread   # 3.22 -> +222 (american)
    assert "반대방향" in n_ml and "반대쪽" in n_ml
    assert "Dallas Wings -5.5" in n_ml


def test_prior_alerted_line_found_via_db_with_snapshot_ev():
    db = db_()
    spread = bet("spread", HOME, -5.5, ev=0.045)
    snap(db, spread)
    mark_alerted(db, spread)
    ml = bet("h2h", AWAY, ev=0.018, book="Caesars", odds=3.22)
    note = rec._opposite_note(db, ml, ())
    assert "반대방향" in note and "+4.5%" in note and "반대쪽" in note


def test_no_note_for_same_direction_or_other_event():
    db = db_()
    batch = [bet("h2h", HOME, ev=0.02),
             bet("spread", HOME, -5.5, ev=0.03),                # same direction
             bet("spread", AWAY, -2.5, ev=0.03, eid="E2")]      # other game
    assert rec._opposite_note(db, batch[0], batch) == ""


# ---------- ③ _dup_note same-market opposite = late-crosser, skip ----------

def test_dup_note_opposite_side_recommends_skip_first_entry_only():
    # The later opposite side is a LATE-CROSSER (side-aware is_first_entry SKIPs it).
    # _dup_note must point back to the first-entry side and forbid hedging — NOT
    # recommend the higher-EV side (data-refuted: higher-EV opposite won only 3/8).
    db = db_()
    under = bet("total", "Under", 153.5, ev=0.025, book="Fanatics", odds=2.25)
    snap(db, under)
    mark_alerted(db, under)
    over = bet("total", "Over", 158.5, ev=0.024, book="FanDuel", odds=2.14)
    note = rec._dup_note(db, over)
    assert "방향 반대" in note and "late-crosser" in note
    assert "first-entry" in note and "헤지 금지" in note
    assert "처음 쪽" not in note and "+2.4%" not in note
