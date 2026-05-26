"""Pinnacle line movement backtest — cross-sport sharp-money-follow.

For each settled sports game in last 60d:

1. Take "open" Pinnacle implied prob (first odds_observations row with
   minutes_to_start > 240, i.e., ≥4h before game).
2. Take "close" Pinnacle implied prob (last row with mts > 30, i.e., ≥30min
   pre-game).
3. Movement = close.pinnacle_yes_prob - open.pinnacle_yes_prob (in pp).
4. Signal at "close" timestamp:
   - move ≥ +3pp: bet YES at close.kalshi_yes_ask
   - move ≤ -3pp: bet NO at (1 - close.kalshi_yes_ask)
   - else: no fire
5. Compute realized PnL with $0.07/ct fee.

Aggregates by sport + movement-magnitude band. Tests whether sharp-money
following has +EV in any sport × magnitude cohort.

Run: python3 scripts/pinnacle_line_movement_backtest.py
"""
import os
import sqlite3
import sys
import time
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 60
KALSHI_FEE = 0.07
MIN_MOVE_PP = 0.03  # 3pp threshold

# Parse sport from kalshi_ticker
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

    # Get settled tickers
    cur = conn.execute("""
        SELECT kalshi_ticker, result
        FROM ticker_settlements
        WHERE fetched_at >= datetime('now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settlements = {tk: res for tk, res in cur.fetchall()}
    print(f"Settled tickers last {LOOKBACK_DAYS}d: {len(settlements)}")

    # For each ticker, get open + close observations
    rows = []
    sport_counts = defaultdict(int)
    no_open = defaultdict(int)
    no_close = defaultdict(int)
    no_pin = defaultdict(int)

    for i, (tk, result) in enumerate(settlements.items()):
        sport = sport_of(tk)
        if sport is None:
            continue
        sport_counts[sport] += 1

        # "Open" snapshot: earliest row with mts > 240
        open_row = conn.execute("""
            SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
            FROM odds_observations
            WHERE kalshi_ticker = ?
              AND minutes_to_start > 240
              AND pinnacle_yes_prob IS NOT NULL
              AND kalshi_yes_ask IS NOT NULL
            ORDER BY minutes_to_start DESC
            LIMIT 1
        """, (tk,)).fetchone()
        if not open_row:
            no_open[sport] += 1
            continue

        # "Close" snapshot: latest pre-game with mts > 30
        close_row = conn.execute("""
            SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
            FROM odds_observations
            WHERE kalshi_ticker = ?
              AND minutes_to_start > 30
              AND pinnacle_yes_prob IS NOT NULL
              AND kalshi_yes_ask IS NOT NULL
            ORDER BY minutes_to_start ASC
            LIMIT 1
        """, (tk,)).fetchone()
        if not close_row:
            no_close[sport] += 1
            continue

        open_mts, _, open_pin = open_row
        close_mts, close_ya, close_pin = close_row
        if open_pin is None or close_pin is None:
            no_pin[sport] += 1
            continue

        # Need meaningful gap between open and close (avoid same snapshot)
        if open_mts - close_mts < 60:
            continue

        move = close_pin - open_pin

        rows.append({
            "ticker": tk, "sport": sport, "result": result,
            "open_mts": open_mts, "close_mts": close_mts,
            "open_pin": open_pin, "close_pin": close_pin, "move": move,
            "close_ya": close_ya,
        })

    print(f"\nBy sport — settled / no-open / usable rows:")
    print(f"{'sport':<6} {'settled':>8} {'no-open':>8} {'no-close':>9} {'no-pin':>7} {'usable':>7}")
    for s in sorted(sport_counts):
        usable = len([r for r in rows if r["sport"] == s])
        print(f"{s:<6} {sport_counts[s]:>8} {no_open[s]:>8} {no_close[s]:>9} {no_pin[s]:>7} {usable:>7}")

    if not rows:
        print("No usable rows."); return

    # Compute PnL for each row under sharp-follow strategy
    for r in rows:
        ya = r["close_ya"]
        move = r["move"]
        won = (r["result"] == "yes")
        if move >= MIN_MOVE_PP:
            # bet YES
            r["side"] = "yes"
            r["bet_price"] = ya
            r["pnl"] = (1.0 - ya - KALSHI_FEE) if won else (-ya - KALSHI_FEE)
        elif move <= -MIN_MOVE_PP:
            # bet NO at (1-ya)
            r["side"] = "no"
            na = 1.0 - ya  # approx (no_ask ≈ 1 - yes_ask without spread)
            r["bet_price"] = na
            r["pnl"] = (1.0 - na - KALSHI_FEE) if not won else (-na - KALSHI_FEE)
        else:
            r["side"] = None
            r["pnl"] = 0.0

    fired = [r for r in rows if r["side"] is not None]
    print(f"\n=== Fired rows (|move| ≥ {MIN_MOVE_PP*100:.0f}pp): {len(fired)} / {len(rows)} ===")

    # Aggregate by sport
    print(f"\n=== EV by sport (sharp-follow strategy) ===")
    print(f"{'sport':<6} {'n':>4} {'WR%':>6} {'EV/$1':>9} {'PnL':>9}")
    by_sport = defaultdict(list)
    for r in fired:
        by_sport[r["sport"]].append(r)
    for s in sorted(by_sport):
        sub = by_sport[s]
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["result"] == "yes") or
            (r["side"] == "no" and r["result"] == "no")))
        p = sum(r["pnl"] for r in sub)
        print(f"{s:<6} {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f} {p:>+8.2f}")

    # Aggregate by movement magnitude band
    print(f"\n=== EV by |move| band (all sports combined) ===")
    bands = [
        ("3-5pp",   0.03, 0.05),
        ("5-8pp",   0.05, 0.08),
        ("8-12pp",  0.08, 0.12),
        ("12pp+",   0.12, 1.0),
    ]
    print(f"{'band':<10} {'n':>4} {'WR%':>6} {'EV/$1':>9} {'PnL':>9}")
    for name, lo, hi in bands:
        sub = [r for r in fired if lo <= abs(r["move"]) < hi]
        if not sub:
            print(f"{name:<10} {0:>4} {'—':>6} {'—':>9} {'—':>9}")
            continue
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["result"] == "yes") or
            (r["side"] == "no" and r["result"] == "no")))
        p = sum(r["pnl"] for r in sub)
        print(f"{name:<10} {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f} {p:>+8.2f}")

    # Cross — sport x magnitude
    print(f"\n=== EV by sport × |move| band ===")
    print(f"{'sport':<6} {'3-5pp':>14} {'5-8pp':>14} {'8-12pp':>14} {'12pp+':>14}")
    for s in sorted(by_sport):
        line = f"{s:<6}"
        for name, lo, hi in bands:
            sub = [r for r in by_sport[s] if lo <= abs(r["move"]) < hi]
            if not sub:
                line += f" {'—':>14}"
                continue
            n = len(sub)
            p = sum(r["pnl"] for r in sub)
            line += f" n={n:>3} EV={p/n:>+6.3f}"
        print(line)

    # Direction split: yes-move vs no-move (does direction matter?)
    print(f"\n=== EV by side (move direction) ===")
    for side in ["yes", "no"]:
        sub = [r for r in fired if r["side"] == side]
        if not sub: continue
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["result"] == "yes") or
            (r["side"] == "no" and r["result"] == "no")))
        p = sum(r["pnl"] for r in sub)
        print(f"  {side.upper():<4} n={n:>4} WR={100*w/n:>5.1f}% EV={p/n:>+6.3f} PnL={p:>+8.2f}")


if __name__ == "__main__":
    main()
