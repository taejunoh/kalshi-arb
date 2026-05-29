"""Mirror-pair audit for NPB sharp-FOLLOW backtest.

NPB markets are mirror pairs (e.g. -TOH and -CHU for TOHCHU match).
Same mechanism as tennis_pinnacle_fade_mirror_audit but FOLLOW direction.

Re-runs the live-strategy gate (|move|∈[3,8]pp, ya∈[0.30,0.70]) and
splits per-ticker vs per-unique-event accounting.
"""
import os
import sqlite3
import re
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 60
FEE = 0.07
MIN_MOVE_PP = 0.03
MAX_MOVE_PP = 0.08
MIN_YA = 0.30
MAX_YA = 0.70


def match_key(ticker: str):
    """KXNPBGAME-26MAY270500TOHCHU-CHU -> KXNPBGAME-26MAY270500TOHCHU"""
    m = re.match(r"^(KXNPBGAME-[0-9A-Z]+)-[A-Z]{2,4}$", ticker)
    return m.group(1) if m else None


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")

    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE kalshi_ticker LIKE 'KXNPBGAME-%'
          AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settled = list(cur.fetchall())
    print(f"Settled NPB last {LOOKBACK_DAYS}d: {len(settled)}")

    rows_by_match = defaultdict(list)
    for tk, result in settled:
        mk = match_key(tk)
        if not mk:
            continue
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
        amv = abs(move)
        if not (MIN_MOVE_PP <= amv <= MAX_MOVE_PP):
            continue
        if not (MIN_YA <= ya <= MAX_YA):
            continue
        won = (result == "yes")
        # FOLLOW direction
        if move > 0:
            side, bet = "yes", ya
            pnl = (1.0 - bet - FEE) if won else (-bet - FEE)
        else:
            side, bet = "no", 1.0 - ya
            pnl = (1.0 - bet - FEE) if not won else (-bet - FEE)
        rows_by_match[mk].append({
            "ticker": tk, "side": side, "ya": ya, "move": move,
            "pnl": pnl, "won": won,
        })

    one_side = sum(1 for v in rows_by_match.values() if len(v) == 1)
    two_side = sum(1 for v in rows_by_match.values() if len(v) == 2)
    print(f"Unique matches with ≥1 firing ticker: {len(rows_by_match)}")
    print(f"  1 firing ticker:  {one_side}")
    print(f"  2 firing tickers: {two_side}  ← mirror pairs (double-counted)")

    def stat(rows, label):
        if not rows:
            print(f"  {label:<25}  n=0"); return
        n = len(rows)
        w = sum(1 for r in rows if
                (r["side"] == "yes" and r["won"]) or
                (r["side"] == "no" and not r["won"]))
        p = sum(r["pnl"] for r in rows)
        print(f"  {label:<25}  n={n:>3}  WR={100*w/n:>5.1f}%  EV/$1={p/n:+7.3f}  PnL={p:+7.2f}")

    print("\n=== Per-ticker (original) ===")
    all_pertkt = [r for rs in rows_by_match.values() for r in rs]
    stat(all_pertkt, "ALL")
    stat([r for r in all_pertkt if r["move"] > 0], "  UP→YES")
    stat([r for r in all_pertkt if r["move"] < 0], "  DN→NO")

    print("\n=== Per-unique-event (dedupped) ===")
    all_unique = [rs[0] for rs in rows_by_match.values()]
    stat(all_unique, "ALL")
    stat([r for r in all_unique if r["move"] > 0], "  UP→YES")
    stat([r for r in all_unique if r["move"] < 0], "  DN→NO")

    # Mirror agreement
    pair_agree = sum(
        1 for rs in rows_by_match.values() if len(rs) == 2
        and (((rs[0]["side"] == "yes" and rs[0]["won"]) or (rs[0]["side"] == "no" and not rs[0]["won"]))
             == ((rs[1]["side"] == "yes" and rs[1]["won"]) or (rs[1]["side"] == "no" and not rs[1]["won"])))
    )
    pair_disagree = two_side - pair_agree
    print(f"\nMirror pair sanity: agree={pair_agree}, disagree={pair_disagree}")


if __name__ == "__main__":
    main()
