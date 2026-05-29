"""Quick check: current spec-matching candidates for NPB / LAL."""
import sqlite3
c = sqlite3.connect("/home/ubuntu/kalshi-arb/data/bot.db")
c.execute("PRAGMA query_only=1")
for sport, prefix, mvmax in [("NPB", "KXNPBGAME", 0.08), ("LAL", "KXLALIGAGAME", 0.05)]:
    cur = c.execute("""
      SELECT DISTINCT kalshi_ticker FROM odds_observations
      WHERE kalshi_ticker LIKE ? AND minutes_to_start > 30
        AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-2 hours')
    """, (prefix + "-%",))
    tickers = [r[0] for r in cur.fetchall()]
    cands = []
    for tk in tickers:
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
        absmv = abs(cp - op)
        if 0.03 <= absmv <= mvmax and 0.30 <= ya <= 0.70:
            cands.append((tk, ya, op, cp, cp - op, mts))
    print(f"\n{sport}: {len(tickers)} active tickers, {len(cands)} match spec")
    for tk, ya, op, cp, mv, mts in cands[:6]:
        direction = "YES" if mv > 0 else "NO"
        print(f"  {tk:<35} ya={ya:.3f} open={op:.3f} close={cp:.3f} move={mv:+.3f} mts={mts:.0f}min → FOLLOW={direction}")
