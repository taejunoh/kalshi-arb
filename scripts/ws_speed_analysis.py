#!/usr/bin/env python3
"""Phase 1 speed-edge analysis — runs the three measurements on recorded WS tick
data (from kalshi_ws_record.py) and prints a GREEN/RED verdict on whether a
taker stale-quote / aggressor-follow edge survives OUR ~latency + cost.

OUR REACTION BUDGET (measured): feed ~42ms median + order ~34ms ≈ ~80-130ms.
We can only capture edges whose window EXCEEDS our latency in markets with volume.

Measurements:
  1. aggressor-follow  — after a trade-sweep, buying the aggressor side at the
     post-trade ask, is mid(t+Δ) - ask(t) - fee > 0 for Δ >= our latency?
  2. quote-staleness   — after a trade moves fair value, how long does the now-
     stale opposite quote persist (gap to next reprice)? >latency = pickoffable.
  3. repricing-speed   — inter-event + post-trade reprice latency by liquidity
     tier: is there a middle tier (slow enough to catch, liquid enough to trade)?

Usage: venv/bin/python scripts/ws_speed_analysis.py data/ws_ticks_YYYYMMDD.db
"""
import sys, sqlite3, math
import numpy as np
from bisect import bisect_left

LATENCY_MS = 130          # our floor reaction (feed+order). Edge must persist past this.
HORIZONS_MS = [130, 250, 500, 1000, 2000, 5000]
COST_C = 0.8              # ~half-spread(0.5) + real fee(~0.2-0.3c at mid) to take, cents


def load(db):
    c = sqlite3.connect(db)
    rows = c.execute("""SELECT recv_ms, exch_ms, ticker, mtype, yes_bid, yes_ask, taker_side, trade_price
                        FROM ticks ORDER BY ticker, recv_ms""").fetchall()
    return rows


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else "data/ws_ticks.db"
    rows = load(db)
    print(f"# {len(rows)} ticks from {db}")
    # feed latency
    lat = [r[0]-r[1] for r in rows if r[1] and r[1] > 1e12]
    if lat:
        print(f"## feed latency recv-exch: median={np.median(lat):.0f}ms p90={np.percentile(lat,90):.0f}ms "
              f"(our order +~34ms -> total reaction ~{int(np.median(lat))+34}ms)")

    # per-ticker mid timeseries + trades
    by = {}
    for recv, exch, tk, mt, yb, ya, tsd, tp in rows:
        d = by.setdefault(tk, {"t": [], "mid": [], "trades": []})
        if yb is not None and ya is not None and yb < ya:
            d["t"].append(recv); d["mid"].append((yb+ya)/2.0)
        if mt == "trade" and tsd in ("yes", "no") and ya is not None and yb is not None:
            d["trades"].append((recv, tsd, ya, yb))

    def mid_at(d, t):
        i = bisect_left(d["t"], t)
        return d["mid"][i] if i < len(d["mid"]) else None

    # ---- Measurement 1: aggressor-follow ----
    print("\n## M1 aggressor-follow: EV(cents) of taking the aggressor side at post-trade ask, by horizon")
    print(f"   (positive & > {COST_C}c past {LATENCY_MS}ms = follow edge)")
    hbuckets = {h: [] for h in HORIZONS_MS}
    ntr = 0
    for tk, d in by.items():
        if len(d["t"]) < 20:
            continue
        for (rt, tsd, ya, yb) in d["trades"]:
            ntr += 1
            entry = ya if tsd == "yes" else (100 - yb)   # we'd pay the ask on the aggressor side
            for h in HORIZONS_MS:
                m = mid_at(d, rt + h)
                if m is None:
                    continue
                val = (m - entry) if tsd == "yes" else ((100 - m) - entry)  # value of holding the side
                hbuckets[h].append(val)
    for h in HORIZONS_MS:
        v = np.array(hbuckets[h])
        if len(v) > 30:
            t = v.mean()/(v.std(ddof=1)/math.sqrt(len(v))) if v.std() > 0 else 0
            flag = "<<EDGE?" if (v.mean()-COST_C) > 0 and t > 2 else ""
            print(f"   +{h:5}ms: mean={v.mean():+.2f}c  net(-{COST_C})={v.mean()-COST_C:+.2f}c  t={t:+.1f}  n={len(v)} {flag}")
    print(f"   (total trades analyzed: {ntr})")

    # ---- Measurement 2: quote-staleness ----
    # after a trade, time until the book's mid actually moves (reprice gap)
    print("\n## M2 quote-staleness: ms until mid moves after a trade (reprice gap)")
    gaps = []
    for tk, d in by.items():
        if len(d["t"]) < 20:
            continue
        for (rt, tsd, ya, yb) in d["trades"]:
            base = mid_at(d, rt)
            if base is None:
                continue
            i = bisect_left(d["t"], rt)
            for j in range(i, min(i+200, len(d["t"]))):
                if abs(d["mid"][j]-base) >= 0.5:        # mid moved >=0.5c
                    gaps.append(d["t"][j]-rt); break
    if gaps:
        g = np.array(gaps)
        frac = (g >= LATENCY_MS).mean()
        print(f"   median reprice gap={np.median(g):.0f}ms p25={np.percentile(g,25):.0f}ms p75={np.percentile(g,75):.0f}ms")
        print(f"   frac of post-trade reprices SLOWER than our {LATENCY_MS}ms latency = {frac:.1%} "
              f"(higher = more pickoff-able stale windows)")

    # ---- Measurement 3: repricing speed by liquidity tier ----
    print("\n## M3 reprice speed by liquidity tier (trades per ticker)")
    tiers = sorted(by.items(), key=lambda kv: -len(kv[1]["trades"]))
    n = len(tiers)
    for label, sub in [("HIGH-liq (top third)", tiers[:n//3]),
                       ("MID-liq (middle)", tiers[n//3:2*n//3]),
                       ("LOW-liq (bottom)", tiers[2*n//3:])]:
        allg = []
        for tk, d in sub:
            for (rt, tsd, ya, yb) in d["trades"]:
                base = mid_at(d, rt)
                if base is None: continue
                i = bisect_left(d["t"], rt)
                for j in range(i, min(i+200, len(d["t"]))):
                    if abs(d["mid"][j]-base) >= 0.5:
                        allg.append(d["t"][j]-rt); break
        tr = sum(len(d["trades"]) for _, d in sub)
        if allg:
            g = np.array(allg)
            print(f"   {label:22} mkts={len(sub):3} trades={tr:5} medReprice={np.median(g):5.0f}ms  "
                  f">{LATENCY_MS}ms={ (g>=LATENCY_MS).mean():.0%}")

    print(f"\n## VERDICT GATE: GREEN if M1 shows net>0/t>2 past {LATENCY_MS}ms, OR M2/M3 show a "
          f"tradeable tier with >{LATENCY_MS}ms reprice + real trade volume. Else RED (ms-efficient, close speed path).")


if __name__ == "__main__":
    main()
