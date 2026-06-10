#!/usr/bin/env python3
"""+EV-betting (soft-book vs sharp Pinnacle) forward CLV recorder — FULL markets.

Records, several times a day, the strategy's actual +EV bets across MONEYLINE,
SPREAD and TOTAL (best soft-book bet per game+market, vs Pinnacle's same-line
sharp price — see softbook_ev_core.py) so softbook_ev_measure.py can later verify
CLOSING-LINE VALUE (did these +EV bets beat the Pinnacle close?). NO BETTING —
record only. Run via kalshi-softbook-ev.timer.
"""
import os, sqlite3, urllib.request, urllib.parse
from datetime import datetime, timezone
import softbook_ev_core as core

OUT = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
QUIET_UTC = range(2, 9)            # 22:00-05:00 NYC: no pushes while user sleeps
BANKROLL = float(os.getenv("SOFTBOOK_BANKROLL", "240"))


def _stake(ev, odds):
    """Quarter-Kelly, $2 floor, 2% cap — same sizing as the paper ledgers."""
    kelly = ev / (odds - 1) if odds > 1 else 0
    return max(2.0, min(BANKROLL * 0.02, BANKROLL * kelly * 0.25))

SCHEMA = """CREATE TABLE IF NOT EXISTS softbook_ev(
    recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
    home TEXT, away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    pinnacle_fair_prob REAL, pinnacle_price REAL,
    best_soft_book TEXT, best_soft_price REAL, snapshot_ev REAL,
    n_books INTEGER, n_ev INTEGER, conf REAL, tier TEXT,
    UNIQUE(recorded_at, event_id, market, side, point, player))"""

ALERT_SCHEMA = """CREATE TABLE IF NOT EXISTS high_alerted(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT, alerted_at TEXT,
    PRIMARY KEY (event_id, market, side, point, player))"""


def send_telegram(text):
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="): tok = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("TELEGRAM_CHAT_ID="): cho = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return
    if not (tok and cho):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
    except Exception:
        pass


def _dup_note(db, b):
    """If this game+market(+player) was already High-alerted at other side/points,
    return a warning line: it's the SAME bet exposure (1/game/market), with the
    FULL chain of previously alerted lines in order (so line movement is visible
    at a glance) plus whether the new line beats the FIRST alert (the one most
    likely bet). A moved line re-alerts as a new bet, so without this the alert
    looks like a fresh opportunity when it's really a re-quote of one you may hold."""
    prior = db.execute(
        "SELECT side, point FROM high_alerted WHERE event_id=? AND market=? AND player=?"
        " AND NOT (side=? AND point=?) ORDER BY alerted_at",
        (b["event_id"], b["market"], b.get("player") or "",
         b["side"], b["point"] or 0)).fetchall()
    if not prior:
        return ""
    first_side, first_point = prior[0]
    same_side = all(s == b["side"] for s, _ in prior)
    if same_side and b["market"] != "h2h":     # compact chain: side once, points only
        chain = (f"{core.label(b['market'], b['side'], prior[0][1], b.get('player'))} → "
                 + " → ".join(f"{p:+g}" if b["market"] == "spread" else f"{p:g}"
                              for _, p in prior[1:] + [(None, b["point"] or 0)]))
    else:                                       # mixed sides (or ML): full labels
        chain = " → ".join(core.label(b["market"], s, p or None, b.get("player"))
                           for s, p in prior + [(b["side"], b["point"] or 0)])
    if first_side != b["side"]:
        return f"\n   ⚠️ 중복 {len(prior)+1}번째: {chain} — 처음 알림과 방향 반대, 이미 베팅했다면 스킵"
    new_point = b["point"] or 0
    if b["side"] == "Under":
        better = new_point > first_point
    elif b["side"] == "Over":
        better = new_point < first_point
    else:                                       # spread: more cushion = better
        better = new_point > first_point
    verdict = "더 좋은 라인" if better else "더 나쁜 라인"
    return (f"\n   ⚠️ 중복 {len(prior)+1}번째: {chain} (처음보다 {verdict})"
            f" — 이미 베팅했다면 스킵 (같은 경기·마켓 1베팅)")


def alert_new_high(db, bets, now):
    """Push NEW High-tier bets to Telegram as they appear (the 4x/day digest's
    fixed times miss them — High clusters near game start). Dedup per exact bet
    (event+market+side+point+player) so each line alerts once; a moved line is a
    new bet and re-alerts. Silent during NYC sleep hours (no mark, so it alerts
    after wake-up if still High and pre-game)."""
    if datetime.now(timezone.utc).hour in QUIET_UTC:
        return 0
    db.execute(ALERT_SCHEMA)
    new = []
    for b in bets:
        if b["tier"] != "High":
            continue
        cur = db.execute("INSERT OR IGNORE INTO high_alerted VALUES (?,?,?,?,?,?)",
                         (b["event_id"], b["market"], b["side"], b["point"] or 0,
                          b.get("player") or "", now))
        if cur.rowcount:
            new.append(b)
    db.commit()
    if not new:
        return 0
    new.sort(key=lambda b: -b["ev"])
    lines = [f"• {b['away'][:16]} @ {b['home'][:16]}\n"
             f"   {b['label']}\n"
             f"   📍 {b['book']} @ {b['odds']:.2f} · 💵 ${_stake(b['ev'], b['odds']):.0f}\n"
             f"   (EV {b['ev']*100:+.1f}%, {b['n_ev']}/{b['n_books']}bk)"
             + _dup_note(db, b) for b in new[:8]]
    send_telegram("🟣 NEW High-tier +EV (즉시 알림):\n\n" + "\n\n".join(lines)
                  + (f"\n\n…+{len(new)-8} more" if len(new) > 8 else "")
                  + "\n\npre-game only · bet small · 대쉬보드 +EV 탭 확인")
    return len(new)


def init_db():
    c = sqlite3.connect(OUT)
    cols = [r[1] for r in c.execute("PRAGMA table_info(softbook_ev)")]
    if cols and "tier" not in cols:          # one-time migrate (adds consensus/confidence)
        c.execute("DROP TABLE softbook_ev")
    c.execute(SCHEMA)
    c.commit()
    return c


def record(db, bets, now):
    """Persist one scan's +EV bets as CLV snapshots. Idempotent per (now, bet)."""
    n = 0
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
    return n


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY"); return
    now = datetime.now(timezone.utc).isoformat()
    db = init_db()
    n = credits = 0
    all_bets = []
    for sport in core.active_sports(key):
        try:
            bets, c, _ = core.scan_sport(sport, key)
        except Exception as e:
            print(f"{sport}: {e}"); continue
        credits += c
        all_bets += bets
        n += record(db, bets, now)
    alerted = alert_new_high(db, all_bets, now)
    print(f"[softbook-ev] {now}: recorded {n} +EV bets (ML/spread/total), "
          f"alerted {alerted} new High, {credits} credits -> {OUT}")


if __name__ == "__main__":
    main()
