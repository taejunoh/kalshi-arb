#!/usr/bin/env python3
"""Generate results.json for keepmypick.com from the live DB.

The public track-record page (web/index.html) fetches this file. It contains the
FIRST-ENTRY GAME record only (core.is_bet — the actual product / single source),
per-market + a graded ledger, mirroring exactly what the app reports.

Each bet is scored under the rule that was live ON THE DAY IT WAS PLACED
(core.bet_tiers_at), never under today's. This script re-derives the entire
record from scratch every morning, so scoring it with the current rule would
silently rewrite history: the 2026-08-05 ML High->Med change alone would have
moved the published record from 140-160 / +2.13% to 332-316 / +13.90% overnight
with no bet settling. A rule is changed because it looks better on the data you
already have, so re-scoring always flatters — that is an in-sample fit, not a
track record. The changes themselves ship in `ruleChanges` for the page to show.

Usage (run on EC2, cwd = scripts/ so it can import softbook_ev_core):
    venv/bin/python keepmypick_results_export.py [OUTPUT_PATH]
Writes JSON to OUTPUT_PATH if given, else prints to stdout.
"""
import sqlite3, json, sys
from datetime import datetime, timezone
import softbook_ev_core as core

# Human-readable companion to core.BET_TIER_ERAS — what changed, and when, for the
# public page. Keep in step with that tuple.
RULE_CHANGES = [
    {"date": "2026-08-05",
     "en": "Moneyline picks moved from the High to the Med confidence tier.",
     "ko": "머니라인 기준을 High → Med 신뢰등급으로 변경."},
    {"date": "2026-08-11",
     "en": "Markets quoted by a single sportsbook no longer count as picks.",
     "ko": "단독 호가(북 1곳뿐) 마켓은 픽에서 제외."},
    # Intake-only change: no published number moved and the 0-5 preseason
    # ledger rows stay. Disclosed anyway — the record shows preseason picks,
    # then none by our choice, then regular-season NFL in September; without
    # a dated note that reads as "quietly dropped it after it lost".
    {"date": "2026-08-14",
     "en": "NFL preseason excluded going forward (lines priced on roster "
           "guesswork). The 0-5 preseason record stays.",
     "ko": "NFL 프리시즌은 향후 제외 (로스터 불확실성으로 라인 품질 낮음). "
           "기존 0-5 기록은 그대로 유지."},
]

# The min-books exposure gate (core.is_confirmable) went live 2026-08-11: a pick
# only one book quotes is not surfaced anywhere, because such markets routinely
# exist in the Odds API without being purchasable at the book. This export was
# missed in that change, so the PUBLIC record kept counting picks nobody could
# take — 15 of them by 08-14, at -14.8% ROI, on a page that says "place each at
# the named sportsbook". Era-consistent like everything else here: bets placed
# BEFORE the gate existed were genuinely alerted and stay; from this date on,
# a 1-book market was never a pick, so it never enters the record.
MIN_BOOKS_SINCE = "2026-08-11"

EV_DB = "/home/ubuntu/kalshi-arb/data/softbook_ev.db"
PAPER_DB = "/home/ubuntu/kalshi-arb/data/softbook_paper.db"


def main():
    ev = sqlite3.connect(EV_DB)
    pp = sqlite3.connect(PAPER_DB)
    cache = {}

    def is_fe(eid, market, side, player, day):
        # keyed on the era, not the day: two bets under the same rule share a lookup
        era = next(st for st, _ in core.BET_TIER_ERAS if day >= st)
        k = (eid, market, player or "", era)
        if k not in cache:
            try:
                cache[k] = core.market_first_entry_sides(ev, *k[:3], as_of=day)
            except Exception:
                cache[k] = (False, frozenset())
        seen, sides = cache[k]
        return seen and (side in sides)

    nb_cache = {}

    def confirmable(eid, market, side, day):
        """core.is_confirmable, from the day the gate went live, on the book
        count at the side's earliest snapshot (same vintage first-entry uses)."""
        if day < MIN_BOOKS_SINCE:
            return True
        k = (eid, market, side)
        if k not in nb_cache:
            try:
                r = ev.execute(
                    "SELECT n_books FROM softbook_ev WHERE event_id=? AND market=?"
                    " AND side=? ORDER BY recorded_at LIMIT 1", k).fetchone()
                nb_cache[k] = r[0] if r else None
            except Exception:
                nb_cache[k] = None
        return core.is_confirmable(nb_cache[k])

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
        if not is_fe(eid, market, side, player, d):
            continue
        if not confirmable(eid, market, side, d):
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
        # Disclosed on the page. The record above is scored per era, so these are
        # what a reader needs to know the rule did not stay still.
        "ruleChanges": RULE_CHANGES,
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
