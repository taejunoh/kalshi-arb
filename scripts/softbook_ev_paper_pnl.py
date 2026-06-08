#!/usr/bin/env python3
"""Paper PnL for the soft-book +EV strategy — FULL markets (ML / spread / total).

Builds a PAPER bet ledger from recorded +EV opps (softbook_ev.db) applying the
live rules — pre-game, one bet per (game, MARKET) = earliest qualifying snapshot,
quarter-Kelly stake — then settles each vs the Odds API /scores final score
(moneyline = winner, spread = margin, total = run/point sum; see core.grade).
Reports realized PnL / ROI / win-rate by market and odds band. PAPER only.

  python scripts/softbook_ev_paper_pnl.py            # place + settle + report
  python scripts/softbook_ev_paper_pnl.py --report   # report only (no API)
  python scripts/softbook_ev_paper_pnl.py --telegram # also push a digest
"""
import os, json, urllib.request, urllib.parse, sqlite3, sys
from datetime import datetime, timezone
import softbook_ev_core as core
import softbook_ev_stats as stats

SRC_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
BANKROLL = float(os.getenv("SOFTBOOK_BANKROLL", "240"))

SCHEMA = """CREATE TABLE IF NOT EXISTS paper_bet(
    event_id TEXT, sport TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
    point REAL, player TEXT, label TEXT, commence_time TEXT, book TEXT, odds REAL,
    ev REAL, stake REAL, placed_at TEXT, tier TEXT, conf REAL,
    completed INTEGER DEFAULT 0, won INTEGER,
    result TEXT, pnl REAL, settled_at TEXT, PRIMARY KEY (event_id, market, player))"""


def _dt(s):
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


def init_db(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(paper_bet)")]
    if cols and "player" not in cols:           # one-time migrate (adds player for props)
        db.execute("DROP TABLE paper_bet")
    db.execute(SCHEMA)
    cols = [r[1] for r in db.execute("PRAGMA table_info(paper_bet)")]
    for col, typ in (("tier", "TEXT"), ("conf", "REAL")):   # additive migrate (track confidence)
        if col not in cols:
            db.execute(f"ALTER TABLE paper_bet ADD COLUMN {col} {typ}")
    db.commit()


def backfill_tiers(paper, src):
    """Fill tier/conf on bets placed before the ledger tracked them — from the
    earliest recorded snapshot of each (event, market, player)."""
    tmap = {}
    for eid, market, player, tier, conf in src.execute(
            """SELECT event_id, market, COALESCE(player, ''), tier, conf FROM softbook_ev
               ORDER BY event_id, market, COALESCE(player, ''), recorded_at"""):
        tmap.setdefault((eid, market, player), (tier, conf))     # first = earliest
    n = 0
    for (eid, market, player), (tier, conf) in tmap.items():
        n += paper.execute(
            """UPDATE paper_bet SET tier=?, conf=? WHERE event_id=? AND market=?
               AND COALESCE(player, '')=? AND tier IS NULL""",
            (tier, conf, eid, market, player)).rowcount
    paper.commit()
    return n


def _band(odds):
    if odds < 2.00:
        return "a) 1.50-2.00"
    if odds < 3.00:
        return "b) 2.00-3.00 (sweet)"
    return "c) 3.00-4.00"


def candidates(src):
    """One bet per (game, market) = earliest qualifying (pre-game) snapshot."""
    try:
        rows = src.execute("""SELECT event_id, sport, home, away, market, side, point,
            player, commence_time, recorded_at, best_soft_book, best_soft_price, snapshot_ev,
            tier, conf
            FROM softbook_ev ORDER BY event_id, market, player, recorded_at""").fetchall()
    except Exception:
        return []
    seen, out = set(), []
    for (eid, sport, home, away, market, side, point, player, ct, rec, book, odds, ev,
         tier, conf) in rows:
        k = (eid, market, player)
        if k in seen:
            continue
        cdt, rdt = _dt(ct), _dt(rec)
        if not (cdt and rdt and cdt > rdt):        # pre-game at record time
            continue
        seen.add(k)
        kelly = ev / (odds - 1) if odds > 1 else 0
        stake = round(max(2.0, min(BANKROLL * 0.02, BANKROLL * kelly * 0.25)), 2)
        out.append((eid, sport, home, away, market, side, point, player or "",
                    core.label(market, side, point, player), ct, book, odds, ev, stake, rec,
                    tier, conf))
    return out


def place(paper, src):
    n = 0
    for c in candidates(src):
        cur = paper.execute(
            """INSERT OR IGNORE INTO paper_bet
               (event_id, sport, home, away, market, side, point, player, label,
                commence_time, book, odds, ev, stake, placed_at, tier, conf)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", c)
        n += cur.rowcount
    paper.commit()
    return n


def settle(paper, key):
    sports = [r[0] for r in paper.execute(
        "SELECT DISTINCT sport FROM paper_bet WHERE completed=0").fetchall()]
    done = 0
    for sport in sports:
        # team scores for GAME markets via Odds API (props settle from a stats source)
        scores = {}
        try:
            games = json.loads(urllib.request.urlopen(
                f"{core.B}/{sport}/scores/?apiKey={key}&daysFrom=3", timeout=25).read())
            for g in games:
                if not g.get("completed"):
                    continue
                sc = {s["name"]: _num(s["score"]) for s in (g.get("scores") or [])}
                if len(sc) >= 2 and all(v is not None for v in sc.values()):
                    scores[g["id"]] = sc
        except Exception as e:
            print(f"  scores {sport}: {e}")
        for (eid, market, side, point, player, home, away, ct, odds, stake) in paper.execute(
                """SELECT event_id, market, side, point, player, home, away,
                          commence_time, odds, stake
                   FROM paper_bet WHERE completed=0 AND sport=?""", (sport,)).fetchall():
            if market in core.ALL_PROPS:                      # PROP -> player stat
                stat = stats.player_stat(sport, away, home, ct, market, player)
                res = core.grade_prop(side, point, stat)
            else:                                             # game market -> team score
                sc = scores.get(eid)
                res = core.grade(market, side, point, home, away, sc) if sc else None
            if res is None:
                continue
            won = 1 if res == "win" else (0 if res == "lose" else None)
            pnl = stake * (odds - 1) if res == "win" else (-stake if res == "lose" else 0)
            paper.execute("""UPDATE paper_bet SET completed=1, won=?, result=?, pnl=?,
                settled_at=? WHERE event_id=? AND market=? AND player=?""",
                (won, res, round(pnl, 2), datetime.now(timezone.utc).isoformat(),
                 eid, market, player))
            done += 1
    paper.commit()
    return done


def report(db, tg=False):
    rows = db.execute("""SELECT sport, market, label, odds, stake, commence_time,
        completed, won, result, pnl, tier FROM paper_bet ORDER BY commence_time""").fetchall()
    if not rows:
        print("No paper bets yet (no qualifying pre-game +EV recommendations recorded).")
        return
    settled = [r for r in rows if r[6]]
    pending = [r for r in rows if not r[6]]
    graded = [r for r in settled if r[7] is not None]          # exclude pushes
    wins = sum(r[7] for r in graded)
    staked = sum(r[4] for r in graded)
    net = sum(r[9] for r in settled)
    roi = (net / staked * 100) if staked else 0.0
    print(f"\n=== SOFT-BOOK +EV  PAPER PnL  (ML/spread/total, bankroll ${BANKROLL:.0f}, PAPER) ===")
    print("Rule: pre-game, odds 1.50-4.00, EV 1-8%, one bet per game+market, 1/4-Kelly.\n")
    for sp, mk, lbl, odds, stake, ct, comp, won, res, pnl, tier in settled:
        mark = {"win": "WON ", "lose": "lost", "push": "push"}.get(res, res or "?")
        print(f"  {ct[:10]} {mk}  {lbl:<22} {odds:>5.2f} ${stake:>5.2f}  PnL {pnl:+6.2f}  ({mk})")
    print("\n  ----- TOTALS -----")
    print(f"   paper bets:  {len(rows)}  (settled {len(settled)}, pending {len(pending)}, "
          f"push {len(settled)-len(graded)})")
    if graded:
        print(f"   record:      {wins}-{len(graded)-wins}  (win rate {100*wins/len(graded):.0f}%)")
        print(f"   staked:      ${staked:.2f}    net PnL: ${net:+.2f}    ROI: {roi:+.1f}%   "
              f"<<< PAPER, n={len(graded)}")
        tord = {"High": 0, "Med": 1, "Low": 2}
        for grp, keyfn in (("confidence tier", lambda r: r[10] or "?"),
                           ("market", lambda r: r[1]),
                           ("odds band", lambda r: _band(r[3]))):
            agg = {}
            for r in graded:
                a = agg.setdefault(keyfn(r), [0, 0.0, 0.0, 0])
                a[0] += 1; a[1] += r[9]; a[2] += r[4]; a[3] += r[7]      # n, pnl, staked, wins
            order = sorted(agg, key=lambda x: tord.get(x, 9)) if grp == "confidence tier" else sorted(agg)
            print(f"   by {grp}:")
            for kk in order:
                nn, pp, st, ww = agg[kk]
                roi = (pp / st * 100) if st else 0.0
                print(f"     {kk:<22} n={nn:<3} {ww}-{nn-ww}  PnL ${pp:+.2f}  ROI {roi:+.1f}%")
    else:
        print("   no graded results yet — games not completed / not in /scores window.")
    if pending:
        print(f"\n   pending ({len(pending)}): " + ", ".join(f"{r[2]}" for r in pending[:10]))
    print("\n   NOTE: PAPER = modeled bets, not real wagers. Trust needs ~1-2wk (n>=~30) + CLV.")
    if tg:
        if graded:
            tp = {}
            for r in graded:
                a = tp.setdefault(r[10] or "?", [0, 0.0]); a[0] += 1; a[1] += r[9]
            tline = " · ".join(f"{t} {tp[t][0]}건 ${tp[t][1]:+.0f}"
                               for t in ("High", "Med", "Low") if t in tp)
            msg = ("📊 소프트북 +EV 페이퍼 PnL (ML/스프레드/토탈)\n"
                   f"전적 {wins}-{len(graded)-wins} (승률 {100*wins/len(graded):.0f}%)\n"
                   f"스테이크 ${staked:.2f} | 순익 ${net:+.2f} | ROI {roi:+.1f}%\n"
                   + (f"tier: {tline}\n" if tline else "")
                   + f"정산 {len(graded)} · 대기 {len(pending)}\n"
                   f"⚠️ n={len(graded)} — 1~2주 더 쌓여야 신뢰 가능 (PAPER)")
        else:
            msg = (f"📊 소프트북 +EV 페이퍼 PnL\n정산된 베팅 아직 없음 "
                   f"(대기 {len(pending)}). 파이프라인 정상.")
        send_telegram(msg)


def main():
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    paper = sqlite3.connect(PAPER_DB)
    init_db(paper)
    filled = backfill_tiers(paper, src)        # label pre-tracking bets (idempotent)
    if "--report" not in sys.argv:
        key = core.read_key()
        if not key:
            print("no ODDS_API_KEY")
        else:
            placed = place(paper, src)
            done = settle(paper, key)
            print(f"[paper-pnl] placed {placed} new bets, settled {done}, "
                  f"backfilled tier on {filled}.")
    report(paper, tg=("--telegram" in sys.argv))


if __name__ == "__main__":
    main()
