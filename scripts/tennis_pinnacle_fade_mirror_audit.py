"""Mirror-pair audit for tennis_pinnacle_fade backtest.

For each ATP/WTA match settled in last 60d, identifies whether both mirror
tickers (e.g., -SHA + -FAR for FARSHA match) qualified for the fade signal.
Then re-computes EV in unique-event terms (one bet per match) alongside
the original per-ticker accounting.

Backtest mirror duplication concern: ticker-A.move = +X → fade=NO,
ticker-B.move = -X (complement) → fade=YES. Both bets win/lose together
since markets are mirror. So n=48 in original backtest ≈ ~n=24 unique
events with 2× row inflation. Promo gate of n=25 effective ≈ n=50 raw.
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
MIN_YA = 0.40
MAX_YA = 0.70


def match_key(ticker: str):
    """Strip the trailing -<3-letter-side> to get the match identifier.
    e.g. 'KXATPMATCH-26MAY24FARSHA-SHA' -> 'KXATPMATCH-26MAY24FARSHA'
    """
    m = re.match(r"^(KX(?:ATP|WTA)MATCH-[0-9A-Z]+)-[A-Z]{3}$", ticker)
    return m.group(1) if m else None


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")

    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE (kalshi_ticker LIKE 'KXATPMATCH-%' OR kalshi_ticker LIKE 'KXWTAMATCH-%')
          AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settled = list(cur.fetchall())
    print(f"Settled ATP+WTA last {LOOKBACK_DAYS}d: {len(settled)}")

    # For each ticker, evaluate whether it fires under the LIVE strategy's
    # gate: |move|∈[3,8]pp, ya∈[0.40,0.70]. Track per-(match_key, tour).
    rows_by_match = defaultdict(list)
    skip_no_data = 0
    for tk, result in settled:
        mk = match_key(tk)
        if not mk:
            continue
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
        if not open_row or not close_row:
            skip_no_data += 1
            continue
        open_mts, _, open_pin = open_row
        close_mts, ya, close_pin = close_row
        if open_pin is None or close_pin is None: continue
        if open_mts - close_mts < 60: continue
        move = close_pin - open_pin
        amv = abs(move)
        # Apply LIVE gate
        if not (MIN_MOVE_PP <= amv <= MAX_MOVE_PP):
            continue
        if not (MIN_YA <= ya <= MAX_YA):
            continue
        won = (result == "yes")
        # FADE direction
        if move > 0:
            side, bet = "no", 1.0 - ya
            pnl = (1.0 - bet - FEE) if not won else (-bet - FEE)
        else:
            side, bet = "yes", ya
            pnl = (1.0 - bet - FEE) if won else (-bet - FEE)
        rows_by_match[(mk, tour)].append({
            "ticker": tk, "side": side, "ya": ya, "move": move,
            "pnl": pnl, "won": won,
        })

    print(f"Tickers skipped (no open+close pinnacle): {skip_no_data}")
    print(f"Unique (match, tour) pairs with ≥1 firing ticker: {len(rows_by_match)}")

    # Count distribution: how many fire 1 vs 2 mirror sides
    one_side = sum(1 for v in rows_by_match.values() if len(v) == 1)
    two_side = sum(1 for v in rows_by_match.values() if len(v) == 2)
    over_two = sum(1 for v in rows_by_match.values() if len(v) > 2)
    print(f"  matches with 1 firing ticker:  {one_side}")
    print(f"  matches with 2 firing tickers: {two_side}  ← mirror pairs (double-counted)")
    print(f"  matches with >2:               {over_two}")

    # Per-ticker accounting (matches the original backtest)
    print("\n=== Per-ticker accounting (original backtest method) ===")
    for tour in ("ATP", "WTA", "ALL"):
        all_rows = [r for (mk, tr), rs in rows_by_match.items()
                    for r in rs if tour == "ALL" or tr == tour]
        if not all_rows:
            print(f"  {tour}: n=0"); continue
        n = len(all_rows)
        w = sum(1 for r in all_rows if
                (r["side"] == "yes" and r["won"]) or
                (r["side"] == "no" and not r["won"]))
        p = sum(r["pnl"] for r in all_rows)
        print(f"  {tour:<5} n={n:>3} WR={100*w/n:>5.1f}% EV/$1={p/n:+7.3f} PnL={p:+7.2f}")

    # Per-unique-event accounting: bet once per match, use one side
    # (either single-firer or take the SHA-equivalent side from the mirror pair).
    # Since mirrors agree on win/loss, just take the first row in each match.
    print("\n=== Per-unique-event accounting (dedupped) ===")
    for tour in ("ATP", "WTA", "ALL"):
        rows = []
        for (mk, tr), rs in rows_by_match.items():
            if tour != "ALL" and tr != tour:
                continue
            rows.append(rs[0])  # take first firing ticker per match
        if not rows:
            print(f"  {tour}: n=0"); continue
        n = len(rows)
        w = sum(1 for r in rows if
                (r["side"] == "yes" and r["won"]) or
                (r["side"] == "no" and not r["won"]))
        p = sum(r["pnl"] for r in rows)
        print(f"  {tour:<5} n={n:>3} WR={100*w/n:>5.1f}% EV/$1={p/n:+7.3f} PnL={p:+7.2f}")

    # Sanity: cross-tabulate per-pair agreement
    print("\n=== Sanity: in mirror pairs, do both sides agree on win/loss? ===")
    pair_agree = 0; pair_disagree = 0
    for rs in rows_by_match.values():
        if len(rs) != 2: continue
        won_a = (rs[0]["side"] == "yes" and rs[0]["won"]) or (rs[0]["side"] == "no" and not rs[0]["won"])
        won_b = (rs[1]["side"] == "yes" and rs[1]["won"]) or (rs[1]["side"] == "no" and not rs[1]["won"])
        if won_a == won_b: pair_agree += 1
        else: pair_disagree += 1
    print(f"  mirror pairs agree: {pair_agree}, disagree: {pair_disagree}")

    # WTA Roland Garros-only check (rough — last 14d window proxy for current tournament)
    print("\n=== WTA recent-only sub-check (last 14d settle, proxy Roland Garros) ===")
    cur14 = conn.execute("""
        SELECT kalshi_ticker FROM ticker_settlements
        WHERE kalshi_ticker LIKE 'KXWTAMATCH-%'
          AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')
          AND result IN ('yes', 'no')
    """)
    rg_tickers = {row[0] for row in cur14.fetchall()}
    rg_matches = {mk for (mk, tr), rs in rows_by_match.items() if tr == "WTA"
                  and any(r["ticker"] in rg_tickers for r in rs)}
    rg_rows_unique = [rs[0] for (mk, tr), rs in rows_by_match.items()
                      if tr == "WTA" and mk in rg_matches]
    rg_rows_pertkt = [r for (mk, tr), rs in rows_by_match.items()
                      if tr == "WTA" and mk in rg_matches for r in rs]
    if rg_rows_unique:
        for label, rows in (("unique-event", rg_rows_unique), ("per-ticker", rg_rows_pertkt)):
            n = len(rows)
            w = sum(1 for r in rows if
                    (r["side"] == "yes" and r["won"]) or
                    (r["side"] == "no" and not r["won"]))
            p = sum(r["pnl"] for r in rows)
            print(f"  WTA-14d  {label:<13} n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f} PnL={p:+7.2f}")


if __name__ == "__main__":
    main()
