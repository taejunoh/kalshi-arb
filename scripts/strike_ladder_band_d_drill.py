"""Drill into strike_ladder_literal band-D flipped cohort (5/26 audit follow-up).

Backtest found +$172 EV in band D (price 0.20-0.35) when flipping predictions.
Verify cohort robustness: split by source (yes→no vs no→yes) + by day + by series.
"""
import json
import sqlite3
from collections import defaultdict

DB = "/home/ubuntu/kalshi-arb/data/bot.db"
FEE = 0.07

yes_flip_no = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
no_flip_yes = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
yes_flip_no_series = defaultdict(lambda: {"n": 0, "pnl": 0.0})
no_flip_yes_series = defaultdict(lambda: {"n": 0, "pnl": 0.0})

c = sqlite3.connect(DB)
c.execute("PRAGMA busy_timeout=5000")

q = """SELECT ts, predicted_outcome, actual_outcome, features_json, market_id
       FROM crypto_observer_signals
       WHERE observer = 'strike_ladder_literal'
         AND actual_outcome IS NOT NULL
         AND ts > strftime('%Y-%m-%dT%H:%M:%S', 'now','-30 days')"""

for ts, pred, actual, fj, mid in c.execute(q):
    try:
        f = json.loads(fj)
    except Exception:
        continue
    ya, na = f.get("target_ya"), f.get("target_na")
    if ya is None or na is None:
        continue
    day = ts[:10]
    series = mid.split("-")[0]

    if pred == "yes" and 0.20 <= na < 0.35:
        pnl = (1.0 - na - FEE) if actual == "no" else (-na - FEE)
        yes_flip_no[day]["n"] += 1
        yes_flip_no[day]["pnl"] += pnl
        if pnl > 0:
            yes_flip_no[day]["wins"] += 1
        yes_flip_no_series[series]["n"] += 1
        yes_flip_no_series[series]["pnl"] += pnl
    elif pred == "no" and 0.20 <= ya < 0.35:
        pnl = (1.0 - ya - FEE) if actual == "yes" else (-ya - FEE)
        no_flip_yes[day]["n"] += 1
        no_flip_yes[day]["pnl"] += pnl
        if pnl > 0:
            no_flip_yes[day]["wins"] += 1
        no_flip_yes_series[series]["n"] += 1
        no_flip_yes_series[series]["pnl"] += pnl


def show_daily(label, d):
    print(f"\n=== {label} ===")
    tot_n = tot_pnl = tot_w = 0
    for day in sorted(d.keys()):
        dd = d[day]
        tot_n += dd["n"]
        tot_pnl += dd["pnl"]
        tot_w += dd["wins"]
        wr = 100 * dd["wins"] / dd["n"] if dd["n"] else 0
        ev = dd["pnl"] / dd["n"] if dd["n"] else 0
        print(f"  {day}  n={dd['n']:4d}  PnL=${dd['pnl']:+7.2f}  EV=${ev:+.4f}  WR={wr:.1f}%")
    if tot_n:
        ev = tot_pnl / tot_n
        wr = 100 * tot_w / tot_n
        print(f"  TOTAL    n={tot_n}  PnL=${tot_pnl:+.2f}  EV=${ev:+.4f}  WR={wr:.1f}%")


def show_series(label, d):
    print(f"\n=== {label} (series) ===")
    for s in sorted(d.keys()):
        sd = d[s]
        ev = sd["pnl"] / sd["n"] if sd["n"] else 0
        print(f"  {s:10}  n={sd['n']:4d}  PnL=${sd['pnl']:+7.2f}  EV=${ev:+.4f}")


show_daily("YES-pred flipped to NO @ na 0.20-0.35", yes_flip_no)
show_series("YES-pred flipped to NO @ na 0.20-0.35", yes_flip_no_series)

show_daily("NO-pred flipped to YES @ ya 0.20-0.35", no_flip_yes)
show_series("NO-pred flipped to YES @ ya 0.20-0.35", no_flip_yes_series)
