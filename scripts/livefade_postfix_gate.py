"""tennis_live_fade_v1 — post-booking-fix cohort tracker + promotion gate.

WHY THIS EXISTS
---------------
live_fade's paper twin used to book entries at the inherited `_get_limit_price`
(≈ consensus fair), while the strategy only fires when the ask sits >=5pp ABOVE
that fair. So the paper ledger recorded fills at a price the book never offered.
The damage, measured 2026-07-28 over the strategy's whole pre-fix history:

  paper twin        n=106  avg entry 0.757  ROI +22.01%
  real book FILLED  n= 45  avg realized 0.894  ROI -10.28%
  real book CANCELLED (never filled)  n=63  avg limit 0.713

64% of the paper profit came from orders that never filled — and the ones that
never filled were exactly the trades resting furthest below the ask, i.e. the
cases where the favourite held and we were right but not in. Classic adverse
selection: you only get filled when the market comes to you.

`_get_booking_price` (2026-07-22, commit 0192cd0, on EC2 23:07 UTC) fixed the
booking price without touching the live order. First trades under it appeared
2026-07-28 once the Washington Open wiring brought tennis back, and paper
immediately tracked the real fills (0.86/0.85/0.93 booked vs 0.86/0.87/0.93
realized). The user's decision: keep it LIVE at 4% sizing, accumulate a clean
post-fix cohort to n>=50 real fills, then run the formal replacement gate.

WHICH BOOK THE GATE COUNTS
  The booking fix changed the PAPER ledger only. `_get_booking_price` is read
  after every entry gate has run and reaches only `_paper_market_order` — never
  `_live_market_order` / `_live_limit_ttl_order`. So real execution is
  identical either side of the epoch, and real fills are ONE continuous cohort:
  the pre-fix ones were always honest, because a real fill is a real fill.

  The gate therefore counts ALL real fills toward n>=50 (45 of them already
  existed on 2026-07-28), and the epoch split is used for two other jobs:
  monitoring paper-vs-real booking honesty, and z-testing that the two eras
  really are one regime — if that check ever fails, something changed in live
  execution that we did not intend, which is worth an alert on its own.

THE GATE (user-owned, see memory tennis_strategies_audit)
  1. n >= 50 real fills (all eras)
  2. 2-proportion z, pre-fix vs post-fix real — regime consistency, not
     significance-hunting. |z| < 1.96 → same regime → pool.
  3. Wilson lower bound on the (pooled or post-only) win rate vs breakeven.

Breakeven is derived from the cohort, not assumed. Buying `s` dollars at price
`p` gets s/p contracts and pays fee `f`:
    EV = WR*(s/p) - s - f = 0  →  WR_BE = p * (1 + f/s)

EARLY KILL
  Waiting to n=50 costs real money. If by n>=20 the Wilson UPPER bound is
  already below breakeven, no remainder can rescue it — alert to demote now
  instead of bleeding to the milestone.

Run as a systemd oneshot; state file suppresses duplicate alerts.
    venv/bin/python scripts/livefade_postfix_gate.py            # alert-on-change
    venv/bin/python scripts/livefade_postfix_gate.py --stdout   # always print
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STRATEGY = "sports_tennis_live_fade_v1"
# EC2 fast-forwarded past the booking fix at 2026-07-22 23:07:14 UTC (git
# reflog). Anything entered at or after that instant is booked honestly.
POSTFIX_EPOCH = "2026-07-22T23:07:14+00:00"
TARGET_N = 50
EARLY_KILL_MIN_N = 20
DB_PATH = os.environ.get("KALSHI_DB", "data/bot.db")
STATE_PATH = os.environ.get(
    "LIVEFADE_GATE_STATE", "data/livefade_postfix_gate_state.json"
)


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _two_prop_z(w1: int, n1: int, w2: int, n2: int) -> float | None:
    """Two-proportion z. None when either arm is empty."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1.0 - pool) * (1.0 / n1 + 1.0 / n2))
    if se == 0:
        return 0.0
    return (p1 - p2) / se


def _cohort(conn, status: str, *, since: str | None = None,
            before: str | None = None) -> dict:
    """Aggregate one book. Real fills price at realized_fill_price when present
    (paper rows have it NULL and fall back to the booked price)."""
    where = ["strategy = ?", "status = ?"]
    params: list = [STRATEGY, status]
    if since:
        where.append("timestamp >= ?")
        params.append(since)
    if before:
        where.append("timestamp < ?")
        params.append(before)
    row = conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(size), 0), COALESCE(SUM(pnl), 0),
                   COALESCE(SUM(fee), 0),
                   COALESCE(AVG(COALESCE(realized_fill_price, price)), 0)
            FROM trades WHERE {' AND '.join(where)}""",
        params,
    ).fetchone()
    n, wins, stake, pnl, fee, avg_px = row
    be_wr = avg_px * (1.0 + (fee / stake if stake else 0.0))
    lo, hi = _wilson_ci(wins, n)
    return {
        "n": n, "wins": wins, "stake": stake, "pnl": pnl, "fee": fee,
        "avg_px": avg_px, "wr": (wins / n if n else 0.0),
        "roi": (100.0 * pnl / stake if stake else 0.0),
        "be_wr": be_wr, "wilson_lo": lo, "wilson_hi": hi,
    }


def _fmt(label: str, c: dict) -> str:
    return (f"{label}: n={c['n']} WR={100*c['wr']:.1f}% ROI={c['roi']:+.2f}% "
            f"avg_px={c['avg_px']:.3f} BE_WR={100*c['be_wr']:.1f}% "
            f"Wilson[{100*c['wilson_lo']:.1f}, {100*c['wilson_hi']:.1f}]")


def evaluate(conn) -> dict:
    post_real = _cohort(conn, "settled", since=POSTFIX_EPOCH)
    post_paper = _cohort(conn, "paper_settled", since=POSTFIX_EPOCH)
    pre_real = _cohort(conn, "settled", before=POSTFIX_EPOCH)
    pre_paper = _cohort(conn, "paper_settled", before=POSTFIX_EPOCH)
    # Real fills are one continuous cohort — the booking fix never touched live
    # execution. This, not the post-fix slice, is what the gate counts.
    all_real = _cohort(conn, "settled")

    out = {
        "post_real": post_real, "post_paper": post_paper,
        "pre_real": pre_real, "pre_paper": pre_paper, "all_real": all_real,
        "n": all_real["n"], "target": TARGET_N,
        "verdict": "COLLECTING", "detail": "",
    }

    # Booking-honesty monitor. Post-fix the paper twin should track the real
    # book; a re-opening gap means the hook regressed or a new code path books
    # around it — that is what made the old +22% meaningless.
    out["price_gap"] = post_paper["avg_px"] - post_real["avg_px"]
    out["roi_gap"] = post_paper["roi"] - post_real["roi"]

    if all_real["n"] >= EARLY_KILL_MIN_N and all_real["wilson_hi"] < all_real["be_wr"]:
        out["verdict"] = "EARLY-KILL"
        out["detail"] = (
            f"Wilson upper {100*all_real['wilson_hi']:.1f}% < breakeven "
            f"{100*all_real['be_wr']:.1f}% at n={all_real['n']} real fills — no "
            f"remainder can reach breakeven. Demote now rather than bleed to "
            f"n={TARGET_N}."
        )
        return out

    if all_real["n"] < TARGET_N:
        out["detail"] = f"{all_real['n']}/{TARGET_N} real fills"
        return out

    z = _two_prop_z(post_real["wins"], post_real["n"],
                    pre_real["wins"], pre_real["n"])
    # None = one era still empty; the structural argument (live path untouched)
    # already says pool, so an untestable split is not a reason to discard data.
    consistent = z is None or abs(z) < 1.96
    if consistent:
        lo, hi = all_real["wilson_lo"], all_real["wilson_hi"]
        be = all_real["be_wr"]
        basis, n_used = "all real fills", all_real["n"]
    else:
        lo, hi = post_real["wilson_lo"], post_real["wilson_hi"]
        be = post_real["be_wr"]
        basis, n_used = "post-fix only (UNEXPECTED regime shift)", post_real["n"]

    out["z"] = z
    out["basis"] = basis
    out["verdict"] = "PASS" if lo > be else "FAIL"
    out["detail"] = (
        f"{basis}, n={n_used}: Wilson LB {100*lo:.1f}% vs BE {100*be:.1f}%"
        + (f" (2-prop z={z:+.2f})" if z is not None else "")
    )
    return out


def render(r: dict) -> str:
    lines = [
        f"🎾 <b>live_fade post-fix gate — {r['verdict']}</b>",
        f"<i>{r['detail']}</i>",
        "",
        "<b>POST-FIX cohort</b> (booking honest)",
        f"  {_fmt('real ', r['post_real'])}",
        f"  {_fmt('paper', r['post_paper'])}",
        "",
        "<b>PRE-FIX</b> (paper booked at a price the book never offered)",
        f"  {_fmt('real ', r['pre_real'])}",
        f"  {_fmt('paper', r['pre_paper'])}",
        "",
        f"booking honesty: paper−real avg_px {r['price_gap']:+.3f}, "
        f"ROI {r['roi_gap']:+.2f}pp  (pre-fix gap was +0.14 / +32pp)",
    ]
    return "\n".join(lines)


async def _send_telegram(report: str) -> None:
    from core.config import Config
    from monitoring.notifier import TelegramNotifier

    cfg = Config()
    if not (cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID):
        print("(no telegram creds — skipped)", file=sys.stderr)
        return
    # msg_type is in CRITICAL_MSG_TYPES: this must break through quiet hours,
    # because we advance the state file right after and a suppressed alert
    # would be dropped, not retried.
    await TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID).send(
        report, msg_type="livefade_gate"
    )


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_PATH)


def _should_alert(r: dict, state: dict) -> bool:
    """Alert on verdict change, on reaching the milestone, and on every 10 fills
    of progress — quiet enough for a frequent timer, loud enough to not drift."""
    if r["verdict"] != state.get("verdict"):
        return True
    if r["verdict"] == "COLLECTING":
        return r["n"] // 10 != state.get("n", 0) // 10
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="print, never Telegram")
    ap.add_argument("--force", action="store_true", help="alert regardless of state")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        r = evaluate(conn)
    finally:
        conn.close()
    report = render(r)

    if args.stdout:
        print(report.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
        return 0

    state = _load_state()
    if args.force or _should_alert(r, state):
        asyncio.run(_send_telegram(report))
        _save_state({
            "verdict": r["verdict"], "n": r["n"],
            "at": datetime.now(timezone.utc).isoformat(),
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
