import sqlite3
c = sqlite3.connect("/home/ubuntu/kalshi-arb/data/bot.db"); c.execute("PRAGMA query_only=1")
ts = c.execute("""SELECT DISTINCT kalshi_ticker FROM odds_observations
    WHERE kalshi_ticker LIKE 'KXNPBGAME%' AND minutes_to_start > 30
    AND timestamp >= datetime('now', '-2 hours')""").fetchall()
print(f"NPB active: {len(ts)}")
print(f"{'ticker':<32} {'ya':>5} {'open':>5} {'close':>5} {'move':>6} {'absmv':>5} {'mts':>5}")
moves = []
for (tk,) in ts:
    o = c.execute("""SELECT pinnacle_yes_prob FROM odds_observations
        WHERE kalshi_ticker=? AND minutes_to_start>=240
        AND pinnacle_yes_prob IS NOT NULL
        ORDER BY minutes_to_start DESC LIMIT 1""", (tk,)).fetchone()
    cl = c.execute("""SELECT minutes_to_start,kalshi_yes_ask,pinnacle_yes_prob
        FROM odds_observations WHERE kalshi_ticker=? AND minutes_to_start>30
        AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC LIMIT 1""", (tk,)).fetchone()
    if not o or not cl: continue
    op = o[0]; mts, ya, cp = cl
    if op is None or cp is None: continue
    mv = cp - op; absmv = abs(mv)
    moves.append(absmv)
    print(f"{tk:<32} {ya:>5.3f} {op:>5.3f} {cp:>5.3f} {mv:>+6.3f} {absmv:>5.3f} {mts:>5.0f}")
import statistics as st
if moves:
    print(f"\n|move| stats: n={len(moves)} min={min(moves):.3f} max={max(moves):.3f}")
    print(f"  median={st.median(moves):.3f} p75={sorted(moves)[int(0.75*len(moves))]:.3f}")
    in_band = sum(1 for m in moves if 0.03 <= m <= 0.08)
    print(f"  in [0.03, 0.08]: {in_band}/{len(moves)}")
