#!/usr/bin/env python3
"""Capture the TRUE closing Pinnacle line for every alerted +EV pick.

The CLV blind spot: the recorder only logs a line while it is in the +EV band
(EV 1-8%), so when a soft line corrects it stops being recorded — and
softbook_ev_measure used the LAST in-band snapshot as the "close", which is biased
toward "still favorable" (truncation). Real CLV (did we beat the actual close?)
needs the Pinnacle line AT game start, regardless of band.

This runs every ~10 min: for each alerted line whose game starts within WINDOW
minutes, it re-fetches the current Pinnacle fair (even if the soft edge is gone)
and UPSERTs it into closing_line. Run repeatedly, the last capture before commence
is the de-facto close. softbook_ev_measure then reads closing_line as the true
close. Alerted-lines-only keeps the credit cost tiny.
"""
import os
import sqlite3
from datetime import datetime, timezone

import softbook_ev_core as core

DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
WINDOW_MIN = int(os.getenv("SOFTBOOK_CLOSE_WINDOW_MIN", "40"))

SCHEMA = """CREATE TABLE IF NOT EXISTS closing_line(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    close_fair REAL, close_soft REAL, captured_at TEXT, commence_time TEXT,
    PRIMARY KEY (event_id, market, side, point, player))"""


def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def alerted_lines(db):
    """Each alerted line + its sport/commence (from the recorded snapshots). A line
    with no softbook_ev row yet (sport/commence unknown) is skipped."""
    return db.execute(
        """SELECT h.event_id, h.market, h.side, COALESCE(h.point,0), COALESCE(h.player,''),
               e.sport, MAX(e.commence_time)
           FROM high_alerted h
           JOIN softbook_ev e ON e.event_id=h.event_id AND e.market=h.market
                AND e.side=h.side AND COALESCE(e.point,0)=COALESCE(h.point,0)
                AND COALESCE(e.player,'')=COALESCE(h.player,'')
           GROUP BY h.event_id, h.market, h.side, COALESCE(h.point,0), COALESCE(h.player,'')"""
    ).fetchall()


def capture(db, now_iso, quote_fn, window_min=WINDOW_MIN):
    """Fetch + UPSERT the closing line for every alerted line that is PRE-GAME and
    starts within window_min. quote_fn(sport, eid, market, side, point, player) ->
    (fair_or_None, soft_or_None). Returns the number of lines captured this run."""
    db.execute(SCHEMA)
    now = _dt(now_iso)
    n = 0
    for eid, market, side, point, player, sport, ct in alerted_lines(db):
        cdt = _dt(ct)
        if not (now and cdt):
            continue
        lead_min = (cdt - now).total_seconds() / 60.0
        if not (0 < lead_min <= window_min):       # pre-game, starting within window
            continue
        fair, soft = quote_fn(sport, eid, market, side, point, player)
        if fair is None:
            continue
        db.execute("INSERT OR REPLACE INTO closing_line VALUES (?,?,?,?,?,?,?,?,?)",
                   (eid, market, side, point, player, fair, soft, now_iso, ct))
        n += 1
    db.commit()
    return n


def _live_quote_fn(key):
    """Real fetch: one event-odds call per (sport,event) per run, cached."""
    cache = {}

    def q(sport, eid, market, side, point, player):
        ck = (sport, eid)
        if ck not in cache:
            try:
                cache[ck] = core.event_odds(sport, eid, key)[0]
            except Exception:
                cache[ck] = None
        data = cache[ck]
        if not data:
            return (None, None)
        fair, quotes = core.line_quotes(
            data, market, side, None if market == "h2h" else point, player or None)
        soft = max((p for _, p in quotes), default=None)
        return (fair, soft)

    return q


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY"); return
    now = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(DB, timeout=15)
    try:
        n = capture(db, now, _live_quote_fn(key))
    finally:
        db.close()
    print(f"[softbook-close] {now}: captured {n} closing lines -> {DB}")


if __name__ == "__main__":
    main()
