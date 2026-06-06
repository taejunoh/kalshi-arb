#!/usr/bin/env python3
"""Verify the soft-book +EV edge: closing-line value (CLV) + (optional) settlement.

For each recorded +EV opportunity (the OPEN snapshot, earliest record per
event+team), find the LATEST snapshot's Pinnacle fair prob for the same
event+team = the CLOSING sharp prob. CLV = closing_fair - opening_fair on the
side we'd have bet: if positive, the sharp line MOVED TOWARD our bet after we
took the soft price = the bet was genuinely +EV (we beat the close). This is the
gold-standard test that the +EV-betting edge is real, not stale-line noise.

A diligent +EV bettor's bets show systematically positive CLV; ~0 CLV means the
divergences were stale and mean-reverted (no real edge).
"""
import sqlite3, os, statistics
from collections import defaultdict

DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")


def main():
    db = sqlite3.connect(DB)
    rows = db.execute("""SELECT event_id, team, recorded_at, pinnacle_fair_prob,
                                snapshot_ev, best_soft_book, best_soft_price
                         FROM softbook_ev ORDER BY event_id, team, recorded_at""").fetchall()
    # group by (event, team) -> chronological fair-prob track
    g = defaultdict(list)
    for ev_id, team, ts, fair, ev, bk, px in rows:
        g[(ev_id, team)].append((ts, fair, ev, bk, px))
    clvs = []
    detail = []
    for (ev_id, team), track in g.items():
        if len(track) < 2:
            continue
        track.sort()
        open_ts, open_fair, open_ev, bk, px = track[0]   # when we took the +EV soft bet
        close_fair = track[-1][1]                         # latest sharp fair prob = ~closing
        clv = close_fair - open_fair       # >0 = sharp moved toward our side after we bet = beat close
        clvs.append(clv)
        detail.append((ev_id[:8], team[:14], open_ev, clv, bk))
    print(f"# {len(g)} unique +EV opportunities recorded; {len(clvs)} with >=2 snapshots (CLV-measurable)")
    if len(clvs) < 15:
        print("not enough closed opportunities yet — keep recording (need ~1-2 weeks).")
        return
    mean_clv = statistics.mean(clvs)
    pos = sum(1 for c in clvs if c > 0)
    print(f"\nCLV (closing sharp prob - opening sharp prob on our bet side):")
    print(f"  mean CLV = {mean_clv*100:+.2f}pp   | positive: {pos}/{len(clvs)} ({100*pos//len(clvs)}%)")
    # bootstrap CI
    import random
    rnd = random.Random(7)
    boots = [statistics.mean([clvs[rnd.randrange(len(clvs))] for _ in clvs]) for _ in range(3000)]
    boots.sort()
    lo, hi = boots[int(0.025*len(boots))], boots[int(0.975*len(boots))]
    print(f"  bootstrap CI95 [{lo*100:+.2f}, {hi*100:+.2f}]pp  {'<<< REAL edge (CLV>0)' if lo>0 else 'spans 0 / stale-noise'}")
    print("\nVERDICT: positive mean CLV with CI>0 = the soft-book +EV edge is REAL & capturable")
    print("(diligent +EV bets beat the closing sharp line). ~0 CLV = divergences were stale noise.")


if __name__ == "__main__":
    main()
