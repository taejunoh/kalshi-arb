"""Drill-down: sharp-FOLLOW per sport (NPB / LAL / KBO).

Same mechanism as tennis_pinnacle_fade_drill but FOLLOW direction (not fade):
  - move ≥ +3pp → bet YES at close.kalshi_yes_ask
  - move ≤ -3pp → bet NO at (1 - close.kalshi_yes_ask)

Splits by sport × magnitude band × ya band × direction.
Identifies optimal forward-strategy spec per sport.
"""
import os, sqlite3
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LB = 60
FEE = 0.07
TARGET_SPORTS = ["NPB", "LAL", "KBO"]
SPORT_PREFIX = {
    "NPB": "KXNPBGAME",
    "LAL": "KXLALIGAGAME",  # actually need to verify exact ticker — could be KXLALGAME etc
    "KBO": "KXKBOGAME",
}


def sport_of(ticker):
    for s, prefix in SPORT_PREFIX.items():
        if ticker.startswith(prefix + "-"):
            return s
    # Try alternate LAL prefix
    if ticker.startswith("KXLALIGAGAME-"):
        return "LAL"
    return None


def main():
    c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

    # Verify ticker prefixes from DB first
    print("=== Verifying ticker prefixes from DB ===")
    for sport in TARGET_SPORTS:
        cur = c.execute("""
            SELECT DISTINCT substr(kalshi_ticker, 1, instr(kalshi_ticker, '-') - 1) AS prefix
            FROM ticker_settlements
            WHERE fetched_at >= datetime('now', '-60 days')
              AND kalshi_ticker LIKE ?
            LIMIT 5
        """, (f"%{sport}%-%",))
        prefixes = [r[0] for r in cur.fetchall()]
        print(f"  {sport}: {prefixes}")

    rows = []
    for sport in TARGET_SPORTS:
        cur = c.execute("""
            SELECT kalshi_ticker, result FROM ticker_settlements
            WHERE kalshi_ticker LIKE ?
              AND fetched_at >= datetime('now', ?)
              AND result IN ('yes', 'no')
        """, (f"%{sport}%-%", f"-{LB} days"))
        settled = [(tk, res) for tk, res in cur.fetchall() if sport_of(tk) == sport]
        for tk, res in settled:
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
            if absmv < 0.03: continue
            # FOLLOW direction
            if move > 0:
                side = "yes"; bet = ya
                pnl = (1 - bet - FEE) if won else (-bet - FEE)
            else:
                side = "no"; bet = 1 - ya
                pnl = (1 - bet - FEE) if not won else (-bet - FEE)
            rows.append({
                "sport": sport, "ticker": tk, "won": won, "side": side,
                "pnl": pnl, "ya": ya, "move": move, "absmv": absmv,
            })

    print(f"\nTotal fired (FOLLOW |move|≥3pp): {len(rows)}")

    def stat(sub, label):
        if not sub:
            print(f"  {label:<22} n=0"); return
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["won"]) or
            (r["side"] == "no" and not r["won"])))
        p = sum(r["pnl"] for r in sub)
        avg_ya = sum(r["ya"] for r in sub) / n
        print(f"  {label:<22} n={n:>3}  WR={100*w/n:>5.1f}%  EV={p/n:+7.3f}  PnL={p:+7.2f}  avg_ya={avg_ya:.3f}")

    for sport in TARGET_SPORTS:
        sub_sport = [r for r in rows if r["sport"] == sport]
        if not sub_sport:
            print(f"\n=== {sport}: no rows ==="); continue
        print(f"\n=== {sport} (sharp-FOLLOW) ===")
        stat(sub_sport, "ALL")

        for lo, hi in [(0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.0)]:
            stat([r for r in sub_sport if lo <= r["absmv"] < hi],
                 f"|move| {lo*100:.0f}-{hi*100:.0f}pp")

        print(f"  -- ya band --")
        for lo, hi in [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]:
            stat([r for r in sub_sport if lo <= r["ya"] < hi],
                 f"ya {lo:.1f}-{hi:.1f}")

        print(f"  -- direction --")
        stat([r for r in sub_sport if r["move"] > 0], "UP→YES")
        stat([r for r in sub_sport if r["move"] < 0], "DN→NO")

        # Combined: 3-8pp + middle ya band
        sub_tight = [r for r in sub_sport
                     if 0.03 <= r["absmv"] <= 0.08
                     and 0.30 <= r["ya"] <= 0.70]
        stat(sub_tight, "TIGHT(3-8pp,ya30-70)")

        # Also try wider: 3-12pp + middle ya
        sub_wide = [r for r in sub_sport
                    if 0.03 <= r["absmv"] <= 0.12
                    and 0.30 <= r["ya"] <= 0.70]
        stat(sub_wide, "WIDE(3-12pp,ya30-70)")


if __name__ == "__main__":
    main()
