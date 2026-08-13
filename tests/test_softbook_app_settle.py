# tests/test_softbook_app_settle.py
"""Settle open app_bet rows (the companion app's REAL bets) using the same
final-score / player-stat graders the paper ledgers use. Network is injected."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_settle as settle  # noqa: E402
import softbook_app_db as adb  # noqa: E402


_BET = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
        "commence_time": "2026-06-12T23:10:00Z", "market": "batter_total_bases",
        "side": "Over", "point": 1.5, "player": "Brandon Nimmo", "book": "FanDuel",
        "rec_odds": 2.2, "fill_odds": 2.2, "stake": 2.0, "source": "one_tap"}


def _add_bet(db, **over):
    row = dict(_BET)
    row.update(over)
    return adb.insert_app_bet(db, row)


def _db_with_bet(**over):
    db = sqlite3.connect(":memory:")
    adb.init_schema(db)
    _add_bet(db, **over)
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


def test_not_placed_bets_are_never_settled():
    """A bet the user says never happened must not enter PnL."""
    db = _db_with_bet()
    db.execute("UPDATE app_bet SET not_placed=1")
    db.commit()
    n = settle.settle_app_bets(
        db, key="k",
        fetch_scores=lambda *a, **k: pytest.fail("must not fetch for a not-placed bet"),
        player_stat=lambda *a, **k: pytest.fail("must not grade a not-placed bet"))
    assert n == 0
    assert db.execute("SELECT settled FROM app_bet").fetchone() == (0,)


# ── a row that cannot be priced must not take the batch down ──────────
#
# Leaving the stake box blank in CaptureSheet records stake=NULL (it sends
# `stake ? Number(stake) : undefined`, and BetIn.stake is `float | None`). PnL is
# `stake * (fill_odds - 1)`, so grading that row as win/lose raises TypeError —
# and with no per-row guard and a single db.commit() after the loop, that aborted
# every settlement in the batch and skipped report_stuck too. Silently, every 15
# minutes, until someone went looking.

def test_a_row_with_no_stake_does_not_stop_the_rest_of_the_batch():
    db = _db_with_bet(stake=None)              # id 1: blank stake box, graded first
    _add_bet(db, event_id="E2")                # id 2: an ordinary bet
    n = settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                               player_stat=lambda *a: 3)      # both grade as win
    assert n == 1
    assert db.execute("SELECT settled, won, pnl FROM app_bet WHERE id=2"
                      ).fetchone() == (1, 1, 2.4)
    assert db.execute("SELECT settled, pnl FROM app_bet WHERE id=1").fetchone() == (0, None)


def test_a_missing_stake_is_never_priced_at_zero():
    """Coercing NULL to 0 writes a fabricated PnL into a ledger of real money and
    hides the corruption. The row stays open until the user supplies the stake."""
    db = _db_with_bet(stake=None)
    assert settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                                  player_stat=lambda *a: 0) == 0        # a loss
    assert db.execute("SELECT settled, won, pnl FROM app_bet").fetchone() == (0, None, None)


def test_a_row_with_no_fill_odds_is_skipped_the_same_way():
    db = _db_with_bet(fill_odds=None)
    assert settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                                  player_stat=lambda *a: 3) == 0
    assert db.execute("SELECT settled FROM app_bet").fetchone() == (0,)


def test_a_missing_stake_is_skipped_on_the_espn_naver_path_too(monkeypatch):
    """PnL is computed at two sites. results.handles() sports (tennis/KBO/NCAA/
    MMA) take the other one, so the guard has to be on both."""
    monkeypatch.setattr(settle.results, "handles", lambda sport: True)
    monkeypatch.setattr(settle.results, "grade_bet",
                        lambda *a, **k: "win")          # would price stake*(odds-1)
    db = _db_with_bet(sport="baseball_kbo", market="h2h", side="KT Wiz", point=0,
                      player="", home="KT Wiz", away="Doosan Bears", stake=None)
    assert settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                                  player_stat=lambda *a: None) == 0
    assert db.execute("SELECT settled, pnl FROM app_bet").fetchone() == (0, None)


def test_one_row_blowing_up_does_not_abort_the_batch():
    """The NULL guard covers the failure we know about. The loop has to survive
    the one we don't, because the blast radius is the whole run — every other
    bet's settlement, and the report_stuck call that would have said so."""
    db = _db_with_bet(player="Boom")           # id 1: grader raises on this one
    _add_bet(db, event_id="E2")                # id 2: an ordinary bet

    def player_stat(sport, away, home, commence_time, market, player):
        if player == "Boom":
            raise RuntimeError("grader blew up")
        return 3

    n = settle.settle_app_bets(db, key="k", fetch_scores=lambda s, k: {},
                               player_stat=player_stat)
    assert n == 1
    assert db.execute("SELECT settled FROM app_bet WHERE id=2").fetchone() == (1,)
    assert db.execute("SELECT settled FROM app_bet WHERE id=1").fetchone() == (0,)
