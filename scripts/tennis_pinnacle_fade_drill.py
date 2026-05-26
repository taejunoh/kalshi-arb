"""Drill-down: ATP/WTA Pinnacle FADE backtest.

Same window as pinnacle_line_movement_backtest.py but FADES the move:
  - move ≥ +3pp → bet NO at (1 - close.kalshi_yes_ask)
  - move ≤ -3pp → bet YES at close.kalshi_yes_ask

Splits by tour (ATP vs WTA), direction (yes-move vs no-move), magnitude band.
"""
import os
import sqlite3
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 60
KALSHI_FEE = 0.07
MIN_MOVE_PP = 0.03


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")

    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE (kalshi_ticker LIKE 'KXATPMATCH-%' OR kalshi_ticker LIKE 'KXWTAMATCH-%')
          AND fetched_at >= datetime('now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settled = [(tk, res) for tk, res in cur.fetchall()]
    print(f"Settled ATP+WTA last {LOOKBACK_DAYS}d: {len(settled)}")

    rows = []
    for tk, result in settled:
        tour = "ATP" if tk.startswith("KXATPMATCH-") else "WTA"
        open_row = conn.execute("""
            SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
            FROM odds_observations
            WHERE kalshi_ticker = ? AND minutes_to_start > 240
              AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
            ORDER BY minutes_to_start DESC LIMIT 1
        """, (tk,)).fetchone()
        close_row = conn.execute("""
            SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob
            FROM odds_observations
            WHERE kalshi_ticker = ? AND minutes_to_start > 30
              AND pinnacle_yes_prob IS NOT NULL AND kalshi_yes_ask IS NOT NULL
            ORDER BY minutes_to_start ASC LIMIT 1
        """, (tk,)).fetchone()
        if not open_row or not close_row: continue
        open_mts, _, open_pin = open_row
        close_mts, ya, close_pin = close_row
        if open_pin is None or close_pin is None: continue
        if open_mts - close_mts < 60: continue
        move = close_pin - open_pin
        won = (result == "yes")

        # FADE direction
        if move >= MIN_MOVE_PP:
            side, bet = "no", 1.0 - ya
            pnl = (1.0 - bet - KALSHI_FEE) if not won else (-bet - KALSHI_FEE)
        elif move <= -MIN_MOVE_PP:
            side, bet = "yes", ya
            pnl = (1.0 - bet - KALSHI_FEE) if won else (-bet - KALSHI_FEE)
        else:
            continue

        rows.append({
            "tour": tour, "move": move, "side": side,
            "ya": ya, "won": won, "pnl": pnl,
            "abs_move": abs(move),
        })

    print(f"Total fired (|move| ≥ 3pp, FADE direction): {len(rows)}\n")

    def stat(sub):
        if not sub: return None
        n = len(sub)
        w = sum(1 for r in sub if (
            (r["side"] == "yes" and r["won"]) or
            (r["side"] == "no" and not r["won"])))
        p = sum(r["pnl"] for r in sub)
        return n, w / n, p / n, p

    # Overall by tour
    print("=== EV by tour ===")
    print(f"{'tour':<5} {'n':>4} {'WR%':>6} {'EV/$1':>8} {'PnL':>9}")
    for tour in ("ATP", "WTA"):
        s = stat([r for r in rows if r["tour"] == tour])
        if s: print(f"{tour:<5} {s[0]:>4} {100*s[1]:>5.1f}% {s[2]:>+7.3f} {s[3]:>+8.2f}")
    s = stat(rows); print(f"{'TOTAL':<5} {s[0]:>4} {100*s[1]:>5.1f}% {s[2]:>+7.3f} {s[3]:>+8.2f}")

    # By direction (which side did Pinnacle move toward)
    print("\n=== EV by Pinnacle move direction (FADE) ===")
    for tour in ("ATP", "WTA", "ALL"):
        for dir_lbl, dir_filter in (
            ("UP(fade=NO)", lambda r: r["move"] >= MIN_MOVE_PP),
            ("DN(fade=YES)", lambda r: r["move"] <= -MIN_MOVE_PP),
        ):
            sub = [r for r in rows if (tour == "ALL" or r["tour"] == tour) and dir_filter(r)]
            s = stat(sub)
            if not s: continue
            print(f"  {tour:<5} {dir_lbl:<14} n={s[0]:>4} WR={100*s[1]:>5.1f}% EV={s[2]:>+7.3f}")

    # By magnitude band
    print("\n=== EV by |move| band (FADE, all tennis) ===")
    bands = [
        ("3-5pp",   0.03, 0.05),
        ("5-8pp",   0.05, 0.08),
        ("8-12pp",  0.08, 0.12),
        ("12pp+",   0.12, 1.0),
    ]
    print(f"{'band':<10} {'ATP':>20} {'WTA':>20}")
    for name, lo, hi in bands:
        line = f"{name:<10}"
        for tour in ("ATP", "WTA"):
            sub = [r for r in rows if r["tour"] == tour and lo <= r["abs_move"] < hi]
            s = stat(sub)
            if not s:
                line += f" {'—':>20}"
                continue
            line += f"  n={s[0]:>3} EV={s[2]:>+6.3f} WR={100*s[1]:>4.0f}%"
        print(line)

    # YA range — does fading work better at certain prices?
    print("\n=== EV by close ya band (FADE, all tennis) ===")
    ya_bands = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]
    for lo, hi in ya_bands:
        sub = [r for r in rows if lo <= r["ya"] < hi]
        s = stat(sub)
        if not s: continue
        print(f"  ya ∈ [{lo:.1f}, {hi:.1f}): n={s[0]:>3} WR={100*s[1]:>5.1f}% EV={s[2]:>+7.3f}")


if __name__ == "__main__":
    main()
