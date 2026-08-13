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
            " n_books, conf, tier")
    tier_sql, tier_params = core.alertable_tiers_sql()
    rows = ev.execute(
        f"SELECT {cols} FROM softbook_ev WHERE {tier_sql} AND"
        " datetime(recorded_at) >= datetime('now','-1 day')", tier_params).fetchall()
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
            return " ⚖️ 반대방향 +EV 있음 (동시적중 불가)"
    return ""


def _is_first_entry(ev, r):
    """True if THIS SIDE was already High at the (event,market,player)'s EARLIEST
    softbook_ev snapshot — the 'first-entry' subset. Late-crossers (drifted into High
    only after the line moved, INCLUDING the opposite side after a steam) settled ~0
    (pure dilution). Side-aware via core (single source) — mirrors recorder._first_entry_note
    and the app /picks first_entry flag. On any lookup error, return True: never suppress
    a possibly-real first-entry alert (a missed +EV bet costs more than a stray push)."""
    try:
        _seen, sides = core.market_first_entry_sides(
            ev, r["event_id"], r["market"], r.get("player") or "")
    except sqlite3.Error:
        return True
    return r["side"] in sides


def _high_messages(ev, pdb, tokens, now):
    """Push ONE alert per NEW BET pick = FIRST-ENTRY GAME only (core.is_bet: h2h/
    spread/total & first-entry; props and late-crossers skip). Non-bet picks are
    still deduped into pushed_high (not re-evaluated each scan) but never alerted —
    the app feed still shows everything, tagged 🟣BET/⚪SKIP with a bet filter."""
    pdb.execute(PUSHED_HIGH)
    rows = _latest_high(ev)
    latest = max((r["recorded_at"] for r in rows), default=None)
    msgs = []
    # Same suppression as the Telegram tag: nothing the user already marked bet or
    # dismissed in the app. See rec.actioned_picks — single source, so the two
    # channels cannot disagree about what the user has already done.
    done = rec.actioned_picks()
    for r in rows:
        if r["recorded_at"] != latest:
            continue
        pk = (r["event_id"], r["market"], r["side"], r["point"] or 0, r["player"] or "")
        if (pk[0], pk[1], pk[2], float(pk[3] or 0), pk[4]) in done:
            continue
        cur = pdb.execute(
            "INSERT OR IGNORE INTO pushed_high VALUES (?,?,?,?,?,?)", (*pk, now))
        if not cur.rowcount:
            continue
        if r["tier"] not in core.bet_tiers(r["market"]):
            continue                                 # not the tier this market is bet at
        if not core.is_confirmable(r["n_books"]):
            continue                                 # single-book market — see core
        if not core.is_bet(_is_first_entry(ev, r), r["market"], r["tier"]):
            continue
        label = core.label(r["market"], r["side"],
                           None if r["market"] == "h2h" else r["point"],
                           r["player"] or None)
        pid = feed._encode_id(pk)
        # Lead with the matchup ("Away @ Home") — the bet label alone ("Over 167.5")
        # doesn't say which game it is. game-only here, so both teams are present.
        game = " @ ".join(t for t in (r["away"], r["home"]) if t)
        head = f"{game} · {label}" if game else label
        body = (f"{head} · {r['best_soft_book']} {rec._american(r['best_soft_price'])}"
                f" · EV {(r['snapshot_ev'] or 0)*100:+.1f}%" + _opp_note(rows, r))
        title = "🟣 First-entry game +EV"
        for tok in tokens:
            msgs.append(push.build_message(tok, title, body,
                                           data={"pick_id": pid}, category="high_pick"))
    pdb.commit()
    return msgs


def _settle_messages(pdb, tokens, now):
    pdb.execute(NOTIFIED_SETTLE)
    rows = pdb.execute(
        "SELECT a.id, a.label, a.won, a.pnl FROM app_bet a"
        " LEFT JOIN notified_settle n ON n.app_bet_id=a.id"
        " WHERE a.settled=1 AND a.not_placed=0 AND n.app_bet_id IS NULL").fetchall()
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
