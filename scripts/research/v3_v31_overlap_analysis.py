"""Retrospective V3 vs V31 overlap analysis.

Generates docs/research/v3_v31_overlap_<YYYY-MM-DD>.md with:
  (a) CLV direction per bucket (overlap us, overlap eu, us-only)
  (b) V31 entry Pinnacle-Kalshi gap distribution
  (c) Pinnacle-predicts-closing convergence rate

Usage:
    python -m scripts.research.v3_v31_overlap_analysis [--db path]
"""
import argparse
import datetime as dt
import sqlite3
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO / "data" / "bot.db"
OUT_DIR = REPO / "docs" / "research"

PAIRS = [
    ("sports_longshot", "sports_longshot_v31"),
    ("sports_midshot", "sports_midshot_v31"),
    ("sports_underdog", "sports_underdog_v31"),
]


def bucket_trades(conn):
    """Return dict: {pair_name: {'overlap_us':[], 'overlap_eu':[], 'us_only':[]}}"""
    out = {}
    for us_strat, eu_strat in PAIRS:
        us = list(conn.execute(
            "SELECT id, market_id, direction, price, COALESCE(pnl,0), status "
            "FROM trades WHERE strategy=? AND status='paper_settled'", (us_strat,)))
        eu = list(conn.execute(
            "SELECT id, market_id, direction, price, COALESCE(pnl,0), status "
            "FROM trades WHERE strategy=? AND status='paper_settled'", (eu_strat,)))
        us_tix = {r[1] for r in us}
        eu_tix = {r[1] for r in eu}
        overlap = us_tix & eu_tix
        us_only = us_tix - eu_tix
        out[f"{us_strat}/{eu_strat}"] = {
            "overlap_us": [r for r in us if r[1] in overlap],
            "overlap_eu": [r for r in eu if r[1] in overlap],
            "us_only": [r for r in us if r[1] in us_only],
        }
    return out


def clv_direction(conn, trade_id, direction):
    """+1 if closing moved toward entry direction, -1 if away, 0 if unknown."""
    rows = list(conn.execute(
        "SELECT snapshot_at, yes_ask, no_ask FROM clv_snapshots WHERE trade_id=?",
        (trade_id,),
    ))
    snaps = {r[0]: (r[1], r[2]) for r in rows}
    entry = snaps.get("entry")
    close = snaps.get("pre_close") or snaps.get("closing")
    if not entry or not close or entry[0] is None or close[0] is None:
        return 0
    if direction == "yes":
        return 1 if close[0] > entry[0] else (-1 if close[0] < entry[0] else 0)
    return 1 if close[1] > entry[1] else (-1 if close[1] < entry[1] else 0)


def bucket_stats(conn, trades, direction_key=2, pnl_key=4):
    pnl = sum(r[pnl_key] for r in trades)
    wins = sum(1 for r in trades if r[pnl_key] > 0)
    clvs = [clv_direction(conn, r[0], r[direction_key]) for r in trades]
    clv_pos = sum(1 for c in clvs if c > 0)
    clv_neg = sum(1 for c in clvs if c < 0)
    return {
        "n": len(trades),
        "pnl_total": round(pnl, 2),
        "pnl_per_trade": round(pnl / len(trades), 3) if trades else 0,
        "win_rate": round(wins / len(trades), 3) if trades else 0,
        "clv_positive_rate": round(clv_pos / len(trades), 3) if trades else 0,
        "clv_negative_rate": round(clv_neg / len(trades), 3) if trades else 0,
    }


def v31_pinnacle_gap(conn):
    """V31 entries: |pinnacle_prob - entry_price| distribution.

    Uses the legacy YES-side-adjusted pinnacle_prob column (populated
    pre-Task-5 deploy), which can be compared directly to the YES/NO
    entry price from trades.
    """
    rows = list(conn.execute("""
        SELECT t.direction, t.price, s.pinnacle_prob
        FROM trades t JOIN clv_snapshots s ON s.trade_id=t.id
        WHERE t.strategy LIKE 'sports_%_v31' AND s.snapshot_at='entry'
          AND s.pinnacle_prob IS NOT NULL
    """))
    gaps = []
    for direction, price, pin in rows:
        implied = price if direction == "yes" else 1 - price
        gaps.append(abs(pin - implied))
    if not gaps:
        return {}
    gaps.sort()
    return {
        "n": len(gaps),
        "mean": round(statistics.mean(gaps), 4),
        "median": round(statistics.median(gaps), 4),
        "p25": round(gaps[len(gaps) // 4], 4),
        "p75": round(gaps[(3 * len(gaps)) // 4], 4),
    }


def pinnacle_predicts_closing(conn):
    """V31 overlap: did closing move toward Pinnacle entry view?"""
    rows = list(conn.execute("""
        SELECT t.id, t.direction, t.price, se.pinnacle_prob
        FROM trades t JOIN clv_snapshots se ON se.trade_id=t.id
        WHERE t.strategy LIKE 'sports_%_v31' AND se.snapshot_at='entry'
          AND se.pinnacle_prob IS NOT NULL
    """))
    converged = 0
    diverged = 0
    moves = []
    for tid, direction, price, pin in rows:
        implied_entry = price if direction == "yes" else 1 - price
        close = list(conn.execute(
            "SELECT yes_ask, no_ask FROM clv_snapshots "
            "WHERE trade_id=? AND snapshot_at IN ('pre_close','closing') "
            "ORDER BY snapshot_at LIMIT 1", (tid,)))
        if not close:
            continue
        close_yes, close_no = close[0]
        implied_close = close_yes if direction == "yes" else close_no
        if implied_close is None:
            continue
        toward = (pin - implied_entry) * (implied_close - implied_entry)
        if toward > 0:
            converged += 1
        elif toward < 0:
            diverged += 1
        moves.append(implied_close - implied_entry)
    total = converged + diverged
    return {
        "n": total,
        "convergence_rate": round(converged / total, 3) if total else 0,
        "avg_move": round(statistics.mean(moves), 4) if moves else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    out_path = OUT_DIR / f"v3_v31_overlap_{today}.md"

    buckets = bucket_trades(conn)
    gap_stats = v31_pinnacle_gap(conn)
    predict_stats = pinnacle_predicts_closing(conn)

    lines = [f"# V3 vs V31 Overlap Analysis — {today}", ""]
    lines.append("## (a) CLV direction per bucket\n")
    lines.append("| pair | bucket | n | PnL | per-trade | win rate | CLV+ rate | CLV- rate |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for pair_name, b in buckets.items():
        for bkt, trades in b.items():
            s = bucket_stats(conn, trades)
            lines.append(
                f"| {pair_name} | {bkt} | {s['n']} | {s['pnl_total']} | "
                f"{s['pnl_per_trade']} | {s['win_rate']} | "
                f"{s['clv_positive_rate']} | {s['clv_negative_rate']} |"
            )
    lines.append("")
    lines.append("## (b) V31 entry Pinnacle-Kalshi gap")
    lines.append(f"```\n{gap_stats}\n```")
    lines.append("")
    lines.append("## (c) Pinnacle-predicts-closing")
    lines.append(f"```\n{predict_stats}\n```")
    lines.append("")
    lines.append("## Interpretation (1 paragraph)")
    lines.append(
        "Placeholder — fill in based on numbers above. "
        "Key questions: (1) Is us-only CLV adverse? (2) Is V31 overlap "
        "CLV positive? (3) Is convergence_rate > 0.55 (Pinnacle predicts)?"
    )

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
