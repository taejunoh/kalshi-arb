"""Quick tighter-spec validation for ATP/WTA Pinnacle FADE v1."""
import os, sqlite3
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
FEE = 0.07

c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")
cur = c.execute("""
  SELECT kalshi_ticker, result FROM ticker_settlements
  WHERE (kalshi_ticker LIKE 'KXATPMATCH-%' OR kalshi_ticker LIKE 'KXWTAMATCH-%')
    AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-60 days')
    AND result IN ('yes', 'no')
""")
rows = []
for tk, res in cur.fetchall():
    tour = "ATP" if tk.startswith("KXATPMATCH-") else "WTA"
    o = c.execute("""SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
        FROM odds_observations WHERE kalshi_ticker=? AND minutes_to_start>240
        AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start DESC LIMIT 1""", (tk,)).fetchone()
    cl = c.execute("""SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
        FROM odds_observations WHERE kalshi_ticker=? AND minutes_to_start>30
        AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC LIMIT 1""", (tk,)).fetchone()
    if not o or not cl: continue
    om, _, op = o; cm, ya, cp = cl
    if om - cm < 60 or op is None or cp is None: continue
    move = cp - op; absmv = abs(move); won = (res == "yes")
    if not (0.03 <= absmv <= 0.08): continue
    if not (0.30 <= ya <= 0.70): continue
    if move >= 0.03:
        bet = 1 - ya
        pnl = (1 - bet - FEE) if not won else (-bet - FEE)
        side = "no"
    else:
        bet = ya
        pnl = (1 - bet - FEE) if won else (-bet - FEE)
        side = "yes"
    rows.append({"tour": tour, "won": won, "side": side, "pnl": pnl, "ya": ya, "move": move})

def stat(sub, label):
    if not sub:
        print(f"  {label:<10} n=0"); return
    n = len(sub)
    w = sum(1 for r in sub if (r["side"] == "yes" and r["won"]) or
            (r["side"] == "no" and not r["won"]))
    p = sum(r["pnl"] for r in sub)
    print(f"  {label:<10} n={n:>3}  WR={100*w/n:>5.1f}%  EV={p/n:+7.3f}  PnL={p:+7.2f}")

print("TIGHTER SPEC (FADE, |move|∈[3,8]pp, ya∈[0.30,0.70]):")
stat(rows, "ALL")
stat([r for r in rows if r["tour"] == "ATP"], "ATP")
stat([r for r in rows if r["tour"] == "WTA"], "WTA")

print("\nBy ya band within spec:")
for lo, hi in [(0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70)]:
    stat([r for r in rows if lo <= r["ya"] < hi], f"{lo:.2f}-{hi:.2f}")

print("\nBy move band within spec:")
for lo, hi in [(0.03, 0.05), (0.05, 0.08)]:
    stat([r for r in rows if lo <= abs(r["move"]) < hi], f"|move|{lo:.2f}-{hi:.2f}")

print("\nBy direction within spec:")
stat([r for r in rows if r["move"] > 0], "UP→NO")
stat([r for r in rows if r["move"] < 0], "DN→YES")
