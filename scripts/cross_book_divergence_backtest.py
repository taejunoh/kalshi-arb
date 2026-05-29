"""Cross-book divergence backtest — Pinnacle vs US/EU consensus.

Hypothesis: when Pinnacle (sharp book) disagrees ≥5pp with US/EU consensus,
Pinnacle's view is correct and US/Kalshi will eventually adjust. Betting
the direction Pinnacle leans relative to US/EU should be +EV.

For each settled sports ticker (last 60d):

  1. Take latest pre-game `odds_observations` row (mts > 30) with all three
     books present (pinnacle_yes_prob, us_yes_prob, eu_yes_prob).
  2. Compute divergence = pinnacle_yes_prob - mean(us_yes_prob, eu_yes_prob).
  3. If |divergence| ≥ threshold:
       - Pinnacle > US/EU: bet YES at kalshi_yes_ask
       - Pinnacle < US/EU: bet NO at (1 - kalshi_yes_ask)
  4. Compute realized PnL with $0.07 fee.

Aggregates by sport × divergence-magnitude band.

Run: python3 scripts/cross_book_divergence_backtest.py
"""
import os
import sqlite3
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 60
KALSHI_FEE = 0.07
MIN_DIV_PP = 0.03  # lower bound for divergence to consider

SPORT_PREFIX = {
    "KXMLBGAME":  "MLB",
    "KXNBAGAME":  "NBA",
    "KXNHLGAME":  "NHL",
    "KXWNBAGAME": "WNBA",
    "KXEPLGAME":  "EPL",
    "KXMLSGAME":  "MLS",
    "KXNPBGAME":  "NPB",
    "KXKBOGAME":  "KBO",
    "KXLALIGAGAME": "LAL",
    "KXBUNDESLIGAGAME": "BUN",
    "KXUCLGAME":  "UCL",
    "KXATPMATCH": "ATP",
    "KXWTAMATCH": "WTA",
    "KXUFCFIGHT": "UFC",
}


def sport_of(ticker):
    for prefix, sport in SPORT_PREFIX.items():
        if ticker.startswith(prefix + "-"):
            return sport
    return None


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")

    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settled = {tk: res for tk, res in cur.fetchall()}
    print(f"Settled tickers last {LOOKBACK_DAYS}d: {len(settled)}")

    rows = []
    for tk, result in settled.items():
        sport = sport_of(tk)
        if sport is None:
            continue
        # Latest pre-game snapshot with all 3 books + kalshi
        snap = conn.execute("""
            SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob,
                   us_yes_prob, eu_yes_prob
            FROM odds_observations
            WHERE kalshi_ticker = ?
              AND minutes_to_start > 30
              AND pinnacle_yes_prob IS NOT NULL
              AND us_yes_prob IS NOT NULL
              AND eu_yes_prob IS NOT NULL
              AND kalshi_yes_ask IS NOT NULL
            ORDER BY minutes_to_start ASC
            LIMIT 1
        """, (tk,)).fetchone()
        if not snap:
            continue
        mts, ya, pin, us, eu = snap
        consensus = (us + eu) / 2
        div = pin - consensus  # +ve: Pinnacle thinks YES more likely than US/EU
        if abs(div) < MIN_DIV_PP:
            continue
        won = (result == "yes")
        if div >= MIN_DIV_PP:
            side = "yes"
            bet = ya
            pnl = (1.0 - ya - KALSHI_FEE) if won else (-ya - KALSHI_FEE)
        else:
            side = "no"
            bet = 1.0 - ya
            pnl = (1.0 - bet - KALSHI_FEE) if not won else (-bet - KALSHI_FEE)
        rows.append({
            "ticker": tk, "sport": sport, "side": side,
            "div": div, "abs_div": abs(div), "ya": ya,
            "pin": pin, "consensus": consensus, "won": won,
            "pnl": pnl,
        })

    print(f"Total fired (|div| ≥ {MIN_DIV_PP*100:.0f}pp): {len(rows)}\n")

    def stat(sub, label):
        if not sub:
            print(f"  {label:<22} n=0"); return
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["won"]) or
            (r["side"] == "no" and not r["won"])))
        p = sum(r["pnl"] for r in sub)
        avg_ya = sum(r["ya"] for r in sub) / n
        print(f"  {label:<22} n={n:>4}  WR={100*w/n:>5.1f}%  EV={p/n:+7.3f}  PnL={p:+8.2f}  avg_ya={avg_ya:.3f}")

    # Overall
    print("=== Overall ===")
    stat(rows, "ALL fired")

    # By sport
    print("\n=== By sport ===")
    by_sport = defaultdict(list)
    for r in rows:
        by_sport[r["sport"]].append(r)
    for s in sorted(by_sport, key=lambda x: -sum(rr["pnl"] for rr in by_sport[x])):
        stat(by_sport[s], s)

    # By divergence magnitude band
    print("\n=== By |divergence| band (all sports) ===")
    bands = [
        ("3-5pp",   0.03, 0.05),
        ("5-8pp",   0.05, 0.08),
        ("8-12pp",  0.08, 0.12),
        ("12-20pp", 0.12, 0.20),
        ("20pp+",   0.20, 1.0),
    ]
    for name, lo, hi in bands:
        stat([r for r in rows if lo <= r["abs_div"] < hi], name)

    # Direction analysis
    print("\n=== By Pinnacle direction (relative to US/EU) ===")
    stat([r for r in rows if r["div"] > 0], "Pin > US/EU → YES")
    stat([r for r in rows if r["div"] < 0], "Pin < US/EU → NO")

    # Sport × magnitude tighter
    print("\n=== Sport × |div| band (n≥10 cells only) ===")
    print(f"{'sport':<6} {'3-5pp':>22} {'5-8pp':>22} {'8-12pp':>22} {'12pp+':>22}")
    for s in sorted(by_sport):
        line = f"{s:<6}"
        for name, lo, hi in bands[:4]:
            sub = [r for r in by_sport[s] if lo <= r["abs_div"] < hi]
            if len(sub) < 10:
                line += f" {'n='+str(len(sub)):>22}"
                continue
            n = len(sub)
            p = sum(r["pnl"] for r in sub)
            w = sum(1 for r in sub if (
                (r["side"] == "yes" and r["won"]) or
                (r["side"] == "no" and not r["won"])))
            line += f"  n={n:>3} EV={p/n:>+6.3f} WR={100*w/n:>3.0f}%"
        print(line)


if __name__ == "__main__":
    main()
