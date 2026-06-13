# scripts/softbook_app_db.py
#!/usr/bin/env python3
"""App-side persistence for the +EV companion app.

Four tables in the ubuntu-owned softbook_paper.db (the existing pipeline tables
are NOT touched):
  pick_state   user override on the live scan feed (bet/dismissed) so a
               dismissed/placed pick never re-appears on the next 20-min scan.
  app_bet      bets actually placed = source of truth for calendar PnL + live
               tracking. Supersedes the Telegram-keyed real_fill table.
  filter_prefs single-row feed filter (min EV, sports, muted books, sort).
  push_token   Expo push tokens (re-registered each app launch, pruned on
               DeviceNotRegistered).

LEDGER RULE (load-bearing): app_bet rows are bet records — never delete or
reprice them for measurement hygiene; segment instead.
"""
import os
import sqlite3

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB",
                     "/home/ubuntu/kalshi-arb/data/softbook_paper.db")

PICK_STATE_SCHEMA = """CREATE TABLE IF NOT EXISTS pick_state(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    state TEXT NOT NULL,                 -- 'bet' | 'dismissed'
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, market, side, point, player))"""

APP_BET_SCHEMA = """CREATE TABLE IF NOT EXISTS app_bet(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
    market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
    book TEXT, rec_odds REAL, fill_odds REAL, stake REAL,
    placed_at TEXT, source TEXT,
    live_status TEXT DEFAULT 'pending', live_progress TEXT,
    settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT)"""

FILTER_PREFS_SCHEMA = """CREATE TABLE IF NOT EXISTS filter_prefs(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    min_ev REAL DEFAULT 0.0, sports_json TEXT, muted_books_json TEXT,
    sort TEXT DEFAULT 'ev')"""

PUSH_TOKEN_SCHEMA = """CREATE TABLE IF NOT EXISTS push_token(
    token TEXT PRIMARY KEY, updated_at TEXT, active INTEGER DEFAULT 1)"""

APP_BET_COLS = ("event_id, sport, home, away, commence_time, market, side, point,"
                " player, label, book, rec_odds, fill_odds, stake, placed_at, source")


def init_schema(db):
    for s in (PICK_STATE_SCHEMA, APP_BET_SCHEMA, FILTER_PREFS_SCHEMA, PUSH_TOKEN_SCHEMA):
        db.execute(s)
    db.execute(
        "CREATE TABLE IF NOT EXISTS pushed_price_move("
        "app_bet_id INTEGER, kind TEXT, notified_at TEXT, "
        "PRIMARY KEY(app_bet_id, kind))")
    _ensure_app_bet_columns(db)
    db.commit()


def _ensure_app_bet_columns(db):
    """Idempotently add advisory price columns to an existing app_bet table."""
    have = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    for name, decl in (("current_best_odds", "REAL"), ("last_odds_check_at", "TEXT")):
        if name not in have:
            db.execute(f"ALTER TABLE app_bet ADD COLUMN {name} {decl}")


_PRICING_COLS = ("id", "event_id", "sport", "market", "side", "point", "player",
                 "book", "fill_odds", "current_best_odds", "last_odds_check_at", "label")


def open_bets_for_pricing(db, limit=15):
    """Open bets eligible for a live-odds check: settled=0, matched event, known book+fill.
    Ordered by stake desc so the credit budget covers the bets that matter most."""
    rows = db.execute(
        "SELECT " + ",".join(_PRICING_COLS) + " FROM app_bet "
        "WHERE settled=0 AND event_id IS NOT NULL AND event_id!='' "
        "AND book IS NOT NULL AND fill_odds IS NOT NULL "
        "ORDER BY COALESCE(stake,0) DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(_PRICING_COLS, r)) for r in rows]


def record_current_odds(db, app_bet_id, odds, ts):
    """Advisory live-column write — NOT a bet-record edit (ledger rule preserved)."""
    db.execute("UPDATE app_bet SET current_best_odds=?, last_odds_check_at=? WHERE id=?",
               (odds, ts, app_bet_id))
    db.commit()


def price_move_pushed(db, app_bet_id, kind):
    return db.execute("SELECT 1 FROM pushed_price_move WHERE app_bet_id=? AND kind=?",
                      (app_bet_id, kind)).fetchone() is not None


def mark_price_move_pushed(db, app_bet_id, kind, ts):
    db.execute("INSERT OR IGNORE INTO pushed_price_move(app_bet_id, kind, notified_at) "
               "VALUES (?,?,?)", (app_bet_id, kind, ts))
    db.commit()


def connect():
    # Resolve the path at call time (not the import-time PAPER_DB constant) so the
    # path tracks SOFTBOOK_PAPER_DB even when this module is imported once and the
    # env is set later — required for per-test isolation and env reloads.
    db = sqlite3.connect(os.getenv("SOFTBOOK_PAPER_DB", PAPER_DB), timeout=60)
    init_schema(db)
    return db


def _norm(pick):
    """(event_id, market, side, point, player) with point->0.0, player->''."""
    eid, market, side, point, player = pick
    return (eid, market, side, float(point or 0), player or "")


def set_pick_state(db, pick, state, now):
    db.execute(
        "INSERT INTO pick_state(event_id,market,side,point,player,state,updated_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(event_id,market,side,point,player)"
        " DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
        (*_norm(pick), state, now))
    db.commit()


def pick_states(db):
    """All overrides keyed by the normalized pick tuple -> state."""
    return {(_norm((e, m, s, p, pl))): st for e, m, s, p, pl, st in db.execute(
        "SELECT event_id, market, side, point, player, state FROM pick_state")}


def insert_app_bet(db, bet):
    cur = db.execute(
        f"INSERT INTO app_bet({APP_BET_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bet["event_id"], bet.get("sport"), bet.get("home"), bet.get("away"),
         bet.get("commence_time"), bet["market"], bet["side"],
         float(bet.get("point") or 0), bet.get("player") or "", bet.get("label"),
         bet.get("book"), bet.get("rec_odds"), bet.get("fill_odds"),
         bet.get("stake"), bet.get("placed_at"), bet.get("source")))
    db.commit()
    return cur.lastrowid


def migrate_real_fill(db):
    """Copy FILLED (skipped=0) real_fill rows into app_bet once. Idempotent via
    a source='telegram' + event/market/side/point/player de-dup check. Returns
    the number of new rows inserted. (real_fill lacks sport/home/away/label/
    commence_time; those stay NULL and the app reads them live by event_id.)"""
    try:
        rows = db.execute(
            "SELECT event_id, market, side, point, player, alert_odds, fill_book,"
            " fill_odds, fill_stake, replied_at FROM real_fill WHERE skipped=0"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for (eid, market, side, point, player, aodds, fbook, fodds, stake, rat) in rows:
        exists = db.execute(
            "SELECT 1 FROM app_bet WHERE source='telegram' AND event_id=? AND"
            " market=? AND side=? AND point=? AND player=?",
            (eid, market, side, float(point or 0), player or "")).fetchone()
        if exists:
            continue
        db.execute(
            f"INSERT INTO app_bet({APP_BET_COLS}) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, None, None, None, None, market, side, float(point or 0),
             player or "", None, fbook, aodds, fodds, stake, rat, "telegram"))
        n += 1
    db.commit()
    return n
