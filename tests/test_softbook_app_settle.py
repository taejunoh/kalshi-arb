# tests/test_softbook_app_settle.py
"""Settle open app_bet rows (the companion app's REAL bets) using the same
final-score / player-stat graders the paper ledgers use. Network is injected."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_settle as settle  # noqa: E402
import softbook_app_db as adb  # noqa: E402


def _db_with_bet(**over):
    db = sqlite3.connect(":memory:")
    adb.init_schema(db)
    row = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
           "commence_time": "2026-06-12T23:10:00Z", "market": "batter_total_bases",
           "side": "Over", "point": 1.5, "player": "Brandon Nimmo", "book": "FanDuel",
           "rec_odds": 2.2, "fill_odds": 2.2, "stake": 2.0, "source": "one_tap"}
    row.update(over)
    adb.insert_app_bet(db, row)
    return db


def test_settles_prop_win_with_fill_odds_pnl():
    db = _db_with_bet()  # Over 1.5, fill_odds 2.2, stake 2.0
    n = settle.settle_app_bets(db, key="k",
        fetch_scores=lambda sport, key: {},
        player_stat=lambda *a: 3)              # 3 total bases > 1.5 -> win
    assert n == 1
    row = db.execute("SELECT settled, won, pnl FROM app_bet").fetchone()
    assert row == (1, 1, 2.4)                  # 2.0 * (2.2 - 1) = 2.40


def test_settles_prop_loss():
    db = _db_with_bet()
    settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                           player_stat=lambda *a: 0)   # 0 bases -> lose
    assert db.execute("SELECT settled, won, pnl FROM app_bet").fetchone() == (1, 0, -2.0)


def test_prop_dnp_is_void_pnl_zero():
    import softbook_ev_stats as stats
    db = _db_with_bet()
    settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                           player_stat=lambda *a: stats.DNP)
    row = db.execute("SELECT settled, won, pnl FROM app_bet").fetchone()
    assert row[0] == 1 and row[1] is None and row[2] == 0


def test_game_market_settles_from_scores():
    db = _db_with_bet(market="h2h", side="New York Mets", point=0, player="",
                      home="Miami Marlins", away="New York Mets", fill_odds=2.0)
    n = settle.settle_app_bets(db, key="k",
        fetch_scores=lambda sport, key: {"E1": {"New York Mets": 5, "Miami Marlins": 3}},
        player_stat=lambda *a: None)
    assert n == 1
    assert db.execute("SELECT won, pnl FROM app_bet").fetchone() == (1, 2.0)  # 2*(2.0-1)


def test_open_bet_not_settled_when_no_result():
    db = _db_with_bet(market="h2h", side="New York Mets", point=0, player="",
                      home="Miami Marlins", away="New York Mets")
    n = settle.settle_app_bets(db, key="k",
        fetch_scores=lambda sport, key: {},          # no scores yet
        player_stat=lambda *a: None)
    assert n == 0
    assert db.execute("SELECT settled FROM app_bet").fetchone() == (0,)


def test_skips_legacy_rows_without_sport():
    db = _db_with_bet(sport=None)                      # migrated real_fill row
    n = settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                               player_stat=lambda *a: 3)
    assert n == 0
    assert db.execute("SELECT settled FROM app_bet").fetchone() == (0,)


def test_already_settled_not_reprocessed():
    db = _db_with_bet()
    db.execute("UPDATE app_bet SET settled=1")
    db.commit()
    assert settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                                  player_stat=lambda *a: 3) == 0
