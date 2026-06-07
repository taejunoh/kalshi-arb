#!/usr/bin/env python3
"""+EV-betting (soft-book vs sharp Pinnacle) forward CLV recorder — FULL markets.

Records, several times a day, the strategy's actual +EV bets across MONEYLINE,
SPREAD and TOTAL (best soft-book bet per game+market, vs Pinnacle's same-line
sharp price — see softbook_ev_core.py) so softbook_ev_measure.py can later verify
CLOSING-LINE VALUE (did these +EV bets beat the Pinnacle close?). NO BETTING —
record only. Run via kalshi-softbook-ev.timer.
"""
import os, sqlite3
from datetime import datetime, timezone
import softbook_ev_core as core

OUT = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")

SCHEMA = """CREATE TABLE IF NOT EXISTS softbook_ev(
    recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
    home TEXT, away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    pinnacle_fair_prob REAL, pinnacle_price REAL,
    best_soft_book TEXT, best_soft_price REAL, snapshot_ev REAL,
    n_books INTEGER, n_ev INTEGER, conf REAL, tier TEXT,
    UNIQUE(recorded_at, event_id, market, side, point, player))"""


def init_db():
    c = sqlite3.connect(OUT)
    cols = [r[1] for r in c.execute("PRAGMA table_info(softbook_ev)")]
    if cols and "tier" not in cols:          # one-time migrate (adds consensus/confidence)
        c.execute("DROP TABLE softbook_ev")
    c.execute(SCHEMA)
    c.commit()
    return c


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY"); return
    now = datetime.now(timezone.utc).isoformat()
    db = init_db()
    n = credits = 0
    for sport in core.active_sports(key):
        try:
            bets, c, _ = core.scan_sport(sport, key)
        except Exception as e:
            print(f"{sport}: {e}"); continue
        credits += c
        for b in bets:
            try:
                db.execute("INSERT OR IGNORE INTO softbook_ev VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, b["sport"], b["event_id"], b["commence_time"], b["home"], b["away"],
                     b["market"], b["side"], b["point"], b.get("player") or "",
                     b["fair"], b["pin_price"], b["book"], b["odds"], b["ev"],
                     b["n_books"], b["n_ev"], b["conf"], b["tier"]))
                n += 1
            except Exception:
                pass
        db.commit()
    print(f"[softbook-ev] {now}: recorded {n} +EV bets (ML/spread/total), {credits} credits -> {OUT}")


if __name__ == "__main__":
    main()
