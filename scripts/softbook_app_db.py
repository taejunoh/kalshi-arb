# scripts/softbook_app_db.py
#!/usr/bin/env python3
"""App-side persistence for the +EV companion app.

Four tables in the ubuntu-owned softbook_paper.db (the existing pipeline tables
are NOT touched):
  pick_state   user override on the live scan feed (bet/dismissed) so a
               dismissed/placed pick never re-appears on the next 20-min scan.
  app_bet      bets actually placed = source of truth for calendar PnL + live
               tracking. Supersedes the Telegram-keyed real_fill table.
  filter_prefs single-row app settings (min EV, sports, muted books, sort,
               unit stake — the ONE place the stake lives; see unit_stake()).
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
    settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT,
    not_placed INTEGER DEFAULT 0, not_placed_at TEXT)"""

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
    db.execute(
        "CREATE TABLE IF NOT EXISTS mute_rule("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, field TEXT, value TEXT, created_at TEXT)")
    _ensure_app_bet_columns(db)
    if "unit_stake" not in {r[1] for r in db.execute("PRAGMA table_info(filter_prefs)")}:
        db.execute("ALTER TABLE filter_prefs ADD COLUMN unit_stake REAL")
    db.commit()


DEFAULT_UNIT_STAKE = 20.0


def unit_stake(db=None):
    """The user's per-pick stake — single source for the app card AND the
    Telegram alert.

    It used to live only in the phone's SecureStore, so the server never saw
    it: the app said $25 (changed in July) while Telegram kept saying $20, its
    code default, for a month. Same class of split as the bet rule before
    core.is_bet — two surfaces describing one decision.

    Opens its own read-only connection when given none, so the recorder can
    call it without holding a writable handle on the app db.
    """
    own = db is None
    if own:
        try:
            db = sqlite3.connect(
                "file:%s?mode=ro" % os.getenv("SOFTBOOK_PAPER_DB", PAPER_DB),
                uri=True, timeout=5)
        except sqlite3.Error:
            return DEFAULT_UNIT_STAKE
    try:
        row = db.execute("SELECT unit_stake FROM filter_prefs WHERE id=1").fetchone()
        return float(row[0]) if row and row[0] else DEFAULT_UNIT_STAKE
    except sqlite3.Error:
        return DEFAULT_UNIT_STAKE          # pre-migration db, or none at all
    finally:
        if own:
            db.close()


def _ensure_app_bet_columns(db):
    """Idempotently add columns to an existing app_bet table."""
    have = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    # Written by the retired price-move pusher (2026-08-05). Kept so existing rows
    # keep their columns; nothing writes or reads them now.
    # not_placed: the user marked a pick bet but the bet never happened (mis-tap,
    # or the book rejected it). NOT called 'voided' — softbook_results already
    # uses 'void' for a settlement outcome (cancelled/walkover game), and one word
    # for two concepts would fuse them permanently. The row is never deleted
    # (ledger rule); every consumer filters not_placed=0 instead.
    for name, decl in (("current_best_odds", "REAL"),
                       ("last_odds_check_at", "TEXT"),
                       ("not_placed", "INTEGER DEFAULT 0"),
                       ("not_placed_at", "TEXT")):
        if name not in have:
            db.execute(f"ALTER TABLE app_bet ADD COLUMN {name} {decl}")


_PRICING_COLS = ("id", "event_id", "sport", "market", "side", "point", "player",
                 "book", "fill_odds", "current_best_odds", "last_odds_check_at", "label")


def add_mute_rule(db, field, value, ts):
    cur = db.execute("INSERT INTO mute_rule(field, value, created_at) VALUES (?,?,?)",
                     (field, value, ts))
    db.commit()
    return cur.lastrowid


def list_mute_rules(db):
    rows = db.execute("SELECT id, field, value, created_at FROM mute_rule ORDER BY id").fetchall()
    return [dict(zip(("id", "field", "value", "created_at"), r)) for r in rows]


def active_mute_rules(db):
    """[(field, value), …] for the feed filter."""
    return [(r[0], r[1]) for r in db.execute("SELECT field, value FROM mute_rule")]


def delete_mute_rule(db, rule_id):
    db.execute("DELETE FROM mute_rule WHERE id=?", (rule_id,))
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


def pick_state_of(db, pick):
    """This one pick's override ('bet' | 'dismissed'), or None. The targeted read
    for callers that only care about a single pick — pick_states() scans the
    whole table to build the feed's map."""
    row = db.execute(
        "SELECT state FROM pick_state WHERE event_id=? AND market=? AND side=?"
        " AND point=? AND player=?", _norm(pick)).fetchone()
    return row[0] if row else None


def undismiss_group(db, pick):
    """Delete the pick's dismissed row AND its same-timestamp dismissed
    companions (the auto-dismissed reverse-middle opposites from the same
    request). Returns rows deleted; 0 if the pick is not dismissed.
    Never touches state='bet' — bets are records, not preferences."""
    row = db.execute(
        "SELECT updated_at FROM pick_state WHERE event_id=? AND market=? AND"
        " side=? AND point=? AND player=? AND state='dismissed'",
        _norm(pick)).fetchone()
    if not row:
        return 0
    cur = db.execute(
        "DELETE FROM pick_state WHERE event_id=? AND state='dismissed'"
        " AND updated_at=?", (pick[0], row[0]))
    db.commit()
    return cur.rowcount


def pick_states(db):
    """All overrides keyed by the normalized pick tuple -> state."""
    return {(_norm((e, m, s, p, pl))): st for e, m, s, p, pl, st in db.execute(
        "SELECT event_id, market, side, point, player, state FROM pick_state")}


def placed_bets_by_event(db):
    """{event_id: [bet dicts]} for every bet the user actually placed, open or
    settled. Feeds the feed's conflict checks — what is already staked on a
    game decides what the app should still offer on it."""
    out = {}
    for eid, m, sd, pt, pl, home, away, label in db.execute(
            "SELECT event_id, market, side, point, player, home, away, label"
            " FROM app_bet WHERE event_id IS NOT NULL AND event_id != ''"):
        out.setdefault(eid, []).append(
            {"market": m, "side": sd, "point": pt or 0, "player": pl or "",
             "home": home, "away": away, "label": label})
    return out


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


_EDITABLE_BET_COLS = ("book", "stake", "fill_odds")


def get_app_bet(db, bet_id):
    """One app_bet row as a dict (all columns), or None."""
    cur = db.execute("SELECT * FROM app_bet WHERE id=?", (bet_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def update_app_bet(db, bet_id, fields):
    """Write the three user-entered columns. Anything else in `fields` is
    ignored on purpose — event/market/side describe a DIFFERENT bet, rec_odds is
    a system observation feeding CLV, and settled/won/pnl belong to the settle
    worker. Returns rows changed."""
    cols = [c for c in _EDITABLE_BET_COLS if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=?" for c in cols)
    cur = db.execute(f"UPDATE app_bet SET {sets} WHERE id=?",
                     (*(fields[c] for c in cols), bet_id))
    db.commit()
    return cur.rowcount


def set_not_placed(db, bet_id, flag, now):
    """Mark (or unmark) a bet as never actually placed. The row stays — see the
    LEDGER RULE at the top of this module. Returns True if the row exists."""
    cur = db.execute("UPDATE app_bet SET not_placed=?, not_placed_at=? WHERE id=?",
                     (1 if flag else 0, now if flag else None, bet_id))
    db.commit()
    return cur.rowcount > 0


def live_bets_for_pick(db, pick, exclude_id=None):
    """How many app_bet rows for this pick the user still owns (not_placed=0),
    ignoring `exclude_id`. Pass the row being disowned as exclude_id to ask "is
    there ANOTHER live bet on this pick?" — the question clear_bet_state's caller
    must answer first (see that docstring).

    `id IS NOT ?` rather than `id != ?`: SQLite's IS NOT is the null-safe
    comparison, so exclude_id=None correctly excludes nothing instead of
    matching no rows the way `id != NULL` would."""
    eid, market, side, point, player = _norm(pick)
    return db.execute(
        "SELECT count(*) FROM app_bet WHERE event_id=? AND market=? AND side=?"
        " AND COALESCE(point,0)=? AND COALESCE(player,'')=?"
        " AND COALESCE(not_placed,0)=0 AND id IS NOT ?",
        (eid, market, side, point, player, exclude_id)).fetchone()[0]


def clear_bet_state(db, pick):
    """Delete this pick's state='bet' override so it returns to the live feed.
    Single row only, and only state='bet' — unlike undismiss_group there is no
    reverse-middle group to sweep, and a 'dismissed' row is a separate
    preference that this must not touch. Returns rows deleted.

    CALLER CONTRACT: the override speaks for EVERY bet on the pick, not for one
    app_bet row, so only call this once no owned bet survives — ask
    live_bets_for_pick first. Returning a pick to the feed while a bet on it is
    still open is the double-bet failure mode."""
    cur = db.execute(
        "DELETE FROM pick_state WHERE event_id=? AND market=? AND side=?"
        " AND point=? AND player=? AND state='bet'", _norm(pick))
    db.commit()
    return cur.rowcount


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
