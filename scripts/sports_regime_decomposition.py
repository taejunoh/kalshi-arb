"""Sports regime decomposition — fee-adjusted-EV per (strategy, sport, regime).

Built 2026-05-21 following [[etf_flow_lag-thesis-broken-2026-05-20]] which
showed pooled paper alpha can be entirely single-cell concentrated
(one ticker × one date × one regime), then collapse the moment that cell
exits. The Phase 1 sports portfolio is bleeding -$1,580 May LIVE; before
demoting v32 / NBA playoff / underdog_v31 / soccer_draw we need to know
whether each strategy's paper alpha is regime-broad or regime-fragile.

Regimes per sport:

  NBA / NHL / WNBA / MLB   → playoff (is_playoff = True) vs reg-season (False)
                              + season-phase month bucket
  EPL / LAL / UCL / BUN    → season phase: opening (Aug-Oct) /
                              mid (Nov-Feb) / end (Mar-May)
  MLS                      → early (Mar-Apr) / mid (May-Aug) / late (Sep-Nov)
  NPB / KBO                → early (Mar-May) / mid (Jun-Aug) / late (Sep-Oct)
  ATP / WTA                → Grand-Slam month (1, 5-6, 7, 8-9) vs other
  UFC                      → all (no regime split)

For each (strategy × sport × regime) cell with n >= 3:

  n, wins, WR
  gross stake $, gross PnL $, ROI %
  fee-adjusted EV per contract  (= pnl / total_contracts, in dollars)
  mean signal price (sanity check the cohort sits in its expected band)
  breakeven WR @ mean signal price = signal / (signal + payout)

Flags:

  CONCENTRATED   one cell holds >70% of positive PnL across cells with n>=3
  WIDESPREAD     no single cell >50% — regime-robust
  SHALLOW        all cells n<5 — too thin to judge

Read-only. Console-only.
"""
import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database


logger = logging.getLogger("sports_regime_decomposition")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


SPORT_SHORT_BY_TOKEN = {
    "WNBAGAME": "WNBA",
    "NBAGAME": "NBA", "MLBGAME": "MLB", "NHLGAME": "NHL",
    "NPBGAME": "NPB", "KBOGAME": "KBO",
    "MLSGAME": "MLS", "EPLGAME": "EPL", "LALIGAGAME": "LAL",
    "UCLGAME": "UCL", "BUNDESLIGAGAME": "BUN",
    "ATPMATCH": "ATP", "WTAMATCH": "WTA", "UFCFIGHT": "UFC",
}
_TOKEN_ORDER = sorted(SPORT_SHORT_BY_TOKEN, key=len, reverse=True)


def extract_sport(ticker: str) -> str | None:
    for tok in _TOKEN_ORDER:
        if tok in ticker:
            return SPORT_SHORT_BY_TOKEN[tok]
    return None


PLAYOFF_SPORTS = {"NBA", "NHL", "WNBA", "MLB"}
SOCCER_LEAGUES = {"EPL", "LAL", "UCL", "BUN"}

# Soccer season-phase month buckets (Northern-Hemisphere domestic leagues)
_SOCCER_PHASE = {
    8: "opening", 9: "opening", 10: "opening",
    11: "mid", 12: "mid", 1: "mid", 2: "mid",
    3: "end", 4: "end", 5: "end",
}
_MLS_PHASE = {
    3: "early", 4: "early",
    5: "mid", 6: "mid", 7: "mid", 8: "mid",
    9: "late", 10: "late", 11: "late",
}
_NPB_PHASE = {
    3: "early", 4: "early", 5: "early",
    6: "mid", 7: "mid", 8: "mid",
    9: "late", 10: "late",
}
_TENNIS_GS_MONTHS = {1, 5, 6, 7, 8, 9}  # Aus / Roland-Garros / Wimbledon / US


def regime_for(sport: str, ticker: str, ts_iso: str,
               is_playoff_col: int | None) -> str:
    """Return regime label for a (sport, trade) — derived from is_playoff
    column when available, else from month-bucket fallback."""
    try:
        d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "unknown"
    m = d.month
    if sport in PLAYOFF_SPORTS:
        if is_playoff_col == 1:
            return "playoff"
        if is_playoff_col == 0:
            return "reg"
        return "reg?"  # null is_playoff — best-guess reg
    if sport in SOCCER_LEAGUES:
        return _SOCCER_PHASE.get(m, f"m{m}")
    if sport == "MLS":
        return _MLS_PHASE.get(m, f"m{m}")
    if sport in {"NPB", "KBO"}:
        return _NPB_PHASE.get(m, f"m{m}")
    if sport in {"ATP", "WTA"}:
        return "GS" if m in _TENNIS_GS_MONTHS else "non-GS"
    return "all"  # UFC, unknown


async def decompose(since_iso: str, strategy_filter: str | None,
                    status_filter: str):
    db = Database()
    await db.initialize()

    import aiosqlite
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        conn.row_factory = aiosqlite.Row
        sql = (
            "SELECT id, strategy, market_id, direction, price, size, fee, "
            "       pnl, realized_fill_price, timestamp, is_playoff "
            "FROM trades "
            "WHERE status = ? "
            "  AND timestamp >= ? "
            "  AND strategy LIKE 'sports_%' "
            "  AND price > 0 "
        )
        params: list = [status_filter, since_iso]
        if strategy_filter:
            sql += "  AND strategy = ? "
            params.append(strategy_filter)
        sql += "ORDER BY timestamp"
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

    if not rows:
        print(f"No {status_filter} sports trades since {since_iso}.")
        return

    # cell key: (strategy, sport, regime)
    cells: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    skipped_no_sport = 0
    for r in rows:
        sport = extract_sport(r["market_id"])
        if not sport:
            skipped_no_sport += 1
            continue
        size = r["size"] or 0.0
        if size <= 0:
            continue
        price = r["price"]
        fill = r["realized_fill_price"] or price
        # For paper rows: contracts = size / price.
        # For LIVE post-backfill: size = contracts × realized → contracts = size/realized.
        # Use realized when available, else signal — robust for paper rows too.
        denom = fill if fill > 0 else price
        contracts = round(size / denom)
        if contracts <= 0:
            continue
        pnl = r["pnl"] or 0.0
        # Result inference: pnl ≈ -size when lost.
        won = abs(pnl + size) > 1.0
        regime = regime_for(sport, r["market_id"], r["timestamp"], r["is_playoff"])
        cells[(r["strategy"], sport, regime)].append({
            "ticker": r["market_id"],
            "price": price,
            "fill": fill,
            "contracts": contracts,
            "size": size,
            "pnl": pnl,
            "won": won,
            "ts": r["timestamp"],
        })

    print(f"\n=== Sports Regime Decomposition ({status_filter}, since {since_iso}) ===")
    if skipped_no_sport:
        print(f"  (skipped {skipped_no_sport} rows with no detectable sport in ticker)")
    print()

    # Compute per-cell metrics, group by strategy for concentration analysis
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for (strat, sport, regime), trades in cells.items():
        n = len(trades)
        wins = sum(1 for t in trades if t["won"])
        pnl = sum(t["pnl"] for t in trades)
        stake = sum(t["size"] for t in trades)
        contracts = sum(t["contracts"] for t in trades)
        wr = wins / n * 100 if n else 0
        roi = pnl / stake * 100 if stake > 0 else 0
        ev_per_ct = pnl / contracts if contracts else 0
        mean_signal = sum(t["price"] * t["contracts"] for t in trades) / contracts if contracts else 0
        # Breakeven WR @ mean signal price: signal = expected loss rate
        # Win pays (1-signal), loss costs signal → BE: WR*(1-s) = (1-WR)*s → WR = s/(s + 1 - s) = s
        # ...wait that's only without fees. With fees:
        # WR*(1-s-fee/size) = (1-WR)*(s+fee/size)
        # Simpler: just show signal price as BE-WR approximation.
        be_wr = mean_signal * 100
        by_strategy[strat].append({
            "sport": sport,
            "regime": regime,
            "n": n,
            "wins": wins,
            "wr": wr,
            "stake": stake,
            "pnl": pnl,
            "roi": roi,
            "contracts": contracts,
            "ev_per_ct": ev_per_ct,
            "mean_signal": mean_signal,
            "be_wr": be_wr,
        })

    # Print per-strategy table + concentration verdict
    print(f"{'strategy / cohort':50} {'n':>4} {'WR':>5} {'sigP':>5} {'BE_WR':>6} "
          f"{'stake':>7} {'pnl':>8} {'ROI':>7} {'EV/ct':>7}")
    print("-" * 115)
    for strat, cells_list in sorted(by_strategy.items()):
        # Sort cells by PnL descending for concentration analysis
        cells_list.sort(key=lambda x: x["pnl"], reverse=True)
        total_pnl = sum(c["pnl"] for c in cells_list)
        total_n = sum(c["n"] for c in cells_list)
        total_stake = sum(c["stake"] for c in cells_list)
        positive_pnl = sum(c["pnl"] for c in cells_list if c["pnl"] > 0)
        max_cell_pnl = max((c["pnl"] for c in cells_list if c["pnl"] > 0), default=0)
        n_substantial = sum(1 for c in cells_list if c["n"] >= 3)

        # Concentration verdict
        if n_substantial <= 1 or all(c["n"] < 5 for c in cells_list):
            conc_verdict = "SHALLOW"
        elif positive_pnl > 0 and max_cell_pnl / positive_pnl > 0.7:
            conc_verdict = "CONCENTRATED"
        elif positive_pnl > 0 and max_cell_pnl / positive_pnl < 0.5:
            conc_verdict = "WIDESPREAD"
        else:
            conc_verdict = "MIXED"

        overall_roi = total_pnl / total_stake * 100 if total_stake > 0 else 0
        print(f"\n{strat}  [n={total_n}  pnl=${total_pnl:+.2f}  "
              f"ROI={overall_roi:+.1f}%  {conc_verdict}]")
        for c in cells_list:
            label = f"  {c['sport']:5} {c['regime']:12}"
            print(f"{label:50} {c['n']:>4} {c['wr']:>4.0f}% "
                  f"{c['mean_signal']:>5.2f} {c['be_wr']:>5.0f}% "
                  f"{c['stake']:>7.0f} {c['pnl']:>+8.2f} {c['roi']:>+6.1f}% "
                  f"{c['ev_per_ct']:>+7.3f}")

    print("\nConcentration verdicts:")
    print("  CONCENTRATED  >70% of positive PnL in one (sport, regime) cell — over-fit risk")
    print("  WIDESPREAD    no single cell >50% — regime-robust")
    print("  MIXED         single cell 50-70% — borderline")
    print("  SHALLOW       no cell with n>=5 — too thin to judge")
    print("\nColumns:")
    print("  sigP    mean signal price (sanity-check cohort band)")
    print("  BE_WR   breakeven WR ≈ signal price (no-fee approximation)")
    print("  EV/ct   fee-net PnL per contract (compare to 1-2c fee floor)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60,
                   help="Look-back window in days (default 60 — wider than slip audit "
                        "so paper backtest samples are meaningful).")
    p.add_argument("--since", type=str, default=None,
                   help="Override --days with explicit ISO date.")
    p.add_argument("--strategy", type=str, default=None,
                   help="Filter to a single strategy name.")
    p.add_argument("--status", type=str, default="settled",
                   choices=["settled", "paper_settled"],
                   help="Trade status filter (default 'settled' = LIVE; "
                        "use 'paper_settled' for paper backtest sample).")
    args = p.parse_args()

    if args.since:
        since_iso = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).isoformat()
    else:
        since_iso = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    asyncio.run(decompose(since_iso, args.strategy, args.status))


if __name__ == "__main__":
    main()
