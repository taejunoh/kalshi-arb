"""late_close_tail_v1 LIVE-promotion what-if simulator.

Replays the observer's 5/21-morning n=110 bucket snapshot through 4 candidate
LIVE configs and prints expected PnL / contracts / fee burden side-by-side.
The simulator is deterministic — no random noise — so the numbers represent
"what would have happened over the observed 5/19-5/21 window if these gates
had been live during that period".

Assumptions:
  • Paper signal price == LIMIT order price (LIMIT_TTL routing, no slip on
    fills); per [[crypto_phase1_fok_fix_2026-05-19]].
  • LIVE fill rate haircut: 85% (the other 15% TTL-expire at $0 PnL).
    Based on deribit_tail_fade_v1 / atm_hold_v1 60s-TTL observed fill behavior.
  • Kalshi fee = ceil(7 * na * (1-na)) / 100 per contract.
  • LIVE contracts = floor($S / na); paper is fractional.

CLI:
  venv/bin/python scripts/late_close_tail_promo_sim.py
  venv/bin/python scripts/late_close_tail_promo_sim.py --fill-rate 0.7
  venv/bin/python scripts/late_close_tail_promo_sim.py --window-days 1.6
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone


# Snapshot from [[late_close_tail_v1_subcohort_audit_2026-05-20]] 5/21 update.
# Fields: (na_mid, n, gross_pnl_per_ct, wr). 110 total settled fires across
# 5/19 22:50 UTC — 5/21 13:00 UTC (~38h, 110 fires => ~70/day weighted toward
# 5/20 crash regime, ~37/day in steady regime).
BUCKETS: list[tuple[float, int, float, float]] = [
    (0.770, 3,  +0.223, 1.00),
    (0.805, 4,  +0.198, 1.00),
    (0.825, 1,  +0.170, 1.00),
    (0.845, 1,  +0.160, 1.00),
    (0.865, 2,  +0.130, 1.00),
    (0.885, 5,  -0.282, 0.60),  # cliff bucket
    (0.905, 1,  +0.100, 1.00),
    (0.925, 5,  +0.080, 1.00),
    (0.945, 6,  +0.058, 1.00),
    (0.965, 24, +0.033, 1.00),
    (0.985, 58, +0.020, 1.00),
]

SNAPSHOT_WINDOW_HOURS = 38.0   # 5/19 22:50 UTC -> 5/21 13:00 UTC
TOTAL_N = sum(b[1] for b in BUCKETS)


OBSERVER_NAME = "late_close_tail"
YA_MIN = 0.05
YA_MAX = 0.30
COOLDOWN_S = 30.0
COOLDOWN_PRICE_DIFF = 0.03


@dataclass
class Config:
    name: str
    size_usdc: float
    max_no_ask: float
    exclude_low: float
    exclude_high: float
    note: str


def _accepted(cfg: Config, na: float) -> bool:
    if na > cfg.max_no_ask:
        return False
    if cfg.exclude_low < cfg.exclude_high and cfg.exclude_low <= na <= cfg.exclude_high:
        return False
    return True


def _kalshi_fee(na: float) -> float:
    return math.ceil(7.0 * na * (1.0 - na)) / 100.0


def _simulate(cfg: Config, fill_rate: float) -> dict:
    """Apply gates to BUCKETS and accumulate per-fire PnL.

    Per-fire LIVE PnL is computed from the *paper gross/ct* (which already
    bakes in the bucket's empirical WR) minus Kalshi fee, scaled by integer
    LIVE contract count. Fill-rate haircut multiplies total LIVE PnL.
    """
    n_fires = 0
    n_cliff = 0
    total_contracts_live = 0
    paper_gross = 0.0
    live_gross = 0.0
    live_fees = 0.0
    worst_bucket_dollar = 0.0
    for na, n, gross_ct, _wr in BUCKETS:
        if not _accepted(cfg, na):
            continue
        n_fires += n
        if 0.88 <= na <= 0.89:
            n_cliff += n
        contracts_live = math.floor(cfg.size_usdc / na)
        contracts_paper = cfg.size_usdc / na
        fee_ct = _kalshi_fee(na)
        bucket_paper_gross = n * contracts_paper * gross_ct
        bucket_live_gross = n * contracts_live * gross_ct
        bucket_live_fees = n * contracts_live * fee_ct
        bucket_live_net = bucket_live_gross - bucket_live_fees
        paper_gross += bucket_paper_gross
        live_gross += bucket_live_gross
        live_fees += bucket_live_fees
        total_contracts_live += n * contracts_live
        worst_bucket_dollar = min(worst_bucket_dollar, bucket_live_net)
    live_net_raw = live_gross - live_fees
    live_net_after_fill = live_net_raw * fill_rate
    return {
        "n_fires": n_fires,
        "n_cliff": n_cliff,
        "contracts_live": total_contracts_live,
        "paper_gross": paper_gross,
        "live_gross": live_gross,
        "live_fees": live_fees,
        "live_net_raw": live_net_raw,
        "live_net_after_fill": live_net_after_fill,
        "worst_bucket_dollar": worst_bucket_dollar,
    }


def _load_signals(db_path: str) -> list[dict]:
    """Read all late_close_tail observer signals, sorted by ts ASC."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT ts, market_id, predicted_outcome, actual_outcome, features_json
               FROM crypto_observer_signals
               WHERE observer = ?
               ORDER BY ts ASC""",
            (OBSERVER_NAME,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for ts_iso, market_id, pred, actual, fj in rows:
        try:
            ts = datetime.fromisoformat(ts_iso)
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            f = json.loads(fj or "{}")
        except Exception:
            continue
        na = f.get("na")
        ya = f.get("ya")
        if na is None or ya is None:
            continue
        out.append({
            "ts": ts,
            "market_id": market_id,
            "predicted_outcome": pred,
            "actual_outcome": actual,
            "na": float(na),
            "ya": float(ya),
        })
    return out


def _replay_db(cfg: Config, signals: list[dict], fill_rate: float) -> dict:
    """Replay signals chronologically applying ALL trader gates:

      1. ya band [YA_MIN, YA_MAX]               (paper-existing)
      2. predicted_outcome == 'no'              (paper-existing)
      3. na <= cfg.max_no_ask                   (LIVE-promo)
      4. na NOT in [exclude_low, exclude_high]  (LIVE-promo)
      5. per-ticker cooldown (30s AND 3c)       (Phase 1 default)

    Tracks per-ticker last-fire (ts, price) for the cooldown gate.
    """
    last_fire: dict[str, tuple[datetime, float]] = {}
    fires: list[dict] = []
    skipped = Counter()
    for s in signals:
        if s["predicted_outcome"] != "no":
            skipped["pred_not_no"] += 1
            continue
        if not (YA_MIN <= s["ya"] <= YA_MAX):
            skipped["ya_band"] += 1
            continue
        if s["na"] > cfg.max_no_ask:
            skipped["max_no_ask"] += 1
            continue
        if cfg.exclude_low < cfg.exclude_high and cfg.exclude_low <= s["na"] <= cfg.exclude_high:
            skipped["exclude_band"] += 1
            continue
        # 30s+3c cooldown — AND semantics, see crypto_observer_phase1.py:60-95
        prev = last_fire.get(s["market_id"])
        if prev is not None:
            age = (s["ts"] - prev[0]).total_seconds()
            if 0 <= age < COOLDOWN_S and abs(s["na"] - prev[1]) < COOLDOWN_PRICE_DIFF:
                skipped["cooldown_30s_3c"] += 1
                continue
        last_fire[s["market_id"]] = (s["ts"], s["na"])
        fires.append(s)

    # Accumulate PnL across settled fires
    n_total = len(fires)
    n_settled = sum(1 for f in fires if f["actual_outcome"] is not None)
    n_cliff = sum(1 for f in fires if 0.88 <= f["na"] <= 0.89)
    paper_gross = 0.0
    live_gross = 0.0
    live_fees = 0.0
    contracts_live_total = 0
    worst_per_trade = 0.0
    for f in fires:
        if f["actual_outcome"] is None:
            continue
        na = f["na"]
        contracts_live = math.floor(cfg.size_usdc / na)
        contracts_paper = cfg.size_usdc / na
        fee_ct = _kalshi_fee(na)
        won = (f["predicted_outcome"] == f["actual_outcome"])
        if won:
            g_live = contracts_live * (1.0 - na)
            g_paper = contracts_paper * (1.0 - na)
        else:
            g_live = -contracts_live * na
            g_paper = -contracts_paper * na
        fees_trade = contracts_live * fee_ct
        net_trade = g_live - fees_trade
        paper_gross += g_paper
        live_gross += g_live
        live_fees += fees_trade
        contracts_live_total += contracts_live
        worst_per_trade = min(worst_per_trade, net_trade)
    live_net_raw = live_gross - live_fees
    return {
        "n_fires": n_total,
        "n_settled": n_settled,
        "n_cliff": n_cliff,
        "contracts_live": contracts_live_total,
        "paper_gross": paper_gross,
        "live_gross": live_gross,
        "live_fees": live_fees,
        "live_net_raw": live_net_raw,
        "live_net_after_fill": live_net_raw * fill_rate,
        "worst_bucket_dollar": worst_per_trade,
        "skipped": dict(skipped),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill-rate", type=float, default=0.85)
    ap.add_argument("--window-days", type=float, default=SNAPSHOT_WINDOW_HOURS / 24.0)
    ap.add_argument("--from-db", default=None,
                    help="If set, replay chronologically from this DB instead of "
                         "the hardcoded bucket snapshot. Applies ALL gates (incl. cooldown).")
    args = ap.parse_args()

    configs = [
        Config("A. AS-IS paper ($20)",        20.0, 1.00, 0.0, 0.0,
               "current paper config; baseline"),
        Config("B. MAX_NO_ASK 0.94 ($20)",    20.0, 0.94, 0.0, 0.0,
               "cuts the rounding-residual ≥0.95 zone"),
        Config("C. B + EXCLUDE 0.88-0.89",    20.0, 0.94, 0.88, 0.89,
               "additionally drops cliff bucket"),
        Config("D. C + $5 micro-pilot",        5.0, 0.94, 0.88, 0.89,
               "Stage B size; same gates as C"),
    ]

    signals: list[dict] | None = None
    window_days = args.window_days
    total_observed = TOTAL_N
    if args.from_db:
        signals = _load_signals(args.from_db)
        if not signals:
            print(f"ERROR: no late_close_tail signals in {args.from_db}", flush=True)
            return 1
        ts_min = min(s["ts"] for s in signals)
        ts_max = max(s["ts"] for s in signals)
        window_secs = (ts_max - ts_min).total_seconds()
        window_days = window_secs / 86400.0
        total_observed = len(signals)
        print(f"late_close_tail_v1 LIVE-promotion simulator  [DB-replay]")
        print(f"db: {args.from_db}")
        print(f"window: {ts_min.isoformat()} -> {ts_max.isoformat()}  "
              f"({window_days*24:.1f}h / {window_days:.2f}d)")
        print(f"total signals: {total_observed}  "
              f"(settled: {sum(1 for s in signals if s['actual_outcome'] is not None)})")
    else:
        print(f"late_close_tail_v1 LIVE-promotion simulator  [bucket-snapshot]")
        print(f"snapshot window: {SNAPSHOT_WINDOW_HOURS:.1f}h ({window_days:.2f}d)  "
              f"total observed: n={total_observed}")
    print(f"fill rate assumed: {args.fill_rate*100:.0f}%  "
          f"(15% TTL-expire @ $0 PnL on remaining)")
    print()
    header = (
        f"{'config':<28s} "
        f"{'n_fire':>6s} "
        f"{'cliff':>5s} "
        f"{'ct_LIVE':>7s} "
        f"{'p_gross':>8s} "
        f"{'L_gross':>8s} "
        f"{'fees':>7s} "
        f"{'L_net':>7s} "
        f"{'L_x_fill':>8s} "
        f"{'$/day':>7s} "
        f"{'worst_b':>7s}"
    )
    print(header)
    print("-" * len(header))
    last_skipped: dict[str, int] | None = None
    for cfg in configs:
        if args.from_db:
            r = _replay_db(cfg, signals or [], args.fill_rate)
            last_skipped = r["skipped"]
        else:
            r = _simulate(cfg, args.fill_rate)
        per_day = r["live_net_after_fill"] / window_days
        print(
            f"{cfg.name:<28s} "
            f"{r['n_fires']:>6d} "
            f"{r['n_cliff']:>5d} "
            f"{r['contracts_live']:>7d} "
            f"{r['paper_gross']:>+8.2f} "
            f"{r['live_gross']:>+8.2f} "
            f"{r['live_fees']:>7.2f} "
            f"{r['live_net_raw']:>+7.2f} "
            f"{r['live_net_after_fill']:>+8.2f} "
            f"{per_day:>+7.2f} "
            f"{r['worst_bucket_dollar']:>+7.2f}"
        )
    print()
    print("Legend:")
    print("  n_fire    = # paper observer fires that pass the gate")
    print("  cliff     = # of those in the 0.88-0.89 -EV bucket")
    print("  ct_LIVE   = total contracts after integer-floor sizing")
    print("  p_gross   = paper-fractional gross PnL (baseline reference)")
    print("  L_gross   = LIVE gross (integer contracts, before fees)")
    print("  fees      = total Kalshi fees")
    print("  L_net     = L_gross - fees  (assuming 100% fills)")
    print("  L_x_fill  = L_net × fill_rate  (realistic expected LIVE PnL)")
    print("  $/day     = L_x_fill / window_days  (forward-rate proxy)")
    print("  worst_b   = most negative single-bucket $ contribution")
    print()
    print("Forward extrapolation (assuming same regime mix):")
    for cfg in configs:
        if args.from_db:
            r = _replay_db(cfg, signals or [], args.fill_rate)
        else:
            r = _simulate(cfg, args.fill_rate)
        per_day = r["live_net_after_fill"] / window_days
        print(f"  {cfg.name:<28s}  7d: ${per_day*7:+.2f}   30d: ${per_day*30:+.2f}")

    if last_skipped:
        print()
        print("Skip breakdown (Config D — strictest gates):")
        for reason, count in sorted(last_skipped.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<24s} {count:>5d}")
    print()
    print("Caveats:")
    print("  • 5/20 was BTC crash regime — high realized vol = more cascades.")
    print("    Steady regime (5/19+5/21 ~37/day) projects ~half the daily.")
    print("  • cliff (0.88-0.89) n=5 is too thin to be sure +EV vs -EV.")
    print("    1 catastrophic cascade event at +$0.32 size could swing $5+.")
    print("  • Fill rate 85% is the deribit_tail_fade reference; late_close_tail")
    print("    fires in shallower books on average → could be 70-80%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
