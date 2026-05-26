import sqlite3, math
from collections import defaultdict

def wilson_lower(wins, n, z=1.96):
    if n == 0: return 0.0
    p = wins/n
    denom = 1 + z*z/n
    centre = p + z*z/(2*n)
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (centre - margin) / denom

c = sqlite3.connect('/home/ubuntu/kalshi-arb/data/bot.db')

print('=== late_close_tail_v1 daily snapshot ===')
print()

def summary(label, q_extra=''):
    r = c.execute(f"SELECT COUNT(*), SUM(pnl), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM trades WHERE strategy='late_close_tail_v1' AND status='paper_settled' {q_extra}").fetchone()
    n, pnl, w = r[0], r[1] or 0, r[2] or 0
    wr = w/n*100 if n else 0
    ci = wilson_lower(w, n) * 100
    pf = pnl/n if n else 0
    print(f'{label:<10} n={n:4d}  WR={wr:5.1f}%  CI95_lo={ci:5.1f}%  PnL={pnl:+7.2f}  per_fire={pf:+.4f}')

summary('POOLED')
summary('>=0.97', "AND price>=0.97")
summary('0.85-0.97', "AND price>=0.85 AND price<0.97")
summary('<0.85', "AND price<0.85")

print()
print('--- Daily (last 7d) ---')
for d, n, p, w in c.execute("""
SELECT substr(timestamp,1,10), COUNT(*), SUM(pnl), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
FROM trades WHERE strategy='late_close_tail_v1' AND status='paper_settled'
GROUP BY substr(timestamp,1,10) ORDER BY 1 DESC LIMIT 7
""").fetchall():
    print(f'  {d}  n={n:4d}  W={w:3d}  PnL={(p or 0):+7.2f}')

print()
print('--- Promotion gate (>=0.97 zone) ---')
r = c.execute("SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM trades WHERE strategy='late_close_tail_v1' AND status='paper_settled' AND price>=0.97").fetchone()
n2, w2 = r[0], r[1] or 0
ci2 = wilson_lower(w2, n2) * 100
print(f'CI95_lo current: {ci2:.1f}%  | target 96%: need ~70 cum fires  | target 98%: need ~189 cum')
needed_96 = 0
while wilson_lower(w2 + needed_96, n2 + needed_96) * 100 < 96.0:
    needed_96 += 1
    if needed_96 > 1000: break
needed_98 = 0
while wilson_lower(w2 + needed_98, n2 + needed_98) * 100 < 98.0:
    needed_98 += 1
    if needed_98 > 1000: break
print(f'consecutive 100%-WR fires needed:  96% lo -> {needed_96}  |  98% lo -> {needed_98}')
