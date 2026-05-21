"""late_close_tail_v1 LIVE-promotion audit snapshot.

Drives the 2026-06-03 Stage B promotion decision per
[[late_close_tail_v1_subcohort_audit_2026-05-20]]:

  Stage B entry requires ALL:
    • observer n_total              >= 80
    • observer n_under_0.95          >= 25  (fee-positive zone)
    • observer WR(<0.95)             >= 80%
    • Stage B size = $5 micro-pilot
    • 0.88-0.89 cliff bucket decision:
        n >= 15 AND WR < 75%   -> Stage B with EXCLUDE_NO_ASK gate
        n >= 15 AND WR >= 80%  -> Stage B without exclude gate
        n  < 15                -> Stage B with EXCLUDE_NO_ASK (conservative)

Sections:
  1. Lifetime observer stats (total / settled / WR)
  2. Fine-grain 2c bucket table — n / W / L / gross-per-ct / net-per-ct (fee-adj) / WR
  3. Stage B entry checklist (PASS / FAIL per criterion)
  4. LIVE-vs-paper twin gap (active only after promotion; placeholder otherwise)
  5. Decision recommendation

CLI:
  venv/bin/python scripts/late_close_tail_promo_eval.py
  venv/bin/python scripts/late_close_tail_promo_eval.py --db data/bot.db
  venv/bin/python scripts/late_close_tail_promo_eval.py --since 2026-05-19
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DB = "data/bot.db"
OBSERVER_NAME = "late_close_tail"
STRATEGY_NAME = "late_close_tail_v1"

# 2c bucket edges. Lower bound inclusive, upper bound exclusive — except
# the final bucket which is [0.98, 1.00).
BUCKETS = [
    (0.00, 0.80),
    (0.80, 0.82), (0.82, 0.84), (0.84, 0.86), (0.86, 0.88),
    (0.88, 0.90),   # cliff bucket per 2026-05-21 audit
    (0.90, 0.92), (0.92, 0.94), (0.94, 0.96),
    (0.96, 0.98), (0.98, 1.00),
]
CLIFF_BUCKET = (0.88, 0.90)
LIVE_NET_ZERO_FLOOR = 0.95   # >= this, paper edge is consumed by rounding + fee

# Stage B entry criteria
MIN_N_TOTAL = 80
MIN_N_UNDER_FLOOR = 25
MIN_WR_UNDER_FLOOR = 0.80
CLIFF_MIN_N_FOR_DECISION = 15
CLIFF_WR_FAIL = 0.75
CLIFF_WR_CLEAR = 0.80


def _kalshi_fee_per_contract(na: float) -> float:
    """Kalshi fee = ceil(7 * price * (1 - price)) / 100 per contract.

    For a BUY NO at no_ask = na, the entry price is na.
    """
    return math.ceil(7.0 * na * (1.0 - na)) / 100.0


def _bucket_for(na: float) -> tuple[float, float] | None:
    for lo, hi in BUCKETS:
        if lo <= na < hi:
            return (lo, hi)
    if 0.99 <= na <= 1.0:
        return (0.98, 1.00)
    return None


# ─────────────────────────────────────────────────────────────────────
# Section 1 — lifetime observer stats
# ─────────────────────────────────────────────────────────────────────

def _section_lifetime(conn: sqlite3.Connection, since: str | None) -> tuple[list[str], list[tuple]]:
    if since:
        rows = conn.execute(
            """SELECT predicted_outcome, actual_outcome, features_json, ts
               FROM crypto_observer_signals
               WHERE observer = ? AND ts >= ?""",
            (OBSERVER_NAME, since),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT predicted_outcome, actual_outcome, features_json, ts
               FROM crypto_observer_signals
               WHERE observer = ?""",
            (OBSERVER_NAME,),
        ).fetchall()
    n_total = len(rows)
    settled = [r for r in rows if r[1] is not None]
    n_settled = len(settled)
    wins = sum(1 for p, a, _f, _t in settled if p == a)
    wr = (100.0 * wins / n_settled) if n_settled else None
    wr_s = f"{wr:.1f}%" if wr is not None else "—"
    win_label = "lifetime" if not since else f"since {since}"
    lines = [
        "=== 1. Observer stats ({}) ===".format(win_label),
        f"  n_total={n_total}  n_settled={n_settled}  wins={wins}  WR={wr_s}",
    ]
    return lines, rows


# ─────────────────────────────────────────────────────────────────────
# Section 2 — fine-grain bucket table + net-EV after fee
# ─────────────────────────────────────────────────────────────────────

def _bucket_table(rows: list[tuple]) -> tuple[list[str], dict[tuple[float, float], dict]]:
    grid: dict[tuple[float, float], dict] = {
        b: {"n": 0, "w": 0, "gross": 0.0, "net": 0.0} for b in BUCKETS
    }
    for pred, actual, fj, _ts in rows:
        if actual is None:
            continue
        try:
            f = json.loads(fj or "{}")
        except Exception:
            continue
        na = f.get("na")
        if na is None:
            continue
        b = _bucket_for(float(na))
        if b is None:
            continue
        cell = grid[b]
        cell["n"] += 1
        fee = _kalshi_fee_per_contract(float(na))
        if pred == actual:
            cell["w"] += 1
            cell["gross"] += (1.0 - float(na))
            cell["net"] += (1.0 - float(na)) - fee
        else:
            cell["gross"] += -float(na)
            cell["net"] += -float(na) - fee
    lines = [
        "",
        "=== 2. 2c bucket WR + fee-adjusted EV (BUY NO at na) ===",
        f"{'bucket':>10s}  {'n':>4s}  {'W':>3s}  {'L':>3s}  {'WR':>6s}  {'gross/ct':>9s}  {'net/ct':>8s}  {'net_$':>8s}",
    ]
    for b in BUCKETS:
        c = grid[b]
        n = c["n"]
        if n == 0:
            lines.append(
                f"{b[0]:.2f}-{b[1]:.2f}    {n:>4d}  {'-':>3s}  {'-':>3s}  {'-':>6s}  "
                f"{'-':>9s}  {'-':>8s}  {'-':>8s}"
            )
            continue
        w = c["w"]
        loss = n - w
        wr = 100.0 * w / n
        gpc = c["gross"] / n
        npc = c["net"] / n
        marker = "  <-- cliff" if b == CLIFF_BUCKET else ""
        lines.append(
            f"{b[0]:.2f}-{b[1]:.2f}    {n:>4d}  {w:>3d}  {loss:>3d}  {wr:>5.1f}%  "
            f"{gpc:>+9.4f}  {npc:>+8.4f}  {c['net']:>+8.2f}{marker}"
        )
    return lines, grid


# ─────────────────────────────────────────────────────────────────────
# Section 3 — Stage B entry checklist
# ─────────────────────────────────────────────────────────────────────

def _section_checklist(
    grid: dict[tuple[float, float], dict], n_total: int
) -> tuple[list[str], dict]:
    n_under = sum(grid[b]["n"] for b in BUCKETS if b[1] <= LIVE_NET_ZERO_FLOOR)
    w_under = sum(grid[b]["w"] for b in BUCKETS if b[1] <= LIVE_NET_ZERO_FLOOR)
    wr_under = (w_under / n_under) if n_under else None

    cliff = grid[CLIFF_BUCKET]
    cliff_wr = (cliff["w"] / cliff["n"]) if cliff["n"] else None

    def _mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    c1 = n_total >= MIN_N_TOTAL
    c2 = n_under >= MIN_N_UNDER_FLOOR
    c3 = (wr_under is not None) and (wr_under >= MIN_WR_UNDER_FLOOR)
    lines = [
        "",
        "=== 3. Stage B entry checklist ===",
        f"  [{_mark(c1)}]  n_total >= {MIN_N_TOTAL}                       got {n_total}",
        f"  [{_mark(c2)}]  n_under_{LIVE_NET_ZERO_FLOOR} >= {MIN_N_UNDER_FLOOR}                  got {n_under}",
        f"  [{_mark(c3)}]  WR(<{LIVE_NET_ZERO_FLOOR}) >= {int(MIN_WR_UNDER_FLOOR*100)}%                 "
        f"got {(wr_under*100 if wr_under is not None else 0):.1f}%",
    ]

    # Cliff decision
    cliff_n = cliff["n"]
    if cliff_n < CLIFF_MIN_N_FOR_DECISION:
        cliff_decision = f"n={cliff_n} < {CLIFF_MIN_N_FOR_DECISION} -> apply EXCLUDE_NO_ASK (conservative)"
        cliff_apply_exclude = True
    elif cliff_wr is not None and cliff_wr < CLIFF_WR_FAIL:
        cliff_decision = f"n={cliff_n}, WR={cliff_wr*100:.1f}% < {int(CLIFF_WR_FAIL*100)}% -> apply EXCLUDE_NO_ASK"
        cliff_apply_exclude = True
    elif cliff_wr is not None and cliff_wr >= CLIFF_WR_CLEAR:
        cliff_decision = f"n={cliff_n}, WR={cliff_wr*100:.1f}% >= {int(CLIFF_WR_CLEAR*100)}% -> NO exclude gate"
        cliff_apply_exclude = False
    else:
        cliff_decision = (
            f"n={cliff_n}, WR={(cliff_wr*100 if cliff_wr is not None else 0):.1f}% "
            f"(in {int(CLIFF_WR_FAIL*100)}-{int(CLIFF_WR_CLEAR*100)}% gray zone) -> apply EXCLUDE_NO_ASK (conservative)"
        )
        cliff_apply_exclude = True
    lines.append(f"  [DECISION]  0.88-0.89 cliff: {cliff_decision}")

    all_pass = c1 and c2 and c3
    state = {
        "all_pass": all_pass,
        "cliff_apply_exclude": cliff_apply_exclude,
        "n_total": n_total,
        "n_under": n_under,
        "wr_under": wr_under,
        "cliff_n": cliff_n,
        "cliff_wr": cliff_wr,
    }
    return lines, state


# ─────────────────────────────────────────────────────────────────────
# Section 4 — LIVE-vs-paper twin gap (post-promotion)
# ─────────────────────────────────────────────────────────────────────

def _section_live_twin(conn: sqlite3.Connection) -> list[str]:
    paper = conn.execute(
        """SELECT COUNT(*),
                  COALESCE(SUM(CASE WHEN status='paper_settled' THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN status='paper_settled' THEN pnl ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN status='paper_settled' THEN size ELSE 0 END), 0)
           FROM trades WHERE strategy = ? AND status IN ('paper_filled', 'paper_settled')""",
        (STRATEGY_NAME,),
    ).fetchone()
    live = conn.execute(
        """SELECT COUNT(*),
                  COALESCE(SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN status='settled' THEN pnl ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN status='settled' THEN size ELSE 0 END), 0)
           FROM trades WHERE strategy = ? AND status IN ('filled', 'settled')""",
        (STRATEGY_NAME,),
    ).fetchone()
    p_total, p_settled, p_pnl, p_size = paper
    l_total, l_settled, l_pnl, l_size = live
    lines = ["", "=== 4. LIVE-vs-paper twin gap ==="]
    p_roi = (100.0 * p_pnl / p_size) if p_size else 0.0
    lines.append(
        f"  paper:  filled={p_total} settled={p_settled} pnl=${p_pnl:+.2f} ROI={p_roi:+.2f}%"
    )
    if l_total == 0:
        lines.append("  LIVE :  no fills yet (Stage A / paper-only)")
        return lines
    l_roi = (100.0 * l_pnl / l_size) if l_size else 0.0
    gap = p_roi - l_roi
    lines.append(
        f"  LIVE :  filled={l_total} settled={l_settled} pnl=${l_pnl:+.2f} ROI={l_roi:+.2f}%"
    )
    lines.append(
        f"  twin-gap (paper - LIVE ROI):  {gap:+.2f}pp  "
        f"{'(within tolerance)' if abs(gap) < 5.0 else '(check slippage)'}"
    )
    # Per-trade slippage if realized_fill_price available
    slip_rows = conn.execute(
        """SELECT price, realized_fill_price
           FROM trades WHERE strategy = ?
             AND status IN ('filled', 'settled')
             AND realized_fill_price IS NOT NULL""",
        (STRATEGY_NAME,),
    ).fetchall()
    if slip_rows:
        diffs = [(p - r) for p, r in slip_rows if p is not None and r is not None]
        if diffs:
            avg = sum(diffs) / len(diffs)
            lines.append(
                f"  avg slip (signal - realized):  ${avg:+.4f}/ct  (n={len(diffs)})"
            )
    return lines


# ─────────────────────────────────────────────────────────────────────
# Section 5 — recommendation
# ─────────────────────────────────────────────────────────────────────

def _section_recommendation(state: dict) -> list[str]:
    lines = ["", "=== 5. Recommendation ==="]
    if not state["all_pass"]:
        lines.append("  HOLD  Stage A (paper continue).  Not all entry criteria met.")
        missing = []
        if state["n_total"] < MIN_N_TOTAL:
            missing.append(f"n_total {state['n_total']}/{MIN_N_TOTAL}")
        if state["n_under"] < MIN_N_UNDER_FLOOR:
            missing.append(f"n_under {state['n_under']}/{MIN_N_UNDER_FLOOR}")
        if state["wr_under"] is None or state["wr_under"] < MIN_WR_UNDER_FLOOR:
            wr_pct = (state["wr_under"] * 100) if state["wr_under"] is not None else 0
            missing.append(f"WR(<0.95) {wr_pct:.1f}%/{int(MIN_WR_UNDER_FLOOR*100)}%")
        lines.append(f"  Missing: {', '.join(missing)}")
        lines.append("  Next check: when one more criterion clears, re-run.")
        return lines

    lines.append("  PROMOTE to Stage B (LIVE micro-pilot).")
    lines.append("  Env flips required:")
    lines.append("    LATE_CLOSE_TAIL_SIZE_USDC=5")
    lines.append("    LATE_CLOSE_TAIL_MAX_NO_ASK=0.94")
    if state["cliff_apply_exclude"]:
        lines.append("    LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_LOW=0.88")
        lines.append("    LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_HIGH=0.90")
    else:
        lines.append("    (no EXCLUDE_NO_ASK — cliff bucket cleared)")
    lines.append("  Stage B eval window: 7 days from deploy.")
    lines.append("  Promotion to Stage C ($20): n>=10 LIVE fills + net>=+$5 + twin-gap<$0.05/ct.")
    return lines


# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default=None,
                    help="ISO timestamp filter for observer signals (default: lifetime)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        out: list[str] = []
        out.append(f"late_close_tail_v1 LIVE-promo audit ({datetime.now(timezone.utc).isoformat()})")
        out.append(f"DB: {args.db}")

        lt_lines, rows = _section_lifetime(conn, args.since)
        out.extend(lt_lines)

        bt_lines, grid = _bucket_table(rows)
        out.extend(bt_lines)

        ck_lines, state = _section_checklist(grid, n_total=len(rows))
        out.extend(ck_lines)

        out.extend(_section_live_twin(conn))
        out.extend(_section_recommendation(state))

        print("\n".join(out))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
