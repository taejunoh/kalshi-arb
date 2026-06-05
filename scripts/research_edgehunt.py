"""Targeted +EV hunt under the empirically-confirmed $0.07/ct FLAT taker fee.

The flat fee creates a 7pp 'dead band': taker EV = p - ask - 0.07, so +EV is only
possible (a) on CHEAP contracts (low ask) where a >=7pp edge is achievable, or
(b) when a sharp-book DIVERGENCE signals the Kalshi ask is >7pp mispriced.

E1 — cheap-bin taker calibration: for buying each side at its ask, bin by ENTRY
     ask price x sport; mean realized EV=(outcome-ask)-fee; require n and
     first/second-half stability (leakage-free robustness, not a fit).
E2 — conditional divergence: bin by d = pinnacle - kalshi_mid; in each cell test
     whether acting on the divergence (buy YES if d>0 at yes_ask, else NO) is
     fee-surviving +EV, and whether realized outcome tracks pinnacle over kalshi.

Reads /tmp/model_extract.db (snap table @ target_mts).
"""
from __future__ import annotations
import argparse, sqlite3, math, re, json, os
from collections import defaultdict
import numpy as np

# Fee model selectable: 'flat' = bot's $0.07/ct (min-capped); 'taker' = real
# Kalshi 0.07*p*(1-p); 'maker' = 25% of taker (resting limit order).
FEE_MODE = os.getenv("FEE_MODE","taker")
def FEE_FN(ask):
    if FEE_MODE == "flat":
        return min(0.07, ask, 1 - ask)
    base = 0.07 * ask * (1 - ask)
    return base if FEE_MODE == "taker" else 0.25 * base
MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def datekey(t):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", t)
    if m: return (int(m.group(1)), MONTHS.get(m.group(2),0), int(m.group(3)))
    return (99,99,99)

def load(db, target_mts):
    conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
    out=[]
    for r in conn.execute("SELECT * FROM snap WHERE target_mts=?",(target_mts,)):
        yb,ya=r["yes_bid"],r["yes_ask"]
        if ya is None or not (0<ya<1): continue
        kmid = 0.5*(yb+ya) if (yb is not None and 0<yb<=ya<1) else ya
        no_ask = (1-yb) if (yb is not None and 0<yb<1) else (1-kmid)
        out.append(dict(ticker=r["ticker"],sport=r["sport"],y=r["y"],
            yes_ask=ya,no_ask=no_ask,kmid=kmid,pin=r["pin"],us=r["us"],
            dk=datekey(r["ticker"])))
    out.sort(key=lambda g: g["dk"])
    return out

def wilson(k,n,z=1.96):
    if n==0: return (0,0)
    p=k/n; d=1+z*z/n
    c=p+z*z/(2*n); h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-h)/d,(c+h)/d)

def cell_stats(bets):
    """bets: list of (ev, win(0/1), dk). Return summary with half-split stability."""
    if not bets: return None
    ev=np.array([b[0] for b in bets]); n=len(ev)
    mean=float(ev.mean()); se=float(ev.std(ddof=1)/math.sqrt(n)) if n>1 else float('nan')
    t=mean/se if se==se and se>0 else float('nan')
    # time split
    bs=sorted(bets,key=lambda b:b[2]); h=n//2
    m1=float(np.mean([b[0] for b in bs[:h]])) if h>0 else float('nan')
    m2=float(np.mean([b[0] for b in bs[h:]])) if n-h>0 else float('nan')
    wins=int(sum(b[1] for b in bets))
    return dict(n=n,mean_ev=round(mean,4),t=round(t,2) if t==t else None,
        wr=round(100*wins/n,1),h1_ev=round(m1,4),h2_ev=round(m2,4),
        stable=(m1>0 and m2>0))

def E1(games):
    """Cheap-bin taker calibration, both sides, x sport."""
    # bins on entry ask, focus where +EV is mathematically reachable
    edges=[0.02,0.08,0.12,0.16,0.20,0.25,0.30,0.35,0.45,0.60,1.01]
    def binof(a):
        for i in range(len(edges)-1):
            if edges[i]<=a<edges[i+1]: return f"{edges[i]:.2f}-{edges[i+1]:.2f}"
        return None
    cells=defaultdict(list)       # (scope, bin) -> bets
    for g in games:
        for side,ask,win in (("YES",g["yes_ask"],g["y"]),("NO",g["no_ask"],1-g["y"])):
            if not (0.02<=ask<1): continue
            ev=(win-ask)-FEE_FN(ask)
            b=binof(ask)
            if b is None: continue
            cells[("ALL",side,b)].append((ev,win,g["dk"]))
            cells[(g["sport"],side,b)].append((ev,win,g["dk"]))
    rows=[]
    for (scope,side,b),bets in cells.items():
        s=cell_stats(bets)
        if s and s["n"]>=25:
            rows.append(dict(scope=scope,side=side,bin=b,**s))
    rows.sort(key=lambda r:r["mean_ev"],reverse=True)
    return rows

def E2(games):
    """Conditional divergence pinnacle vs kalshi."""
    g2=[g for g in games if g["pin"] is not None]
    dbins=[(-1.01,-0.12),(-0.12,-0.07),(-0.07,-0.04),(-0.04,-0.02),(-0.02,0.02),
           (0.02,0.04),(0.04,0.07),(0.07,0.12),(0.12,1.01)]
    def binof(d):
        for lo,hi in dbins:
            if lo<=d<hi: return f"{lo:+.2f}_{hi:+.2f}"
        return None
    cells=defaultdict(list); calib=defaultdict(lambda:[0,0.0,0.0,0.0]) # n, sumY, sumkmid, sumpin
    for g in g2:
        d=g["pin"]-g["kmid"]; b=binof(d)
        if b is None: continue
        # act on divergence: d>0 -> pinnacle higher -> buy YES at yes_ask; else buy NO
        if d>0:
            ev=(g["y"]-g["yes_ask"])-FEE_FN(g["yes_ask"]); win=g["y"]
        else:
            ev=((1-g["y"])-g["no_ask"])-FEE_FN(g["no_ask"]); win=1-g["y"]
        cells[b].append((ev,win,g["dk"]))
        c=calib[b]; c[0]+=1; c[1]+=g["y"]; c[2]+=g["kmid"]; c[3]+=g["pin"]
    rows=[]
    for b,bets in cells.items():
        s=cell_stats(bets); c=calib[b]
        if s and s["n"]>=20:
            rows.append(dict(dbin=b,**s,realY=round(c[1]/c[0],3),
                avg_kmid=round(c[2]/c[0],3),avg_pin=round(c[3]/c[0],3)))
    rows.sort(key=lambda r:r["dbin"])
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="/tmp/model_extract.db")
    ap.add_argument("--target_mts",type=int,default=60)
    a=ap.parse_args()
    games=load(a.db,a.target_mts)
    print(f"# {len(games)} games @mts~{a.target_mts}, fee_mode={FEE_MODE} (0.07*p*(1-p) taker)\n")

    print("## E1 — cheap-bin taker +EV cells (mean_ev>0, n>=25), sorted by EV")
    e1=E1(games)
    pos=[r for r in e1 if r["mean_ev"]>0]
    print(f"  {len(pos)} of {len(e1)} cells have mean_ev>0 (gross of multiple-comparisons):")
    for r in pos[:30]:
        flag="STABLE" if r["stable"] else "unstable"
        print(f"  {r['scope']:6} {r['side']:3} ask{r['bin']:11} n={r['n']:4} ev={r['mean_ev']:+.4f} t={r['t']} wr={r['wr']} h1={r['h1_ev']:+.3f} h2={r['h2_ev']:+.3f} [{flag}]")
    print("\n  Top NEGATIVE ALL-scope cells (context):")
    for r in [x for x in e1 if x['scope']=='ALL'][-6:]:
        print(f"  {r['scope']:6} {r['side']:3} ask{r['bin']:11} n={r['n']:4} ev={r['mean_ev']:+.4f} t={r['t']} wr={r['wr']}")

    print("\n## E2 — conditional divergence (pinnacle - kalshi_mid) cells")
    print("  dbin              n   ev(act)   t    wr   realY  kmid  pin   stable")
    for r in E2(games):
        print(f"  {r['dbin']:14} {r['n']:4} {r['mean_ev']:+.4f} {str(r['t']):>5} {r['wr']:5} {r['realY']:.3f} {r['avg_kmid']:.3f} {r['avg_pin']:.3f}  {r['stable']}")

if __name__=="__main__":
    main()
