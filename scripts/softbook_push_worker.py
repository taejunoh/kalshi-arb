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


def _high_messages(ev, pdb, tokens, now):
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
        label = core.label(r["market"], r["side"],
                           None if r["market"] == "h2h" else r["point"],
                           r["player"] or None)
        pid = feed._encode_id(pk)
        body = (f"{label} · {r['best_soft_book']} {r['best_soft_price']:.2f}"
                f" · EV {(r['snapshot_ev'] or 0)*100:+.1f}%" + _opp_note(rows, r))
        for tok in tokens:
            msgs.append(push.build_message(tok, "🟣 New High +EV", body,
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


def run_once(ev, pdb, send=None):
    send = send or (lambda msgs: push.send(msgs))
    now = _now()
    tokens = push.active_tokens(pdb)
    if not tokens:
        return 0
    msgs = _high_messages(ev, pdb, tokens, now) + _settle_messages(pdb, tokens, now)
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
