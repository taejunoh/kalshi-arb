"""C2 validation — does multi-observer consensus boost EV?

Before building [[2026-05-26-c2-multi-source-fv-engine.md]] infrastructure,
test the core hypothesis: when 2+ observers fire on the same Kalshi market
within a short window AND agree on direction, is EV higher than single-
observer fires?

If yes → build full consensus FV engine (8-12h).
If no → consensus is not the alpha; rethink.

Method: for each settled lognormal_fair_dev signal, check if other +EV-
candidate observers also fired on same market_id within ±60s. Bucket
trades by # of agreeing observers (1 vs 2 vs 3) and compute EV.

Candidate observers (from audit, those with non-trivial volume):
  lognormal_fair_dev (the spine — Deribit IV based)
  cme_basis_fade (CME futures spread based)
  deribit_tail_skew (Deribit IV tail skew based) — only +EV strategy
  late_close_tail (time-decay based)
  open_no_touch (touchless open based)
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

DB = "/home/ubuntu/kalshi-arb/data/bot.db"
FEE = 0.07
WINDOW_S = 60  # ±60s for "concurrent"

OBSERVERS = [
    "lognormal_fair_dev",
    "cme_basis_fade",
    "deribit_tail_skew",
    "late_close_tail",
    "open_no_touch",
]


def parse_ts(s):
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def pnl_per_contract(pred, ya, na, actual):
    if pred == "yes":
        price = ya
        win = actual == "yes"
    else:
        price = na
        win = actual == "no"
    if price is None or price <= 0 or price >= 1:
        return None
    return (1.0 - price - FEE) if win else (-price - FEE)


def main():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=10000")

    # Build index of all observer fires: market_id -> [(ts_dt, observer, pred, features)]
    print("Loading observer fire index...")
    idx: dict = defaultdict(list)
    cur = c.execute(
        """SELECT ts, observer, market_id, predicted_outcome, features_json
           FROM crypto_observer_signals
           WHERE observer IN ({})
             AND ts > strftime('%Y-%m-%dT%H:%M:%S', 'now','-30 days')""".format(
            ",".join("?" * len(OBSERVERS))
        ),
        OBSERVERS,
    )
    for ts, obs, mid, pred, fj in cur:
        try:
            ts_dt = parse_ts(ts)
            f = json.loads(fj)
        except Exception:
            continue
        idx[mid].append((ts_dt, obs, pred, f))
    print(f"Indexed {sum(len(v) for v in idx.values())} fires across {len(idx)} markets")

    # For each settled lognormal_fair_dev signal (spine), find concurrent fires
    # on the SAME market_id within ±WINDOW_S seconds.
    print("\nProcessing settled lognormal_fair_dev signals as spine...")
    by_agreers = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_pair = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    SPINE = "deribit_tail_skew"  # only +EV observer (83.6% pred WR)
    cur = c.execute(
        """SELECT ts, market_id, predicted_outcome, actual_outcome, features_json
           FROM crypto_observer_signals
           WHERE observer = ?
             AND actual_outcome IS NOT NULL
             AND ts > strftime('%Y-%m-%dT%H:%M:%S', 'now','-30 days')""",
        (SPINE,),
    )

    processed = 0
    for ts, mid, pred, actual, fj in cur:
        try:
            ts_dt = parse_ts(ts)
            f = json.loads(fj)
        except Exception:
            continue
        ya, na = f.get("ya"), f.get("na")
        p = pnl_per_contract(pred, ya, na, actual)
        if p is None:
            continue
        processed += 1

        # Find concurrent fires on this market_id from OTHER observers
        agreeing = {SPINE}  # ourselves
        for other_ts, other_obs, other_pred, _ in idx.get(mid, []):
            if other_obs == SPINE:
                continue
            dt = abs((ts_dt - other_ts).total_seconds())
            if dt > WINDOW_S:
                continue
            if other_pred == pred:
                agreeing.add(other_obs)

        n_agreers = len(agreeing)
        by_agreers[n_agreers]["n"] += 1
        by_agreers[n_agreers]["pnl"] += p
        if p > 0:
            by_agreers[n_agreers]["wins"] += 1

        # Pairwise: name the agreeing-other observer
        for obs in agreeing - {SPINE}:
            key = f"{SPINE}+{obs}"
            by_pair[key]["n"] += 1
            by_pair[key]["pnl"] += p
            if p > 0:
                by_pair[key]["wins"] += 1

    c.close()

    print(f"\nProcessed {processed} {SPINE} spine signals.\n")

    print("=" * 70)
    print(f"EV by # of agreeing observers (spine = {SPINE})")
    print("=" * 70)
    for k in sorted(by_agreers.keys()):
        st = by_agreers[k]
        ev = st["pnl"] / st["n"] if st["n"] else 0
        wr = 100 * st["wins"] / st["n"] if st["n"] else 0
        print(f"  N agreers = {k}  n={st['n']:6d}  PnL=${st['pnl']:+8.2f}  "
              f"EV=${ev:+.4f}  WR={wr:.1f}%")

    print("\n" + "=" * 70)
    print("EV by which OBSERVER paired with lognormal (within ±60s, same direction)")
    print("=" * 70)
    for k in sorted(by_pair.keys()):
        st = by_pair[k]
        if st["n"] < 20:
            continue
        ev = st["pnl"] / st["n"]
        wr = 100 * st["wins"] / st["n"]
        print(f"  {k:35} n={st['n']:5d}  PnL=${st['pnl']:+8.2f}  "
              f"EV=${ev:+.4f}  WR={wr:.1f}%")


if __name__ == "__main__":
    main()
