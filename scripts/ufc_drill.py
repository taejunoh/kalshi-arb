"""UFC drill-down — sharp-follow AND fade direction, all bands."""
import sqlite3
DB = "/home/ubuntu/kalshi-arb/data/bot.db"
FEE = 0.07
c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

cur = c.execute("""SELECT kalshi_ticker, result FROM ticker_settlements
    WHERE kalshi_ticker LIKE 'KXUFCFIGHT-%'
    AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-60 days')
    AND result IN ('yes','no')""")
settled = cur.fetchall()
print(f"UFC settled last 60d: {len(settled)}")

rows = []
for tk, result in settled:
    o = c.execute("""SELECT pinnacle_yes_prob FROM odds_observations
        WHERE kalshi_ticker=? AND minutes_to_start>=240
        AND pinnacle_yes_prob IS NOT NULL
        ORDER BY minutes_to_start DESC LIMIT 1""", (tk,)).fetchone()
    cl = c.execute("""SELECT minutes_to_start,kalshi_yes_ask,pinnacle_yes_prob
        FROM odds_observations WHERE kalshi_ticker=? AND minutes_to_start>30
        AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC LIMIT 1""", (tk,)).fetchone()
    if not o or not cl: continue
    om, _, op = o[0], None, o[0]
    cm, ya, cp = cl
    op = o[0]
    if op is None or cp is None: continue
    move = cp - op; absmv = abs(move); won = (result == "yes")
    rows.append({"won": won, "ya": ya, "move": move, "absmv": absmv})

print(f"Usable rows: {len(rows)}")

# Try BOTH directions (follow and fade) at multiple magnitude bands
def stat(sub, label):
    if not sub:
        print(f"  {label:<28} n=0"); return
    n = len(sub)
    f_pnl = sum(r["fpnl"] for r in sub)  # follow pnl
    fa_pnl = sum(r["fapnl"] for r in sub)  # fade pnl
    f_wr = sum(1 for r in sub if r["fwon"])
    fa_wr = sum(1 for r in sub if not r["fwon"])  # fade wins when follow loses
    print(f"  {label:<28} n={n:>3} FOLLOW: WR={100*f_wr/n:>5.1f}% EV={f_pnl/n:+7.3f}  FADE: EV={fa_pnl/n:+7.3f}")

# Annotate each row with follow/fade pnl
for r in rows:
    ya = r["ya"]; won = r["won"]
    if r["move"] >= 0:
        # Follow: bet YES at ya; Fade: bet NO at 1-ya
        r["fpnl"] = (1 - ya - FEE) if won else (-ya - FEE)
        r["fapnl"] = (1 - (1-ya) - FEE) if not won else (-(1-ya) - FEE)
        r["fwon"] = won
    else:
        # Follow: bet NO at 1-ya; Fade: bet YES at ya
        r["fpnl"] = (1 - (1-ya) - FEE) if not won else (-(1-ya) - FEE)
        r["fapnl"] = (1 - ya - FEE) if won else (-ya - FEE)
        r["fwon"] = not won

print("\n=== UFC by |move| band ===")
for lo, hi in [(0.00, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 0.08),
               (0.08, 0.12), (0.12, 1.0)]:
    sub = [r for r in rows if lo <= r["absmv"] < hi]
    stat(sub, f"|move| {lo*100:.0f}-{hi*100:.0f}pp")

print("\n=== UFC by ya band (move ≥ 3pp) ===")
for lo, hi in [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]:
    sub = [r for r in rows if r["absmv"] >= 0.03 and lo <= r["ya"] < hi]
    stat(sub, f"ya {lo:.1f}-{hi:.1f}")

print("\n=== UFC favorite vs underdog ya bias (no move filter) ===")
favorites = [r for r in rows if r["ya"] >= 0.60]
underdogs = [r for r in rows if r["ya"] < 0.40]
n_f = len(favorites); fav_wins = sum(1 for r in favorites if r["won"])
n_u = len(underdogs); ud_wins = sum(1 for r in underdogs if r["won"])
print(f"  Favorites (ya≥0.60): n={n_f}  WR={100*fav_wins/n_f:.1f}% if n>0 else N/A")
if n_u > 0:
    print(f"  Underdogs (ya<0.40): n={n_u}  WR={100*ud_wins/n_u:.1f}%")

# Pure backing favorite EV
print("\n=== Pure ya-band performance (just bet YES at all observed prices) ===")
for lo, hi in [(0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: continue
    n = len(sub)
    pnl = sum((1 - r["ya"] - FEE) if r["won"] else (-r["ya"] - FEE) for r in sub)
    wr = sum(1 for r in sub if r["won"])
    print(f"  ya {lo:.1f}-{hi:.1f}: n={n:>3} WR={100*wr/n:>5.1f}% EV={pnl/n:+7.3f}")

# And fade favorite (bet NO when ya is high)
print("\n=== Fade big favorites (bet NO at 1-ya) ===")
for lo, hi in [(0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: continue
    n = len(sub)
    pnl = sum((1 - (1-r["ya"]) - FEE) if not r["won"] else (-(1-r["ya"]) - FEE) for r in sub)
    wr = sum(1 for r in sub if not r["won"])
    print(f"  ya {lo:.1f}-{hi:.1f} (FADE→NO): n={n:>3} fade-WR={100*wr/n:>5.1f}% EV={pnl/n:+7.3f}")
