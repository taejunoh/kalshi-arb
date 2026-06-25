#!/usr/bin/env python3
"""Generate results.json for keepmypick.com from the live DB.

The public track-record page (web/index.html) fetches this file. It contains the
FIRST-ENTRY GAME record only (core.is_bet — the actual product / single source),
per-market + a graded ledger, mirroring exactly what the app reports.

Usage (run on EC2, cwd = scripts/ so it can import softbook_ev_core):
    venv/bin/python keepmypick_results_export.py [OUTPUT_PATH]
Writes JSON to OUTPUT_PATH if given, else prints to stdout.
"""
import sqlite3, json, sys
from datetime import datetime, timezone
import softbook_ev_core as core

EV_DB = "/home/ubuntu/kalshi-arb/data/softbook_ev.db"
PAPER_DB = "/home/ubuntu/kalshi-arb/data/softbook_paper.db"


def main():
    ev = sqlite3.connect(EV_DB)
    pp = sqlite3.connect(PAPER_DB)
    cache = {}

    def is_fe(eid, market, side, player):
        k = (eid, market, player or "")
        if k not in cache:
            try:
                cache[k] = core.market_first_entry_sides(ev, *k)
            except Exception:
                cache[k] = (False, frozenset())
        seen, sides = cache[k]
        return seen and (side in sides)

    rows = pp.execute(
        "SELECT event_id, date(placed_at) d, sport, label, market, side, player, "
        "book, ROUND(odds,2) odds, stake, result, ROUND(pnl,2) pnl "
        "FROM paper_bet WHERE market IN ('h2h','spread','total') AND result IS NOT NULL "
        "ORDER BY placed_at"
    ).fetchall()

    bm = {m: {"market": m, "w": 0, "l": 0, "pnl": 0.0, "staked": 0.0}
          for m in ("h2h", "spread", "total")}
    ledger = []
    for eid, d, sport, label, market, side, player, book, odds, stake, result, pnl in rows:
        if not is_fe(eid, market, side, player):
            continue
        ledger.append({"d": d, "sport": sport, "label": label, "market": market,
                       "book": book, "odds": odds, "result": result, "pnl": pnl})
        b = bm[market]
        if result == "win":
            b["w"] += 1; b["pnl"] += pnl or 0; b["staked"] += stake or 0
        elif result == "lose":
            b["l"] += 1; b["pnl"] += pnl or 0; b["staked"] += stake or 0
        # push/void stay in the ledger (pnl 0) but are excluded from W-L / ROI

    byMarket = []
    for m in ("h2h", "spread", "total"):
        b = bm[m]
        roi = 100.0 * b["pnl"] / b["staked"] if b["staked"] else 0.0
        byMarket.append({"market": m, "w": b["w"], "l": b["l"],
                         "pnl": round(b["pnl"], 2), "roi": round(roi, 2)})

    W = sum(b["w"] for b in bm.values())
    L = sum(b["l"] for b in bm.values())
    PNL = sum(b["pnl"] for b in bm.values())
    STK = sum(b["staked"] for b in bm.values())
    n = W + L
    roi = 100.0 * PNL / STK if STK else 0.0
    win = 100.0 * W / n if n else 0.0

    dates = [x["d"] for x in ledger if x["d"]]

    def fmt(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d")
        except Exception:
            return d or ""

    out = {
        "roi": round(roi, 2), "pnl": round(PNL, 2), "n": n, "w": W, "l": L,
        "win": round(win, 1),
        "since": fmt(min(dates)) if dates else "",
        "until": fmt(max(dates)) if dates else "",
        "updated": datetime.now(timezone.utc).strftime("%b %-d, %Y"),
        "byMarket": byMarket, "ledger": ledger,
    }
    js = json.dumps(out, ensure_ascii=False)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(js)
        sys.stderr.write("wrote %s  (%d picks, ROI %+.2f%%, %d-%d)\n"
                         % (sys.argv[1], len(ledger), roi, W, L))
    else:
        print(js)


if __name__ == "__main__":
    main()
