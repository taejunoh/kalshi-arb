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
import softbook_ev_npb as npb
import softbook_results as results

SRC_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
BANKROLL = float(os.getenv("SOFTBOOK_BANKROLL", "240"))

SCHEMA = """CREATE TABLE IF NOT EXISTS paper_bet(
    event_id TEXT, sport TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
    point REAL, player TEXT, label TEXT, commence_time TEXT, book TEXT, odds REAL,
    ev REAL, stake REAL, placed_at TEXT, tier TEXT, conf REAL,
    completed INTEGER DEFAULT 0, won INTEGER,
    result TEXT, pnl REAL, settled_at TEXT, PRIMARY KEY (event_id, market, player))"""

# Strategy-B twin: "bet every NEW High pick the moment it first turns High" — the
# user's actual behavior. One bet per EXACT line (side+point in the key), so a
# moved line / the other side after a sharp move enters as its own bet (these are
# correlated within a game — fine for signal measurement, not independent samples).
HIGH_SCHEMA = """CREATE TABLE IF NOT EXISTS paper_bet_high(
    event_id TEXT, sport TEXT, home TEXT, away TEXT, market TEXT, side TEXT,
    point REAL, player TEXT, label TEXT, commence_time TEXT, book TEXT, odds REAL,
    ev REAL, stake REAL, placed_at TEXT, tier TEXT, conf REAL,
    completed INTEGER DEFAULT 0, won INTEGER,
    result TEXT, pnl REAL, settled_at TEXT,
    PRIMARY KEY (event_id, market, side, point, player))"""


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
    db.execute(HIGH_SCHEMA)
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


def candidates_high(src):
    """Strategy-B candidates: each exact line (event, market, side, point, player)
    entered at the FIRST pre-game snapshot where tier=='High', at THAT snapshot's
    book/odds — i.e. what you'd get betting each new High pick as it appears.
    Quiet-hour (02-09 UTC) triggers COUNT: the user watches the dashboard and bets
    at night too (2026-06-11, his call — do NOT re-add a quiet filter here); the
    alerted-vs-obtained price gap is measured separately via real_fill replies."""
    try:
        rows = src.execute("""SELECT event_id, sport, home, away, market, side, point,
            player, commence_time, recorded_at, best_soft_book, best_soft_price,
            snapshot_ev, tier, conf FROM softbook_ev WHERE tier='High'
            ORDER BY event_id, market, side, point, player, recorded_at""").fetchall()
    except Exception:
        return []
    seen, out = set(), []
    for (eid, sport, home, away, market, side, point, player, ct, rec, book, odds, ev,
         tier, conf) in rows:
        k = (eid, market, side, point if point is not None else 0, player or "")
        if k in seen:
            continue
        cdt, rdt = _dt(ct), _dt(rec)
        if not (cdt and rdt and cdt > rdt):        # pre-game at the High trigger
            continue
        seen.add(k)
        kelly = ev / (odds - 1) if odds > 1 else 0
        stake = round(max(2.0, min(BANKROLL * 0.02, BANKROLL * kelly * 0.25)), 2)
        out.append((eid, sport, home, away, market, side,
                    point if point is not None else 0, player or "",
                    core.label(market, side, point, player), ct, book, odds, ev, stake,
                    rec, tier, conf))
    return out


def place_high(paper, src):
    n = 0
    for c in candidates_high(src):
        cur = paper.execute(
            """INSERT OR IGNORE INTO paper_bet_high
               (event_id, sport, home, away, market, side, point, player, label,
                commence_time, book, odds, ev, stake, placed_at, tier, conf)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", c)
        n += cur.rowcount
    paper.commit()
    return n


LEDGERS = ("paper_bet", "paper_bet_high")


def settle(paper, key):
    sports = set()
    for t in LEDGERS:
        sports |= {r[0] for r in paper.execute(
            f"SELECT DISTINCT sport FROM {t} WHERE completed=0").fetchall()}
    done = 0
    res_cache = {}                       # softbook_results per-(sport,date) fetch cache
    for sport in sports:
        # team scores for GAME markets via Odds API (props settle from a stats
        # source). Skip the (billed, always-empty) call for sports the Odds API
        # can't score — softbook_results sources those from ESPN/Naver instead.
        scores = {}
        if not results.handles(sport):
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
        for t in LEDGERS:
            for (eid, market, side, point, player, home, away, ct, odds, stake) in paper.execute(
                    f"""SELECT event_id, market, side, point, player, home, away,
                              commence_time, odds, stake
                       FROM {t} WHERE completed=0 AND sport=?""", (sport,)).fetchall():
                if results.handles(sport):                        # tennis/KBO/NCAA/MMA -> ESPN/Naver
                    res = results.grade_bet(sport, home, away, ct, market, side,
                                            point, player or "", cache=res_cache)
                elif market in core.ALL_PROPS:                    # PROP -> player stat
                    stat = stats.player_stat(sport, away, home, ct, market, player)
                    res = "void" if stat == stats.DNP else core.grade_prop(side, point, stat)
                elif market == "team_total":                      # player col holds the team
                    sc = scores.get(eid)
                    if sc is None and sport == "baseball_npb":
                        sc = npb.scores_for(away, home, ct)
                    res = core.grade_prop(side, point, sc.get(player)) if sc else None
                else:                                             # game market -> team score
                    sc = scores.get(eid)
                    if sc is None and sport == "baseball_npb":    # Odds API: no NPB scores
                        sc = npb.scores_for(away, home, ct)       # npb.jp official, final-only
                    res = core.grade(market, side, point, home, away, sc) if sc else None
                if res is None:
                    continue
                won = 1 if res == "win" else (0 if res == "lose" else None)
                pnl = stake * (odds - 1) if res == "win" else (-stake if res == "lose" else 0)
                paper.execute(f"""UPDATE {t} SET completed=1, won=?, result=?, pnl=?,
                    settled_at=? WHERE event_id=? AND market=? AND side=?
                    AND COALESCE(point,0)=COALESCE(?,0) AND COALESCE(player,'')=COALESCE(?,'')""",
                    (won, res, round(pnl, 2), datetime.now(timezone.utc).isoformat(),
                     eid, market, side, point, player))
                done += 1
    paper.commit()
    return done


STALE_DAYS = float(os.getenv("SOFTBOOK_STALE_DAYS", "4"))


def void_stale(paper):
    """Void bets whose game started STALE_DAYS+ ago but still can't be settled
    (Odds API has no scores feed for NPB/KBO/NCAA/WTA etc.). won=NULL keeps them
    out of the graded record; manual settlement inside the window beats this."""
    n = 0
    for t in LEDGERS:
        cur = paper.execute(
            f"""UPDATE {t} SET completed=1, won=NULL, result='stale-void', pnl=0, settled_at=?
               WHERE completed=0 AND datetime(commence_time) < datetime('now', ?)""",
            (datetime.now(timezone.utc).isoformat(), f"-{int(STALE_DAYS * 24)} hours"))
        n += cur.rowcount
    paper.commit()
    return n


def report_high(db):
    """Strategy-B scoreboard: ROI of 'bet each NEW High pick when it first fires'
    (the user's actual strategy). Returns (graded_n, wins, net, roi) or None."""
    try:
        rows = db.execute("""SELECT market, odds, stake, completed, won, pnl
            FROM paper_bet_high""").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    graded = [r for r in rows if r[3] and r[4] is not None]
    pend = sum(1 for r in rows if not r[3])
    wins = sum(r[4] for r in graded)
    staked = sum(r[2] for r in graded)
    net = sum(r[5] for r in graded)
    roi = (net / staked * 100) if staked else 0.0
    print(f"\n=== HIGH-TRIGGER strategy (bet each NEW High pick at its first High snapshot) ===")
    print(f"   {len(rows)} bets (graded {len(graded)}, pending {pend}, "
          f"void/push {len(rows)-len(graded)-pend})")
    if graded:
        print(f"   record: {wins}-{len(graded)-wins}  staked ${staked:.2f}  "
              f"PnL ${net:+.2f}  ROI {roi:+.1f}%   <<< measures the 'bet Highs' strategy")
        agg = {}
        for mk, odds, stake, comp, won, pnl in graded:
            a = agg.setdefault(mk, [0, 0, 0.0]); a[0] += 1; a[1] += won; a[2] += pnl
        for mk in sorted(agg):
            nn, ww, pp = agg[mk]
            print(f"     {mk:<22} n={nn:<3} {ww}-{nn-ww}  PnL ${pp:+.2f}")
    return (len(graded), wins, net, roi)


def report(db, tg=False, high=None):
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
          f"push/void {len(settled)-len(graded)})")
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
            hline = (f"🟣 HIGH-트리거 전략: {high[1]}-{high[0]-high[1]} | "
                     f"순익 ${high[2]:+.2f} | ROI {high[3]:+.1f}% (n={high[0]})\n"
                     if high and high[0] else "")
            fline = ""
            try:        # real fills logged by softbook_fill_listener replies.
                try:    # bonus col exists post 6/16 migration — exclude bonus bets
                    f_, s_, sl, bn = db.execute(
                        """SELECT sum(skipped=0 AND COALESCE(bonus,0)=0), sum(skipped=1),
                           avg(CASE WHEN skipped=0 AND COALESCE(bonus,0)=0 AND alert_odds>0
                               THEN 100.0*(fill_odds/alert_odds-1) END),
                           sum(COALESCE(bonus,0)=1)
                           FROM real_fill""").fetchone()
                except Exception:    # pre-migration real_fill (no bonus col)
                    f_, s_, sl = db.execute(
                        """SELECT sum(skipped=0), sum(skipped=1),
                           avg(CASE WHEN skipped=0 AND alert_odds>0
                               THEN 100.0*(fill_odds/alert_odds-1) END)
                           FROM real_fill""").fetchone()
                    bn = 0
                if (f_ or 0) + (s_ or 0) + (bn or 0):
                    co = 0
                    try:    # column exists post fill-listener 6/12 migration
                        co = db.execute("SELECT sum(cashed_out=1) FROM real_fill"
                                        ).fetchone()[0] or 0
                    except Exception:
                        pass
                    denom = (f_ or 0) + (s_ or 0)
                    fline = (f"🎯 실체결 {f_ or 0}건"
                             + (f" 슬리피지 {sl:+.1f}%" if sl is not None else "")
                             + f" · skip {s_ or 0}"
                             + (f" (노필 {100*(s_ or 0)/denom:.0f}%)" if denom else "")
                             + (f" · 캐시아웃 {co}" if co else "")
                             + (f" · 🎁보너스 {bn}" if bn else "") + "\n")
            except Exception:
                pass
            msg = ("📊 소프트북 +EV 페이퍼 PnL (ML/스프레드/토탈)\n"
                   f"전적 {wins}-{len(graded)-wins} (승률 {100*wins/len(graded):.0f}%)\n"
                   f"스테이크 ${staked:.2f} | 순익 ${net:+.2f} | ROI {roi:+.1f}%\n"
                   + (f"tier: {tline}\n" if tline else "") + hline + fline
                   + f"정산 {len(graded)} · 대기 {len(pending)}\n"
                   f"⚠️ n={len(graded)} — 1~2주 더 쌓여야 신뢰 가능 (PAPER)")
        else:
            msg = (f"📊 소프트북 +EV 페이퍼 PnL\n정산된 베팅 아직 없음 "
                   f"(대기 {len(pending)}). 파이프라인 정상.")
        try:        # stake-tiered promotion tracker (non-critical: never break the digest)
            import softbook_tripwire as tw
            msg += "\n\n" + tw.telegram_block(db)
        except Exception:
            pass
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
            placed_hi = place_high(paper, src)
            done = settle(paper, key)
            stale = void_stale(paper)
            print(f"[paper-pnl] placed {placed} new bets (+{placed_hi} High-trigger), "
                  f"settled {done}, backfilled tier on {filled}, stale-voided {stale}.")
    hi = report_high(paper)
    report(paper, tg=("--telegram" in sys.argv), high=hi)


if __name__ == "__main__":
    main()
