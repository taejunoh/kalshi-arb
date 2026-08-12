#!/usr/bin/env python3
"""MLS fade honest-ask first-crossing replay (post-World-Cup cohort).

Rebuilt 2026-08-12; the 7/09 original was lost with the /tmp wipe. Gate is the
LIVE one, unchanged: pre-game, <=24h out, yes_ask 0.55-0.70 AND no_ask
0.30-0.45, no -TIE legs, one candidate per ticker at its FIRST crossing.
Indexed WHERE with sport leading — odds_observations is the OOM table.
"""
import sqlite3, sys, collections, math
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-07-09"
db = sqlite3.connect("file:/home/ubuntu/kalshi-arb/data/bot.db?mode=ro", uri=True)

rows = db.execute("""
  SELECT kalshi_ticker, timestamp, kalshi_no_ask
    FROM odds_observations
   WHERE sport="MLS" AND phase="pre" AND timestamp >= ?
     AND minutes_to_start <= 1440
     AND kalshi_yes_ask BETWEEN 0.55 AND 0.70
     AND kalshi_no_ask  BETWEEN 0.30 AND 0.45
     AND kalshi_ticker NOT LIKE "%-TIE"
   ORDER BY kalshi_ticker, timestamp""", (SINCE,)).fetchall()

first = {}
for tk, ts, no_ask in rows:                 # first crossing per ticker
    if tk not in first:
        first[tk] = (ts, no_ask)
print("crossings: %d rows -> %d tickers" % (len(rows), len(first)))

settled = dict(db.execute("SELECT kalshi_ticker, result FROM ticker_settlements").fetchall())
cands = []
for tk, (ts, p) in sorted(first.items(), key=lambda kv: kv[1][0]):
    res = settled.get(tk)
    if res not in ("yes", "no") or not p:
        continue
    fee = 0.07 * p * (1 - p)
    ret = ((1 - p) - fee) / p if res == "no" else -(p + fee) / p
    cands.append((ts, tk, p, res, ret))

n = len(cands)
print("graded candidates: %d  (unsettled/pending skipped: %d)" % (n, len(first) - n))
if not n:
    sys.exit(0)
w = sum(1 for c in cands if c[3] == "no")
roi = 100 * sum(c[4] for c in cands) / n
top3 = sorted(cands, key=lambda c: -c[4])[:3]
net_ex3 = sum(c[4] for c in cands if c not in top3)
print("  n=%d  WR %.1f%%  ROI %+.2f%%  net %+.2f  drop-top-3 net %+.2f"
      % (n, 100*w/n, roi, sum(c[4] for c in cands), net_ex3))
print("")
print("주별 분포:")
g = collections.defaultdict(list)
for ts, tk, p, res, ret in cands:
    g[ts[:10]] = g[ts[:10]]
wk = collections.defaultdict(list)
for ts, tk, p, res, ret in cands:
    import datetime
    d = datetime.date.fromisoformat(ts[:10])
    wk[d.isocalendar()[1]].append((res, ret))
for k in sorted(wk):
    s = wk[k]
    print("   W%-3d n=%2d  WR %5.1f%%  net %+7.2f" % (
        k, len(s), 100*sum(1 for x in s if x[0]=="no")/len(s), sum(x[1] for x in s)))
print("")
print("후보 전체 (시간순):")
for ts, tk, p, res, ret in cands:
    print("   %s  %-28s p=%.2f  %-3s  %+.3f" % (ts[:16], tk[-28:], p, res, ret))
