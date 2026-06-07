#!/usr/bin/env python3
"""Verify the soft-book +EV edge: closing-line value (CLV) — FULL markets.

For each recorded +EV bet (unique event+market+side+line), compare the OPENING
snapshot's Pinnacle fair prob to the LATEST (~closing) snapshot's fair prob on
that same bet. CLV = closing_fair - opening_fair: positive = the sharp line moved
TOWARD our bet after we took the soft price = genuinely +EV (we beat the close).
Diligent +EV bets show systematically positive CLV; ~0 = stale-line noise.
"""
import sqlite3, os, statistics, random
from collections import defaultdict

DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")


def _band(px):
    if px < 1.50:
        return "a) <1.50 (heavy fav)"
    if px <= 4.00:
        return "b) 1.50-4.00 (BET band)"
    return "c) >4.00 (longshot)"


def main():
    db = sqlite3.connect(DB)
    try:
        rows = db.execute("""SELECT event_id, market, side, point, player, recorded_at,
            pinnacle_fair_prob, best_soft_price FROM softbook_ev
            ORDER BY event_id, market, side, point, player, recorded_at""").fetchall()
    except Exception as e:
        print("read err (schema?):", e); return
    g = defaultdict(list)
    for eid, market, side, point, player, ts, fair, px in rows:
        g[(eid, market, side, point, player)].append((ts, fair, px))
    clvs, px_clv, mkt = [], [], defaultdict(list)
    for k, track in g.items():
        if len(track) < 2:
            continue
        track.sort()
        clv = track[-1][1] - track[0][1]          # close fair - open fair
        clvs.append(clv); px_clv.append((track[0][2], clv)); mkt[k[1]].append(clv)
    print(f"# {len(g)} unique +EV bets recorded; {len(clvs)} with >=2 snapshots (CLV-measurable)")
    if len(clvs) < 15:
        print("not enough closed opportunities yet — keep recording (need ~1-2 weeks).")
        return
    mean_clv = statistics.mean(clvs)
    pos = sum(1 for c in clvs if c > 0)
    print(f"\nCLV (closing sharp prob - opening, on our bet side):")
    print(f"  mean CLV = {mean_clv*100:+.2f}pp   | positive: {pos}/{len(clvs)} ({100*pos//len(clvs)}%)")
    rnd = random.Random(7)
    boots = sorted(statistics.mean([clvs[rnd.randrange(len(clvs))] for _ in clvs]) for _ in range(3000))
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
    print(f"  bootstrap CI95 [{lo*100:+.2f}, {hi*100:+.2f}]pp  "
          f"{'<<< REAL edge (CLV>0)' if lo > 0 else 'spans 0 / stale-noise'}")
    print("\nCLV by market:")
    for m in sorted(mkt):
        v = mkt[m]
        print(f"  {m:<8} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
    bands = defaultdict(list)
    for px, clv in px_clv:
        bands[_band(px)].append(clv)
    print("\nCLV by odds band (validates the 1.50-4.00 cut):")
    for b in sorted(bands):
        v = bands[b]
        print(f"  {b:<24} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
    print("\nVERDICT: positive mean CLV with CI>0 = the soft-book +EV edge is REAL & capturable.")


if __name__ == "__main__":
    main()
