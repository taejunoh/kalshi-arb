# scripts/softbook_push_worker.py
#!/usr/bin/env python3
"""Push worker for the +EV companion app — replaces the Telegram send path.

Each run: push every NEW High pick (deduped in pushed_high so a pick pushes
once; a moved line is a new pick = new push, like the recorder's high_alerted),
attach a reverse-middle warning when a same-game opposite-direction High exists,
and push settlement results for app_bet rows not yet notified.

Runs as a oneshot timer aligned to the scan cycle (kalshi-softbook-push.timer),
or inline after a scan. Fires 24/7 (no quiet window — the user bets at night).
"""
import datetime
import os
import sqlite3

import softbook_ev_core as core
import softbook_ev_recorder as rec
import softbook_app_db as adb
import softbook_app_feed as feed
import softbook_push as push

PRICE_MOVE_LIMIT = 15           # max open bets priced per run (credit budget)
PRICE_MOVE_THRESHOLD = 0.05     # |move| >= 5% to push
CATEGORY_PRICE_MOVE = "price_move"


def project_price_move(fill_odds, current_odds, threshold=PRICE_MOVE_THRESHOLD):
    """Classify an open bet's odds move. move = current/fill - 1.
    drop (move <= -threshold) -> 'cashout' (you beat the market);
    rise (move >= +threshold) -> 'move' (line against you); else None."""
    if not fill_odds or not current_odds:
        return {"kind": None, "move_pct": 0.0}
    move = current_odds / fill_odds - 1.0
    if move <= -threshold:
        return {"kind": "cashout", "move_pct": move}
    if move >= threshold:
        return {"kind": "move", "move_pct": move}
    return {"kind": None, "move_pct": move}


EV_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")

PUSHED_HIGH = """CREATE TABLE IF NOT EXISTS pushed_high(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    pushed_at TEXT, PRIMARY KEY (event_id, market, side, point, player))"""
NOTIFIED_SETTLE = """CREATE TABLE IF NOT EXISTS notified_settle(
    app_bet_id INTEGER PRIMARY KEY, notified_at TEXT)"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _latest_high(ev):
    cols = ("recorded_at, sport, event_id, commence_time, home, away, market,"
            " side, point, player, best_soft_book, best_soft_price, snapshot_ev,"
            " n_books, conf")
    rows = ev.execute(
        f"SELECT {cols} FROM softbook_ev WHERE tier='High' AND"
        " datetime(recorded_at) >= datetime('now','-1 day')").fetchall()
    keys = cols.replace(" ", "").split(",")
    return [dict(zip(keys, r)) for r in rows]


def _opp_note(rows, pick):
    this = {"market": pick["market"], "side": pick["side"],
            "point": pick["point"] or 0, "player": pick["player"] or "",
            "home": pick["home"], "away": pick["away"]}
    for r in rows:
        if r["event_id"] != pick["event_id"] or r["market"] == pick["market"]:
            continue
        other = {"market": r["market"], "side": r["side"], "point": r["point"] or 0,
                 "player": r["player"] or "", "home": r["home"], "away": r["away"]}
        if rec._no_both_win(this, other):
            return " ⚖️ 반대방향 High 있음 (동시적중 불가)"
    return ""


def _is_first_entry(ev, r):
    """True if this (event,market,player)'s EARLIEST softbook_ev snapshot was already
    High — the +16.6% ROI 'first-entry' subset. Late-crossers (drifted into High only
    after the line moved) settled ~0 (pure dilution). Mirrors recorder._first_entry_note
    and the app /picks first_entry flag. On any lookup error, return True: never suppress
    a possibly-real first-entry alert (a missed +EV bet costs more than a stray push)."""
    try:
        row = ev.execute(
            "SELECT tier FROM softbook_ev WHERE event_id=? AND market=?"
            " AND COALESCE(player,'')=? ORDER BY recorded_at LIMIT 1",
            (r["event_id"], r["market"], r.get("player") or "")).fetchone()
    except sqlite3.Error:
        return True
    return bool(row and row[0] == "High")


def _high_messages(ev, pdb, tokens, now):
    """Push ONE alert per NEW first-entry High pick. Late-crossers are still deduped
    into pushed_high (so they are not re-evaluated each scan) but never alerted — the
    user wants first-entry-only notifications; the app feed still shows everything,
    tagged 🟣/⚪ with a first-entry filter."""
    pdb.execute(PUSHED_HIGH)
    rows = _latest_high(ev)
    latest = max((r["recorded_at"] for r in rows), default=None)
    msgs = []
    for r in rows:
        if r["recorded_at"] != latest:
            continue
        pk = (r["event_id"], r["market"], r["side"], r["point"] or 0, r["player"] or "")
        cur = pdb.execute(
            "INSERT OR IGNORE INTO pushed_high VALUES (?,?,?,?,?,?)", (*pk, now))
        if not cur.rowcount:
            continue
        if not _is_first_entry(ev, r):       # first-entry-only alerts (the +EV edge)
            continue
        label = core.label(r["market"], r["side"],
                           None if r["market"] == "h2h" else r["point"],
                           r["player"] or None)
        pid = feed._encode_id(pk)
        body = (f"{label} · {r['best_soft_book']} {r['best_soft_price']:.2f}"
                f" · EV {(r['snapshot_ev'] or 0)*100:+.1f}%" + _opp_note(rows, r))
        for tok in tokens:
            msgs.append(push.build_message(tok, "🟣 First-entry +EV", body,
                                           data={"pick_id": pid}, category="high_pick"))
    pdb.commit()
    return msgs


def _settle_messages(pdb, tokens, now):
    pdb.execute(NOTIFIED_SETTLE)
    rows = pdb.execute(
        "SELECT a.id, a.label, a.won, a.pnl FROM app_bet a"
        " LEFT JOIN notified_settle n ON n.app_bet_id=a.id"
        " WHERE a.settled=1 AND n.app_bet_id IS NULL").fetchall()
    msgs = []
    for bid, label, won, pnl in rows:
        # won: 1=win, 0=lose, None=void/push (app_settle writes None for DNP/push)
        verdict = "✅ 적중" if won == 1 else ("↩️ 무효" if won is None else "❌ 미적중")
        body = f"{label or ''} — {verdict} ({(pnl or 0):+.2f})"
        for tok in tokens:
            msgs.append(push.build_message(tok, "정산 결과", body,
                                           data={"app_bet_id": bid}, category="settle"))
        pdb.execute("INSERT OR IGNORE INTO notified_settle VALUES (?,?)", (bid, now))
    pdb.commit()
    return msgs


def _markets_for(market):
    """Mirror softbook_app_api._markets_for_pick EXACTLY: a comma-joined STRING (event_odds
    interpolates it straight into the URL — a list would stringify to "['x','y']"), and only
    append "_alternate" when market is in core.PROP_ALTS. Requesting a non-existent alternate
    key 422s the WHOLE Odds API call (load-bearing PROP_ALTS guard), so never blindly add it."""
    if market in core.ALL_PROPS:
        keys = [market] + ([market + "_alternate"] if market in core.PROP_ALTS else [])
        return ",".join(keys)
    return core.GAME_MARKETS


def _default_fetch_odds(sport, event_id, market, side, point, player, book):
    """Current best available odds for this exact selection at the user's book, or None."""
    key = core.read_key()
    data, _ = core.event_odds(sport, event_id, key, _markets_for(market))
    _, quotes = core.line_quotes(data, market, side, point, player)
    return dict(quotes).get(book)


def _price_move_messages(pdb, tokens, now, fetch_odds=None):
    """Price open bets (top PRICE_MOVE_LIMIT by stake) and push on a material move.
    One push per (app_bet, kind). Records current odds even when no push fires."""
    fetch_odds = fetch_odds or _default_fetch_odds
    total = pdb.execute(
        "SELECT COUNT(*) FROM app_bet WHERE settled=0 AND event_id IS NOT NULL "
        "AND event_id!='' AND book IS NOT NULL AND fill_odds IS NOT NULL").fetchone()[0]
    if total > PRICE_MOVE_LIMIT:
        print(f"[price-move] {total} priceable open bets; capping at {PRICE_MOVE_LIMIT} by stake")
    msgs = []
    for b in adb.open_bets_for_pricing(pdb, limit=PRICE_MOVE_LIMIT):
        try:
            cur = fetch_odds(b["sport"], b["event_id"], b["market"],
                             b["side"], b["point"], b["player"], b["book"])
        except Exception as e:
            print(f"[price-move] fetch failed for bet {b['id']}: {e}")
            cur = None
        if cur is None:
            continue
        adb.record_current_odds(pdb, b["id"], cur, now)
        proj = project_price_move(b["fill_odds"], cur)
        kind = proj["kind"]
        if not kind or adb.price_move_pushed(pdb, b["id"], kind):
            continue
        title = "💸 캐시아웃 기회" if kind == "cashout" else "⚠️ 라인 이동"
        arrow = "↓" if kind == "cashout" else "↑"
        body = (f"{b['label'] or b['side']} · {b['book']} "
                f"{b['fill_odds']:.2f}{arrow}{cur:.2f} ({proj['move_pct'] * 100:+.0f}%)")
        for tok in tokens:
            msgs.append(push.build_message(tok, title, body,
                                           data={"app_bet_id": b["id"]},
                                           category=CATEGORY_PRICE_MOVE))
        adb.mark_price_move_pushed(pdb, b["id"], kind, now)
    return msgs


def run_once(ev, pdb, send=None):
    send = send or (lambda msgs: push.send(msgs))
    now = _now()
    tokens = push.active_tokens(pdb)
    if not tokens:
        return 0
    msgs = _high_messages(ev, pdb, tokens, now) + _settle_messages(pdb, tokens, now)
    msgs += _price_move_messages(pdb, tokens, now)
    if msgs:
        send(msgs)
    return len(msgs)


def main():
    ev = sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True, timeout=15)
    pdb = adb.connect()
    try:
        n = run_once(ev, pdb)
        print(f"[softbook-push] sent {n} messages")
    finally:
        ev.close()
        pdb.close()


if __name__ == "__main__":
    main()
