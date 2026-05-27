"""Mass observer Phase 0 backtest — sweep all observers for +EV cohorts.

Generalizes scripts/observer_phase0_backtest.py to all observers in
crypto_observer_signals. Handles varied feature_json schemas (ya/na, ya/no_ask,
ya/yb→derive na, etc.) and outputs a single ranked report.

Reports per observer:
  - Pooled EV (original direction)
  - Flipped (contrarian) EV
  - Best price-band cohort by EV
  - Any band with WR ≥ 60% AND n ≥ 50 AND EV ≥ +$0.02/ct

Output: writes ranked findings to data/observer_mass_findings.md.
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sim_reliability import assess, format_result

FEE = 0.07
LOOKBACK_DAYS = 30
MIN_N_BAND = 50
MIN_N_OBSERVER = 200
DB = "/home/ubuntu/kalshi-arb/data/bot.db"
OUT = "/home/ubuntu/kalshi-arb/data/observer_mass_findings.md"


def extract_prices(features):
    """Return (ya, na) for any common observer feature schema, or (None, None).
    Priority: explicit na > no_ask > derived 1-yb."""
    ya = features.get("ya")
    if ya is None:
        ya = features.get("target_ya") or features.get("yes_ask")
    na = features.get("na")
    if na is None:
        na = features.get("no_ask") or features.get("target_na")
    if na is None and features.get("yb") is not None:
        # No-ask = 1 - Yes-bid (arbitrage-free book)
        na = 1.0 - features["yb"]
    return ya, na


def pnl(pred, ya, na, actual):
    """Per-contract PnL with $0.07 flat fee."""
    if pred == "yes":
        price, win = ya, (actual == "yes")
    else:
        price, win = na, (actual == "no")
    if price is None or price <= 0 or price >= 1:
        return None
    return (1.0 - price - FEE) if win else (-price - FEE)


def band_of(price):
    if price is None: return "X"
    if price < 0.05: return "A_<0.05"
    if price < 0.10: return "B_0.05-0.10"
    if price < 0.20: return "C_0.10-0.20"
    if price < 0.35: return "D_0.20-0.35"
    if price < 0.50: return "E_0.35-0.50"
    if price < 0.65: return "F_0.50-0.65"
    if price < 0.80: return "G_0.65-0.80"
    if price < 0.93: return "H_0.80-0.93"
    return "I_>=0.93"


def run_observer(c, observer):
    cur = c.execute(
        """SELECT predicted_outcome, actual_outcome, features_json, ts
           FROM crypto_observer_signals
           WHERE observer = ?
             AND actual_outcome IS NOT NULL
             AND ts > datetime('now', ?)""",
        (observer, f"-{LOOKBACK_DAYS} days"),
    )

    pooled = {"n": 0, "pnl": 0.0, "wins": 0}
    flipped = {"n": 0, "pnl": 0.0, "wins": 0}
    bands = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    flip_bands = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    # Per-(direction, band) per-row entries for sim_reliability assessment
    band_entries: dict = defaultdict(list)
    rejected = 0

    for pred, actual, features_json, ts in cur:
        try:
            f = json.loads(features_json)
        except Exception:
            rejected += 1; continue
        ya, na = extract_prices(f)
        p = pnl(pred, ya, na, actual)
        if p is None:
            rejected += 1; continue
        pooled["n"] += 1; pooled["pnl"] += p
        if p > 0: pooled["wins"] += 1
        price = ya if pred == "yes" else na
        b = band_of(price)
        bands[b]["n"] += 1; bands[b]["pnl"] += p
        if p > 0: bands[b]["wins"] += 1
        band_entries[("orig", b)].append({"ts": ts, "pnl": p, "price": price})
        # Flipped (contrarian)
        flip_pred = "no" if pred == "yes" else "yes"
        fp = pnl(flip_pred, ya, na, actual)
        if fp is not None:
            flipped["n"] += 1; flipped["pnl"] += fp
            if fp > 0: flipped["wins"] += 1
            flip_price = ya if flip_pred == "yes" else na
            fb = band_of(flip_price)
            flip_bands[fb]["n"] += 1; flip_bands[fb]["pnl"] += fp
            if fp > 0: flip_bands[fb]["wins"] += 1
            band_entries[("flip", fb)].append({"ts": ts, "pnl": fp, "price": flip_price})

    return {
        "observer": observer,
        "rejected": rejected,
        "pooled": pooled,
        "flipped": flipped,
        "bands": dict(bands),
        "flip_bands": dict(flip_bands),
        "band_entries": dict(band_entries),
    }


def find_actionable_cohorts(result):
    """Return cohorts meeting: n>=MIN_N_BAND AND WR>=60% AND EV>=+$0.02/ct.
    Each cohort is enriched with sim_reliability tier assessment."""
    actionable = []
    band_entries = result.get("band_entries", {})
    for direction, source in (("orig", result["bands"]), ("flip", result["flip_bands"])):
        for band, st in source.items():
            if st["n"] < MIN_N_BAND: continue
            wr = st["wins"] / st["n"]
            ev = st["pnl"] / st["n"]
            if wr >= 0.60 and ev >= 0.02:
                cohort = {
                    "observer": result["observer"],
                    "direction": direction,
                    "band": band,
                    "n": st["n"], "wr": wr, "ev": ev, "pnl": st["pnl"],
                }
                # Enrich with sim_reliability tier — wires Phase A-F utilities
                entries = band_entries.get((direction, band), [])
                if entries:
                    try:
                        avg_price = sum(e["price"] for e in entries) / len(entries)
                        tier_result = assess(entries, sample_avg_price=avg_price)
                        cohort["tier"] = tier_result["tier"]
                        cohort["tier_verdict"] = tier_result["overall_verdict"]
                        cohort["tier_reason"] = tier_result["recommendation"]
                    except Exception as e:
                        cohort["tier"] = None
                        cohort["tier_verdict"] = "ERROR"
                        cohort["tier_reason"] = str(e)
                actionable.append(cohort)
    return actionable


def main():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA query_only = 1")
    c.execute("PRAGMA busy_timeout = 30000")
    observers = [
        r[0] for r in c.execute(
            f"""SELECT observer FROM crypto_observer_signals
                WHERE actual_outcome IS NOT NULL AND ts > datetime('now','-{LOOKBACK_DAYS} days')
                GROUP BY observer HAVING COUNT(*) >= {MIN_N_OBSERVER}
                ORDER BY observer"""
        ).fetchall()
    ]

    lines = [
        f"# Observer mass-backtest — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"_Lookback {LOOKBACK_DAYS}d. Settled signals only. ${FEE} fee/ct. "
        f"Reporting bands with n≥{MIN_N_BAND}._",
        "",
        f"Observers scanned: {len(observers)}",
        "",
    ]

    all_actionable = []
    summaries = []
    for obs in observers:
        r = run_observer(c, obs)
        actionable = find_actionable_cohorts(r)
        all_actionable.extend(actionable)
        p = r["pooled"]
        f = r["flipped"]
        summaries.append({
            "observer": obs,
            "n": p["n"],
            "pooled_ev": p["pnl"] / p["n"] if p["n"] else 0,
            "pooled_wr": p["wins"] / p["n"] if p["n"] else 0,
            "flipped_ev": f["pnl"] / f["n"] if f["n"] else 0,
            "best_band": None,
            "actionable_count": len(actionable),
        })

    lines.append("## Actionable cohorts (n>={}, WR>=60%, EV>=+$0.02/ct)".format(MIN_N_BAND))
    lines.append("")
    if not all_actionable:
        lines.append("_None._")
    else:
        lines.append("| Observer | Dir | Band | n | WR | EV/ct | PnL | Tier | Verdict |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|---|")
        for a in sorted(all_actionable, key=lambda x: -x["ev"]):
            tier = a.get("tier")
            tier_str = ("★" * tier) if tier else "—"
            verdict = a.get("tier_verdict", "—")
            lines.append(
                f"| `{a['observer']}` | {a['direction']} | {a['band']} "
                f"| {a['n']} | {100*a['wr']:.1f}% | ${a['ev']:+.4f} | ${a['pnl']:+.2f} "
                f"| {tier_str} | {verdict} |"
            )
        # Highlight high-tier cohorts (>= 3 stars) as ready for paper drill
        promotable = [a for a in all_actionable if a.get("tier", 0) >= 3]
        if promotable:
            lines.append("")
            lines.append("### Tier 3+ cohorts (passing day-skew + walk-forward)")
            for a in promotable:
                lines.append(f"- `{a['observer']}` {a['direction']} {a['band']}: "
                             f"{'*' * a['tier']} — {a['tier_reason']}")

    lines.append("")
    lines.append("## Per-observer pooled summary")
    lines.append("")
    lines.append("| Observer | n | Pooled EV/ct | Pooled WR | Flipped EV/ct | Actionable |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in sorted(summaries, key=lambda x: -x["pooled_ev"]):
        lines.append(
            f"| `{s['observer']}` | {s['n']} "
            f"| ${s['pooled_ev']:+.4f} | {100*s['pooled_wr']:.1f}% "
            f"| ${s['flipped_ev']:+.4f} | {s['actionable_count']} |"
        )

    out = "\n".join(lines)
    with open(OUT, "w") as f:
        f.write(out)
    print(f"Wrote {OUT}")
    print(out)


if __name__ == "__main__":
    main()
