"""Phase 0 14-day backtest sim for deribit_tail_fade_high_v1.

Replays deribit_tail_skew Phase 0 settled signals through the high_v1
gate (ya ∈ [MIN_YA, MAX_YA], predicted='no') with the proposed LIVE
config (per-event fire cap, $5 size) and computes hypothetical PnL.

Purpose: confirm or reject the +$205/n=14 paper signal from 2026-05-21
(single-day-only data) by extending to 14d and checking:

  • Multi-day EV consistency (any day with substantial -EV?)
  • Per-event concentration (top1 event ≤ 50% of gross?)
  • KXBTC / KXETH split (both +EV?)
  • Cascade net EV (sum of clusters ≥3 fires)
  • Per-cap impact (uncapped vs cap=2 vs cap=4)

Read-only. Run on demand:
    ssh ec2 "/home/ubuntu/kalshi-arb/venv/bin/python /home/ubuntu/kalshi-arb/scripts/deribit_tail_fade_high_v1_backtest.py"
"""
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_PATH = os.getenv("BOT_DB_PATH") or (
    "/home/ubuntu/kalshi-arb/data/bot.db"
    if Path("/home/ubuntu/kalshi-arb/data/bot.db").exists()
    else str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
)

# Strategy gate constants (must match crypto_observer_phase1.py + .env).
MIN_YA = 0.65
MAX_YA = 0.95
FEE_PER_CT = 0.07
SIZE_USDC_VARIANTS = [5.0, 20.0]  # LIVE proposed, paper actual


def _event_key(market_id: str) -> str:
    if not market_id or "-" not in market_id:
        return market_id or ""
    return market_id.rsplit("-", 1)[0]


def _asset(market_id: str) -> str:
    return (market_id or "").split("-")[0]


def _connect() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        sys.exit(f"DB not found: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def fetch_signals(con: sqlite3.Connection, days: int = 14):
    """Return Phase 0 deribit_tail_skew signals within `days` that pass gate."""
    rows = con.execute(
        f"""
        SELECT ts, series, market_id, actual_outcome,
          CAST(json_extract(features_json, '$.ya') AS REAL) AS ya
        FROM crypto_observer_signals
        WHERE observer = 'deribit_tail_skew'
          AND ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-{days} days')
          AND predicted_outcome = 'no'
          AND actual_outcome IN ('yes', 'no')
          AND CAST(json_extract(features_json, '$.ya') AS REAL) BETWEEN ? AND ?
        ORDER BY ts
        """,
        (MIN_YA, MAX_YA),
    ).fetchall()
    return [dict(r) for r in rows if r["ya"] is not None]


def _trade_pnl_per_contract(ya: float, actual_outcome: str) -> float:
    """BUY NO at na = 1 - ya, Kalshi $0.07/ct flat fee.

    Win  (actual_outcome='no'): payout 1, cost na, fee 0.07
                                -> net = 1 - na - 0.07 = ya - 0.07
    Loss (actual_outcome='yes'): payout 0, cost na, fee 0.07
                                -> net = -na - 0.07 = -(1 - ya) - 0.07
    """
    if actual_outcome == "no":
        return ya - FEE_PER_CT
    return -(1.0 - ya) - FEE_PER_CT


def simulate(signals: list[dict], size_usdc: float,
             per_event_cap: int | None) -> dict:
    """Apply per-event cap and compute PnL summary.

    per_event_cap = None means uncapped (paper default before cap).
    """
    fires_per_event: dict[str, int] = defaultdict(int)
    trades: list[dict] = []
    for s in signals:
        ek = _event_key(s["market_id"])
        if per_event_cap is not None and fires_per_event[ek] >= per_event_cap:
            continue
        fires_per_event[ek] += 1
        na = 1.0 - s["ya"]
        if na <= 0:
            continue
        contracts = size_usdc / na
        pnl = contracts * _trade_pnl_per_contract(s["ya"], s["actual_outcome"])
        trades.append({
            "day": s["ts"][:10],
            "event_key": ek,
            "market_id": s["market_id"],
            "asset": _asset(s["market_id"]),
            "ya": s["ya"],
            "contracts": contracts,
            "pnl": pnl,
            "win": s["actual_outcome"] == "no",
        })
    return {"trades": trades}


def report(label: str, sim: dict, size_usdc: float, cap):
    trades = sim["trades"]
    if not trades:
        print(f"  {label}: no fires")
        return
    n = len(trades)
    net = sum(t["pnl"] for t in trades)
    stake = n * size_usdc
    roi = net / stake * 100.0 if stake > 0 else 0.0
    wins = sum(1 for t in trades if t["win"])
    wr = wins / n * 100.0
    by_day: dict[str, list[float]] = defaultdict(list)
    by_event: dict[str, float] = defaultdict(float)
    by_asset: dict[str, list[float]] = defaultdict(list)
    by_market: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t["pnl"])
        by_event[t["event_key"]] += t["pnl"]
        by_asset[t["asset"]].append(t["pnl"])
        by_market[t["market_id"]].append(t["pnl"])
    days_pos = sum(1 for pnls in by_day.values() if sum(pnls) > 0)
    n_days = len(by_day)
    gross_pos = sum(p for p in by_event.values() if p > 0)
    top_events = sorted(by_event.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top1_pct = (top_events[0][1] / gross_pos * 100.0) if gross_pos > 0 else 0.0
    cascade_events = [(m, p) for m, p in by_market.items() if len(p) >= 3]
    cascade_net = sum(sum(p) for _, p in cascade_events)
    cap_str = "uncapped" if cap is None else f"cap={cap}"
    print()
    print(f"━ {label} (size=${size_usdc:.0f}, {cap_str}) ━━━━━━━━━━━━━━━")
    print(f"  Aggregate: n={n}  PnL=${net:+.2f}  ROI={roi:+.2f}%  WR={wr:.1f}%")
    print(f"  Day spread: {days_pos}/{n_days} positive days")
    print(f"  Top1 event share: {top1_pct:.1f}% of gross")
    for a in sorted(by_asset.keys()):
        an = len(by_asset[a])
        ap = sum(by_asset[a])
        a_roi = ap / (an * size_usdc) * 100 if an > 0 else 0
        print(f"  {a}: n={an}  PnL=${ap:+.2f}  ROI={a_roi:+.2f}%")
    print(f"  Cascades (≥3 same-strike fires): "
          f"{len(cascade_events)} events  net ${cascade_net:+.2f}")
    print("  Per-day (sorted):")
    for d in sorted(by_day.keys()):
        dn = len(by_day[d])
        dp = sum(by_day[d])
        marker = "+" if dp > 0 else "-"
        print(f"    {marker} {d}: n={dn}  ${dp:+.2f}")
    print(f"  Top events: " + ", ".join(
        f"{ek}=${ep:+.0f}" for ek, ep in top_events
    ))


def main():
    print(f"DB: {DB_PATH}")
    con = _connect()
    try:
        signals = fetch_signals(con, days=14)
    finally:
        con.close()

    if not signals:
        print("No Phase 0 signals match the gate. Check observer is firing.")
        return

    print(f"\nPhase 0 deribit_tail_skew signals in 14d "
          f"(ya ∈ [{MIN_YA}, {MAX_YA}], pred='no', settled): {len(signals)}")

    # 3 cap variants × 2 sizes = 6 scenarios. Keep it focused.
    scenarios = [
        ("LIVE proposed", 5.0, 2),
        ("Paper actual",  20.0, None),
        ("Cap=4 sensitivity", 20.0, 4),
    ]
    for label, size, cap in scenarios:
        sim = simulate(signals, size_usdc=size, per_event_cap=cap)
        report(label, sim, size, cap)


if __name__ == "__main__":
    main()
