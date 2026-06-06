#!/usr/bin/env python3
"""Paper PnL for the soft-book +EV strategy — 'what if I'd bet every recommendation'.

Builds a PAPER bet ledger from the recorded +EV opportunities (softbook_ev.db),
applying the SAME rules the live bet list uses, so it measures the real strategy:
  - pre-game only        (commence_time strictly AFTER the snapshot time)
  - odds band            1.50 <= price <= 4.00
  - EV band              0.01 <= snapshot_ev <= 0.08
  - one bet per game     = the EARLIEST qualifying snapshot (the side you'd see
                           first; look-ahead-free, even if the line later flips)
  - stake                = the strategy's quarter-Kelly (capped 2% bankroll, min $2)

Each paper bet is locked into the `paper_bet` table (INSERT OR IGNORE, so a bet
never changes once placed), then settled against the Odds API /scores result.
Reports realized PnL / ROI / win-rate. PAPER = no real money; compare to your
actual bets later.

  python scripts/softbook_ev_paper_pnl.py            # place + settle + report
  python scripts/softbook_ev_paper_pnl.py --report   # report only (no API calls)
"""
import os, json, urllib.request, sqlite3, sys
from datetime import datetime, timezone

SRC_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
BANKROLL = float(os.getenv("SOFTBOOK_BANKROLL", "240"))
ODDS_MIN, ODDS_MAX = 1.50, 4.00
EV_MIN, EV_MAX = 0.01, 0.08


def _key():
    for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
        if line.startswith("ODDS_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("ODDS_API_KEY")


def _dt(s):
    """Parse an ISO timestamp (handles both 'Z' and '+00:00'); None on failure."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _num(x):
    try:
        return int(x)
    except Exception:
        try:
            return float(x)
        except Exception:
            return None


def init_db(db):
    db.execute("""CREATE TABLE IF NOT EXISTS paper_bet(
        event_id TEXT PRIMARY KEY, sport TEXT, team TEXT, opponent TEXT,
        commence_time TEXT, book TEXT, odds REAL, ev REAL, stake REAL,
        placed_at TEXT,
        completed INTEGER DEFAULT 0, won INTEGER, result_winner TEXT,
        pnl REAL, settled_at TEXT)""")
    db.commit()


def _band(odds):
    if odds < 2.00:
        return "a) 1.50-2.00"
    if odds < 3.00:
        return "b) 2.00-3.00 (sweet)"
    return "c) 3.00-4.00"


def candidates(db):
    """One bet per game = earliest qualifying (pre-game, in-band) snapshot."""
    rows = db.execute("""SELECT event_id, sport, team, opponent, commence_time,
        recorded_at, best_soft_book, best_soft_price, snapshot_ev
        FROM softbook_ev ORDER BY event_id, recorded_at""").fetchall()
    seen, out = set(), []
    for ev_id, sport, team, opp, ct, rec, book, px, ev in rows:
        if ev_id in seen:
            continue
        cdt, rdt = _dt(ct), _dt(rec)
        if not (cdt and rdt and cdt > rdt):        # pre-game at record time only
            continue
        if not (ODDS_MIN <= px <= ODDS_MAX):
            continue
        if not (EV_MIN <= ev <= EV_MAX):
            continue
        seen.add(ev_id)
        kelly = ev / (px - 1) if px > 1 else 0
        stake = max(2.0, min(BANKROLL * 0.02, BANKROLL * kelly * 0.25))
        out.append((ev_id, sport, team, opp, ct, book, px, ev, round(stake, 2), rec))
    return out


def place(paper, src):
    n = 0
    for c in candidates(src):
        cur = paper.execute(
            """INSERT OR IGNORE INTO paper_bet
               (event_id, sport, team, opponent, commence_time, book, odds, ev,
                stake, placed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", c)
        n += cur.rowcount
    paper.commit()
    return n


def settle(db, key):
    sports = [r[0] for r in db.execute(
        "SELECT DISTINCT sport FROM paper_bet WHERE completed=0").fetchall()]
    settled = 0
    for sport in sports:
        try:
            url = (f"https://api.the-odds-api.com/v4/sports/{sport}/scores/"
                   f"?apiKey={key}&daysFrom=3")
            games = json.loads(urllib.request.urlopen(url, timeout=25).read())
        except Exception as e:
            print(f"  scores {sport}: {e}")
            continue
        results = {}
        for g in games:
            if not g.get("completed"):
                continue
            sc = {s["name"]: _num(s["score"]) for s in (g.get("scores") or [])}
            if len(sc) < 2 or any(v is None for v in sc.values()):
                continue
            hi = max(sc.values())
            winners = [nm for nm, v in sc.items() if v == hi]
            results[g["id"]] = winners[0] if len(winners) == 1 else None  # None = tie/void
        for ev_id, team, odds, stake in db.execute(
                "SELECT event_id, team, odds, stake FROM paper_bet "
                "WHERE completed=0 AND sport=?", (sport,)).fetchall():
            if ev_id not in results:
                continue
            winner = results[ev_id]
            if winner is None:                       # tie -> void (stake back)
                db.execute("UPDATE paper_bet SET completed=1, won=NULL, "
                           "result_winner=NULL, pnl=0, settled_at=? WHERE event_id=?",
                           (datetime.now(timezone.utc).isoformat(), ev_id))
            else:
                won = int(winner == team)
                pnl = stake * (odds - 1) if won else -stake
                db.execute("UPDATE paper_bet SET completed=1, won=?, result_winner=?, "
                           "pnl=?, settled_at=? WHERE event_id=?",
                           (won, winner, round(pnl, 2),
                            datetime.now(timezone.utc).isoformat(), ev_id))
            settled += 1
    db.commit()
    return settled


def report(db):
    rows = db.execute("""SELECT sport, team, opponent, odds, ev, stake, commence_time,
        completed, won, result_winner, pnl FROM paper_bet ORDER BY commence_time""").fetchall()
    if not rows:
        print("No paper bets yet (no qualifying pre-game / in-band recommendations recorded).")
        return
    settled = [r for r in rows if r[7]]
    pending = [r for r in rows if not r[7]]
    print(f"\n=== SOFT-BOOK +EV  PAPER PnL  (bankroll ${BANKROLL:.0f}, PAPER — no real money) ===")
    print("Rule: pre-game, odds 1.50-4.00, EV 1-8%, one bet/game (earliest side), 1/4-Kelly stake.\n")
    if settled:
        print("  settled bets:")
        for sp, team, opp, odds, ev, stake, ct, comp, won, win, pnl in settled:
            mark = "void" if won is None else ("WON " if won else "lost")
            print(f"   {ct[:10]} {team[:20]:<20} {odds:>5.2f} ${stake:>5.2f}  {mark}  "
                  f"PnL {pnl:+6.2f}   ({sp.replace('_',' ')})")
    staked = sum(r[5] for r in settled if r[8] is not None)   # exclude voids from staked
    net = sum(r[10] for r in settled)
    graded = [r for r in settled if r[8] is not None]
    wins = sum(r[8] for r in graded)
    roi = (net / staked * 100) if staked else 0.0
    print("\n  ----- TOTALS -----")
    print(f"   paper bets:   {len(rows)}   (settled {len(settled)}, pending {len(pending)}, "
          f"void {len(settled)-len(graded)})")
    if graded:
        print(f"   record:       {wins}-{len(graded)-wins}   (win rate {100*wins/len(graded):.0f}%)")
        print(f"   staked:       ${staked:.2f}")
        print(f"   net PnL:      ${net:+.2f}")
        print(f"   ROI:          {roi:+.1f}%   <<< (PAPER, n={len(graded)} — far too small to trust)")
        # by odds band
        print("\n   by odds band:")
        bands = {}
        for r in graded:
            b = _band(r[3]); bands.setdefault(b, [0, 0.0, 0])
            bands[b][0] += 1; bands[b][1] += r[10]; bands[b][2] += r[8]
        for b in sorted(bands):
            n, p, w = bands[b]
            print(f"     {b:<22} n={n:<3} {w}-{n-w}  PnL ${p:+.2f}")
    else:
        print("   no graded results yet — games not completed / not in /scores window.")
    if pending:
        print(f"\n   pending ({len(pending)}): " +
              ", ".join(f"{r[1][:14]}({r[6][5:10]})" for r in pending[:12]))
    print("\n   NOTE: PAPER = the strategy's modeled bets, not your real wagers. Realized")
    print("   edge needs ~1-2 weeks (n>=~30) + positive CLV to be trustworthy.")


def main():
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    paper = sqlite3.connect(PAPER_DB)
    init_db(paper)
    if "--report" not in sys.argv:
        key = _key()
        if not key:
            print("no ODDS_API_KEY")
        else:
            placed = place(paper, src)
            done = settle(paper, key)
            print(f"[paper-pnl] placed {placed} new bets, settled {done}.")
    report(paper)


if __name__ == "__main__":
    main()
