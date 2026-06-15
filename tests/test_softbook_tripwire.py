"""Tests for the stake-tiered promotion tracker (scripts/softbook_tripwire.py).

Covers: OOS cutoff, first-entry classification, the point-estimate gates,
single-day & single-game concentration gates, the KILL rule, and the digest
block render. The day-clustered bootstrap is seeded, so verdicts are
deterministic for a given ledger.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_paper_pnl as pnl                          # noqa: E402
import softbook_tripwire as tw                               # noqa: E402


def db_():
    db = sqlite3.connect(":memory:")
    db.execute(pnl.SCHEMA)
    db.execute(pnl.HIGH_SCHEMA)
    return db


def fe_key(db, eid, market, player):
    """Mark (eid, market, player) as a first-entry High opportunity (paper_bet)."""
    db.execute(
        "INSERT OR IGNORE INTO paper_bet (event_id, market, player, tier) VALUES (?,?,?,?)",
        (eid, market, player, "High"))


_UNSET = object()


def high(db, eid, won, pnl_, *, market="batter_total_bases", player="Batter X",
         stake=2.0, odds=2.0, sport="baseball_mlb", side="Over", point=1.5,
         ct="2026-06-20T23:00:00Z", placed="2026-06-20T18:00:00+00:00",
         completed=1, won_col=_UNSET):
    won_col = won if won_col is _UNSET else won_col
    db.execute(
        "INSERT INTO paper_bet_high (event_id,sport,home,away,market,side,point,player,"
        "label,commence_time,book,odds,ev,stake,placed_at,tier,conf,completed,won,result,"
        "pnl,settled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, sport, "H", "A", market, side, point, player, "L", ct, "FD", odds, 0.03,
         stake, placed, "High", 60.0, completed, won_col, "win" if won else "lose",
         pnl_, "x"))


# ---------------- OOS cutoff + classification ----------------

def test_oos_cutoff_excludes_pre_window_bets():
    db = db_()
    high(db, "e_old", 1, 2.0, placed="2026-06-14T18:00:00+00:00")   # pre-OOS -> excluded
    high(db, "e_new", 1, 2.0, placed="2026-06-16T18:00:00+00:00")   # in-OOS  -> counted
    rows = tw.load_classified(db)
    s = tw.cohort_stats(rows, tw.COHORTS[0]["filter"], "2026-06-15")
    assert s["n"] == 1


def test_first_entry_classification_splits_correctly():
    db = db_()
    fe_key(db, "e_fe", "batter_total_bases", "Batter X")            # first-entry opportunity
    high(db, "e_fe", 1, 2.0, player="Batter X")                     # -> first_entry True
    high(db, "e_lc", 0, -2.0, player="Batter Y")                    # not in paper_bet -> late-crosser
    rows = tw.load_classified(db)
    by = {r["event_id"]: r["first_entry"] for r in rows}
    assert by["e_fe"] is True and by["e_lc"] is False


def test_only_graded_rows_counted():
    db = db_()
    high(db, "e1", 1, 2.0, placed="2026-06-16T18:00:00+00:00")
    high(db, "e2", 0, 0.0, placed="2026-06-16T18:00:00+00:00", completed=0)   # pending
    high(db, "e3", 0, 0.0, placed="2026-06-16T18:00:00+00:00", won_col=None)  # void/push
    rows = tw.load_classified(db)
    assert len(rows) == 1 and rows[0]["event_id"] == "e1"


# ---------------- verdict tiers ----------------

def test_small_n_observes():
    db = db_()
    for i in range(5):
        high(db, f"e{i}", 1, 2.0, placed="2026-06-16T18:00:00+00:00")
    s = tw.cohort_stats(tw.load_classified(db), tw.COHORTS[0]["filter"], "2026-06-15")
    _, tier = tw.verdict(s)
    assert tier.startswith("🟢")


def test_kill_when_negative_past_killn():
    db = db_()
    for i in range(tw.KILL_N + 2):                                 # all losses, n > KILL_N
        high(db, f"e{i}", 0, -2.0, ct=f"2026-06-{16 + i % 9:02d}T23:00:00Z",
             placed=f"2026-06-{16 + i % 9:02d}T18:00:00+00:00")
    s = tw.cohort_stats(tw.load_classified(db), tw.COHORTS[0]["filter"], "2026-06-15")
    _, tier = tw.verdict(s)
    assert tier.startswith("☠️")


def test_single_day_concentration_gate_trips():
    db = db_()
    # 8 days, 60 bets, net positive — but ONE day carries almost all the profit.
    for i in range(59):                                            # spread small +/- across 8 days
        d = 16 + i % 8
        high(db, f"sm{i}", 1 if i % 2 else 0, 0.2 if i % 2 else -0.2,
             ct=f"2026-06-{d:02d}T23:00:00Z", placed=f"2026-06-{d:02d}T18:00:00+00:00")
    high(db, "bigday", 1, 80.0, stake=2.0, odds=41.0,             # one monster day
         ct="2026-06-16T23:00:00Z", placed="2026-06-16T18:00:00+00:00")
    s = tw.cohort_stats(tw.load_classified(db), tw.COHORTS[0]["filter"], "2026-06-15")
    gates, _ = tw.verdict(s)
    assert gates["단일일<40%"] is False                            # concentration caught


def test_telegram_block_renders():
    db = db_()
    high(db, "e1", 1, 2.0, placed="2026-06-16T18:00:00+00:00")
    block = tw.telegram_block(db, "2026-06-15")
    assert "트립와이어" in block and tw.COHORTS[0]["label"] in block


def test_telegram_block_zero_state():
    db = db_()                                                     # no OOS bets at all
    block = tw.telegram_block(db, "2026-06-15")
    assert "아직 0건" in block
