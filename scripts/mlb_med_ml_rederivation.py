#!/usr/bin/env python3
"""MLB Med ML re-derivation — fires once, the day the forward sample reaches n>=50.

Agreed 2026-08-12 (memory: pending_mlb_med_ml_rederivation_2026-09): inside the
Med ML cohort MLB is the largest slice (~33% of volume) and the weakest —
lifetime +2.3%/bet vs +17.1% for everything else, and dead-on breakeven (+1.4%,
n=150) in its core 2.00-2.60 odds band. The question is whether MLB keeps its
place in the ML=Med rule.

Runs daily from kalshi-mlb-rederivation.timer. Below the gate it exits silently;
at n>=50 it runs the full protocol and Telegrams the report ONCE (state file),
then keeps exiting silently. The DECISION stays with the user — this script
never edits BET_TIERS, and any change must also update keepmypick's
RULE_CHANGES (the era-disclosure test enforces the pair).

Protocol (the memory note's, verbatim):
  * forward = placed_at >= 2026-08-05 (the tier switch); n<50 -> wait
  * regime consistency FIRST: 2-prop z, pre vs forward. Consistent -> pool and
    judge on full-sample Wilson LB vs BE. Inconsistent -> judge forward alone.
    Never demand Wilson LB > BE of an OOS subset (over-conservative).
  * no overlapping windows; robustness = drop best AND worst day, both ways
  * BE from actual odds (sum(1/odds)/n), never win rate alone
  * control = non-MLB Med ML over the same forward window, so "MLB is broken"
    and "everything cooled" stay distinguishable.
"""
import math
import os
import sqlite3
import sys

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
STATE = os.getenv("MLB_REDERIVATION_STATE", "/home/ubuntu/kalshi-arb/data/.mlb_rederivation_sent")
SWITCH = "2026-08-05"
GATE_N = 50


def _wilson_lo(w, n):
    if not n:
        return 0.0
    p, z = w / n, 1.96
    return 100 * ((p + z * z / (2 * n) - z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n))
                  / (1 + z * z / n))


def _stats(rows):
    n = len(rows)
    if not n:
        return dict(n=0, w=0, wr=0.0, be=0.0, per=0.0, lb=0.0, pnl=0.0)
    w = sum(r["won"] for r in rows)
    st = sum(r["stake"] for r in rows)
    pnl = sum(r["pnl"] or 0 for r in rows)
    return dict(n=n, w=w, wr=100 * w / n, be=sum(1 / r["odds"] for r in rows) / n * 100,
                per=100 * pnl / st if st else 0.0, lb=_wilson_lo(w, n), pnl=pnl)


def two_prop_z(w1, n1, w2, n2):
    """Pooled two-proportion z (regime-consistency test)."""
    if not n1 or not n2:
        return 0.0
    p = (w1 + w2) / (n1 + n2)
    den = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return ((w1 / n1) - (w2 / n2)) / den if den else 0.0


def cohort(db):
    rows = db.execute(
        "SELECT sport, odds, stake, pnl, won, substr(placed_at,1,10) d FROM paper_bet"
        " WHERE tier='Med' AND market='h2h' AND completed=1 AND won IS NOT NULL").fetchall()
    out = [dict(sport=s, odds=o, stake=st, pnl=p, won=w, d=d)
           for s, o, st, p, w, d in rows if o]
    mlb = [r for r in out if r["sport"] == "baseball_mlb"]
    return ([r for r in mlb if r["d"] < SWITCH],
            [r for r in mlb if r["d"] >= SWITCH],
            [r for r in out if r["sport"] != "baseball_mlb" and r["d"] >= SWITCH])


def _drop_day(rows, worst=False):
    """Rows minus the single best (or worst) PnL day — robustness both ways:
    a negative verdict must survive removing the worst day, a positive one the
    best."""
    by = {}
    for r in rows:
        by[r["d"]] = by.get(r["d"], 0.0) + (r["pnl"] or 0)
    if not by:
        return rows
    pick = min(by, key=by.get) if worst else max(by, key=by.get)
    return [r for r in rows if r["d"] != pick]


def analyze(pre, fwd, ctrl):
    a = dict(pre=_stats(pre), fwd=_stats(fwd), ctrl=_stats(ctrl))
    a["z_regime"] = two_prop_z(a["pre"]["w"], a["pre"]["n"], a["fwd"]["w"], a["fwd"]["n"])
    a["consistent"] = abs(a["z_regime"]) < 1.96
    pooled = pre + fwd
    a["pooled"] = _stats(pooled)
    basis_rows = pooled if a["consistent"] else fwd
    a["basis"] = "pooled" if a["consistent"] else "forward-only"
    b = _stats(basis_rows)
    a["basis_stats"] = b
    a["drop_best"] = _stats(_drop_day(basis_rows))
    a["drop_worst"] = _stats(_drop_day(basis_rows, worst=True))
    a["z_vs_ctrl"] = two_prop_z(a["fwd"]["w"], a["fwd"]["n"], a["ctrl"]["w"], a["ctrl"]["n"])

    if a["fwd"]["n"] < GATE_N:
        a["verdict"], a["tier"] = "대기", "-"
        return a
    passes = b["lb"] > b["be"]
    negative = b["per"] <= 0 and a["drop_worst"]["per"] <= 2.0   # must survive its worst day
    if passes:
        a["verdict"] = "MLB 유지"
    elif negative:
        a["verdict"] = "MLB만 Med ML에서 제외 권고"
    else:
        a["verdict"] = "판정 보류 — 계속 관찰"
    strong = a["fwd"]["n"] >= 80 and (a["ctrl"]["per"] - a["fwd"]["per"]) > 10
    a["tier"] = "★4" if strong and a["verdict"] != "판정 보류 — 계속 관찰" else "★3"
    return a


def fmt(t):
    return ("n=%d %d승%d패 WR %.1f%% vs BE %.1f%% · 베팅당 %+.1f%% · Wilson하한 %.1f%%"
            % (t["n"], t["w"], t["n"] - t["w"], t["wr"], t["be"], t["per"], t["lb"]))


def report(a):
    L = ["📊 MLB Med ML 재유도 (게이트 n≥%d 도달)" % GATE_N, ""]
    L.append("전환 전  : " + fmt(a["pre"]))
    L.append("전진(8/05+): " + fmt(a["fwd"]))
    L.append("대조군(비MLB): " + fmt(a["ctrl"]))
    L.append("")
    L.append("레짐 z=%.2f → %s (판정 기반: %s)"
             % (a["z_regime"], "일관" if a["consistent"] else "불일치", a["basis"]))
    L.append("판정 기반: " + fmt(a["basis_stats"]))
    L.append("최고일 제외: %+.1f%% · 최악일 제외: %+.1f%%"
             % (a["drop_best"]["per"], a["drop_worst"]["per"]))
    L.append("MLB vs 대조군 z=%.2f" % a["z_vs_ctrl"])
    L.append("")
    L.append("권고: %s (%s)" % (a["verdict"], a["tier"]))
    L.append("결정은 유저 몫 — 변경 시 BET_TIER_ERAS + keepmypick RULE_CHANGES 동시 갱신.")
    return "\n".join(L)


def main():
    if os.path.exists(STATE):
        return
    db = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    try:
        pre, fwd, ctrl = cohort(db)
    finally:
        db.close()
    if len(fwd) < GATE_N:
        print("[mlb-rederivation] waiting: forward n=%d < %d" % (len(fwd), GATE_N))
        return
    a = analyze(pre, fwd, ctrl)
    msg = report(a)
    print(msg)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import softbook_ev_recorder as rec
    if rec.send_telegram(msg) is not None:
        with open(STATE, "w") as f:
            f.write("sent\n")
    # unsent (telegram down): no state file, tomorrow retries — a lost report
    # is worse than a repeated one.


if __name__ == "__main__":
    main()
