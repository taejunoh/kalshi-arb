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
            pinnacle_fair_prob, best_soft_price, tier FROM softbook_ev
            ORDER BY event_id, market, side, point, player, recorded_at""").fetchall()
    except Exception as e:
        print("read err (schema?):", e); return
    g = defaultdict(list)
    for eid, market, side, point, player, ts, fair, px, tier in rows:
        g[(eid, market, side, point, player)].append((ts, fair, px, tier))
    # TRUE closing fairs (softbook_close_capture: Pinnacle at commence, any band).
    # Falling back to the last in-band snapshot biases CLV UP (corrected lines were
    # truncated out), so prefer the true close whenever it exists.
    close = {}
    try:
        for eid, m, s, p, pl, cf in db.execute(
                "SELECT event_id, market, side, point, player, close_fair FROM closing_line"):
            close[(eid, m, s, p or 0, pl or "")] = cf
    except sqlite3.OperationalError:
        pass                                      # capturer not deployed yet
    clvs, px_clv, mkt, tier_clv, n_true = [], [], defaultdict(list), defaultdict(list), 0
    for k, track in g.items():
        track.sort()
        open_fair = track[0][1]
        ck = (k[0], k[1], k[2], k[3] or 0, k[4] or "")
        if ck in close:
            close_fair, is_true = close[ck], True
        elif len(track) >= 2:
            close_fair, is_true = track[-1][1], False
        else:
            continue                              # no close available -> unmeasurable
        n_true += is_true
        clv = close_fair - open_fair              # close fair - open fair
        clvs.append(clv); px_clv.append((track[0][2], clv)); mkt[k[1]].append(clv)
        tier_clv[track[0][3] or "?"].append(clv)  # by OPENING confidence tier
    print(f"# {len(g)} unique +EV bets recorded; {len(clvs)} CLV-measurable "
          f"({n_true} vs TRUE close, {len(clvs)-n_true} fallback to last in-band = biased UP)")
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
    print("\nCLV by confidence tier (does High actually beat the close more?):")
    for t in ("High", "Med", "Low", "?"):
        v = tier_clv.get(t, [])
        if v:
            print(f"  {t:<5} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
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
    if lo > 0:
        print("\nVERDICT: CI95 lower bound > 0 — soft-book +EV edge is REAL & capturable (we beat the close).")
    elif hi < 0:
        print("\nVERDICT: CI95 entirely < 0 — recorded bets LOSE to the close (stale-line / -EV); not capturable as-is.")
    else:
        print("\nVERDICT: CI95 spans 0 — no detectable CLV edge yet (stale-line noise). Keep recording.")


if __name__ == "__main__":
    main()
