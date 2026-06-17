"""CLV measure — FIRST-ENTRY GAME ONLY subset must match the single source.

softbook_ev_measure.measure() reports the full recorded population (diagnostic) AND
the decision-relevant cut: first-entry GAME only, selected by core.is_bet. That
selection MUST agree, snapshot-for-snapshot, with recorder._first_entry_note (the
single source for the Telegram tag / app /picks flag / push worker). These tests
pin both the pure CLV arithmetic and the measure↔recorder agreement so the two
can never silently drift.

first-entry = THIS SIDE was High at the (event, market, player)'s EARLIEST recorded
snapshot (side-aware: the opposite side that crosses High after a steam is a
late-crosser, not a first-entry); core.is_bet then drops props + late-crossers.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_measure as mz                                # noqa: E402
import softbook_ev_recorder as rec                              # noqa: E402
import softbook_ev_core as core                                 # noqa: E402


def row(eid, market, side, point, player, ts, fair, tier, px=2.0):
    """A softbook_ev snapshot tuple in measure()'s column order."""
    return (eid, market, side, point, player, ts, fair, px, tier)


# ---------- first_entry_map: side-aware High-sides-at-open ----------

def test_first_entry_map_earliest_tier_wins():
    rows = [
        row("E1", "spread", "Home", -1.5, "", "2026-06-13T14:00:00+00:00", 0.5, "High"),
        row("E1", "spread", "Home", -1.5, "", "2026-06-13T18:00:00+00:00", 0.6, "Med"),
        row("E2", "total", "Over", 8.5, "", "2026-06-13T14:00:00+00:00", 0.5, "Med"),
        row("E2", "total", "Over", 8.5, "", "2026-06-13T18:00:00+00:00", 0.6, "High"),
    ]
    fe = mz.first_entry_map(rows)
    assert "Home" in fe[("E1", "spread", "")]                    # earliest was High
    assert "Over" not in fe.get(("E2", "total", ""), frozenset())  # drifted High later


def test_first_entry_map_side_aware_opposite_crosser_excluded():
    # side-aware: the side High at the market's earliest snapshot (Home) is the
    # first-entry; the OPPOSITE side that crosses into High only later (Away, after a
    # line move) is a LATE-CROSSER, NOT a first-entry.
    rows = [
        row("E1", "h2h", "Home", 0, "", "2026-06-13T14:00:00+00:00", 0.55, "High"),
        row("E1", "h2h", "Away", 0, "", "2026-06-13T18:00:00+00:00", 0.45, "High"),
    ]
    fe = mz.first_entry_map(rows)
    assert fe[("E1", "h2h", "")] == frozenset({"Home"})          # only the open-High side


# ---------- measure(): bet-rule subset selection ----------

def _close_at(eid, market, side, point, player, close_fair):
    return {(eid, market, side, point or 0, player or ""): close_fair}


def test_measure_game_first_entry_in_bet_subset():
    rows = [
        row("G1", "spread", "Home", -1.5, "", "2026-06-13T14:00:00+00:00", 0.40, "High"),
        row("G1", "spread", "Home", -1.5, "", "2026-06-13T18:00:00+00:00", 0.42, "High"),
    ]
    r = mz.measure(rows, _close_at("G1", "spread", "Home", -1.5, "", 0.48))
    assert len(r["bet_clvs"]) == 1
    assert abs(r["bet_clvs"][0] - (0.48 - 0.40)) < 1e-9   # close_fair - open_fair, TRUE close
    assert r["bet_true"] == 1


def test_measure_late_crosser_game_excluded():
    rows = [
        row("G2", "total", "Over", 8.5, "", "2026-06-13T14:00:00+00:00", 0.40, "Med"),
        row("G2", "total", "Over", 8.5, "", "2026-06-13T18:00:00+00:00", 0.42, "High"),
    ]
    r = mz.measure(rows, {})
    assert r["bet_clvs"] == []                          # late-crosser game -> SKIP
    assert len(r["clvs"]) == 1                          # still in full population


def test_measure_prop_excluded_even_if_first_entry():
    rows = [
        row("P1", "pitcher_strikeouts", "Over", 5.5, "Skubal", "2026-06-13T14:00:00+00:00", 0.40, "High"),
        row("P1", "pitcher_strikeouts", "Over", 5.5, "Skubal", "2026-06-13T18:00:00+00:00", 0.42, "High"),
    ]
    r = mz.measure(rows, {})
    assert r["bet_clvs"] == []                          # all props SKIP
    assert len(r["clvs"]) == 1


def test_measure_fallback_close_when_no_true_close():
    rows = [
        row("G3", "h2h", "Home", 0, "", "2026-06-13T14:00:00+00:00", 0.50, "High"),
        row("G3", "h2h", "Home", 0, "", "2026-06-13T20:00:00+00:00", 0.53, "High"),
    ]
    r = mz.measure(rows, {})                            # no closing_line -> fallback last in-band
    assert len(r["bet_clvs"]) == 1
    assert abs(r["bet_clvs"][0] - (0.53 - 0.50)) < 1e-9
    assert r["bet_true"] == 0                           # fallback, not a true close


def test_measure_single_snapshot_unmeasurable_without_true_close():
    rows = [row("G4", "spread", "Home", -1.5, "", "2026-06-13T14:00:00+00:00", 0.50, "High")]
    r = mz.measure(rows, {})                            # only 1 snap, no close -> dropped
    assert r["clvs"] == [] and r["bet_clvs"] == []


def test_measure_bet_true_subset_excludes_fallback():
    # one BET game with a TRUE close, one BET game on fallback only -> bet_clvs has 2,
    # bet_clvs_true has just the true-close one (the honest, un-biased cut).
    rows = [
        row("T1", "spread", "Home", -1.5, "", "2026-06-13T14:00:00+00:00", 0.40, "High"),
        row("T1", "spread", "Home", -1.5, "", "2026-06-13T18:00:00+00:00", 0.41, "High"),
        row("F1", "h2h", "Home", 0, "", "2026-06-13T14:00:00+00:00", 0.50, "High"),
        row("F1", "h2h", "Home", 0, "", "2026-06-13T20:00:00+00:00", 0.55, "High"),
    ]
    r = mz.measure(rows, _close_at("T1", "spread", "Home", -1.5, "", 0.48))
    assert len(r["bet_clvs"]) == 2 and r["bet_true"] == 1
    assert len(r["bet_clvs_true"]) == 1
    assert abs(r["bet_clvs_true"][0] - (0.48 - 0.40)) < 1e-9    # the TRUE-close one only


def _rows_from_db(db):
    """Fetch snapshots exactly as main() does — SAME ORDER BY so measure's first-entry
    tie-break consumes the identical leading row the recorder's LIMIT 1 sees."""
    return db.execute(
        "SELECT event_id, market, side, point, player, recorded_at, "
        "pinnacle_fair_prob, best_soft_price, tier FROM softbook_ev "
        "ORDER BY recorded_at").fetchall()


def test_measure_tie_same_recorded_at_split_tier_matches_recorder():
    # h2h Home=High + Away=Med at the SAME opening recorded_at (one scan). Side-aware:
    # only the side that was High at the open (Home) is first-entry; Away is a
    # late-crosser. measure's bet-subset membership and the recorder tag must agree
    # PER SIDE — deterministic, no SQLite tie-break dependence.
    ts0, ts1 = "2026-06-13T14:00:00+00:00", "2026-06-13T18:00:00+00:00"
    snaps = [("h2h", "Home", 0, "", ts0, "High"), ("h2h", "Away", 0, "", ts0, "Med"),
             ("h2h", "Home", 0, "", ts1, "High"), ("h2h", "Away", 0, "", ts1, "Med")]
    db = _snap_db(snaps)
    rows = _rows_from_db(db)
    r = mz.measure(rows, {})
    rec_bet_sides = [s for s in ("Home", "Away")
                     if "BET" in rec._first_entry_note(
                         db, {"event_id": "E1", "market": "h2h", "side": s, "point": 0,
                              "player": "", "sport": "baseball_mlb"})]
    assert rec_bet_sides == ["Home"]                     # only the open-High side bets
    assert len(r["bet_clvs"]) == len(rec_bet_sides) == 1  # measure agrees, no drift


# ---------- the load-bearing cross-check: measure ↔ recorder single source ----------

def _snap_db(snaps):
    """Build an in-memory recorder db from (market, side, point, player, ts, tier)."""
    db = sqlite3.connect(":memory:")
    db.execute(rec.SCHEMA)
    db.execute(rec.ALERT_SCHEMA)
    rec._ensure_alert_cols(db)
    for market, side, point, player, ts, tier in snaps:
        db.execute("INSERT INTO softbook_ev VALUES (" + ",".join("?" * 19) + ")",
                   (ts, "baseball_mlb", "E1", "2026-06-13T23:10:00Z", "Home", "Away",
                    market, side, point, player or "", 0.5, 2.0, "FanDuel", 2.05,
                    0.03, 8, 6, 61.0, tier))
    return db


def test_measure_bet_subset_agrees_with_recorder_tag():
    # Same four scenarios as test_softbook_first_entry's 2x2 — measure's bet-subset
    # membership must equal "BET" in the recorder tag, market for market.
    cases = [
        ("spread", "Home", -1.5, "", [("2026-06-13T14:00:00+00:00", "High")], True),
        ("total", "Over", 8.5, "", [("2026-06-13T14:00:00+00:00", "Med"),
                                     ("2026-06-13T18:00:00+00:00", "High")], False),
        ("pitcher_strikeouts", "Over", 5.5, "Skubal",
         [("2026-06-13T14:00:00+00:00", "High")], False),
        ("batter_total_bases", "Over", 1.5, "Soto",
         [("2026-06-13T14:00:00+00:00", "Low"),
          ("2026-06-13T18:00:00+00:00", "High")], False),
    ]
    for market, side, point, player, snaps, expect_bet in cases:
        # recorder single source
        db = _snap_db([(market, side, point, player, ts, tier) for ts, tier in snaps])
        b = {"event_id": "E1", "market": market, "side": side, "point": point,
             "player": player, "sport": "baseball_mlb"}
        rec_bets = "BET" in rec._first_entry_note(db, b)
        # measure subset (needs >=2 snaps OR a true close to be measurable; add a
        # 2nd snap so single-High cases are measurable without inventing a close)
        rows = [row("E1", market, side, point, player, ts, 0.4, tier)
                for ts, tier in snaps]
        if len(rows) == 1:
            rows.append(row("E1", market, side, point, player,
                            "2026-06-13T22:00:00+00:00", 0.42,
                            rows[0][8]))               # same tier, later -> measurable
        r = mz.measure(rows, {})
        mz_bet = len(r["bet_clvs"]) == 1
        assert rec_bets == expect_bet, (market, "recorder", rec_bets, expect_bet)
        assert mz_bet == expect_bet, (market, "measure", mz_bet, expect_bet)
        assert mz_bet == rec_bets, (market, "DRIFT measure!=recorder")
