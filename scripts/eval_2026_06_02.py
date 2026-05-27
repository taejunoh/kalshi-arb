"""6/02 (7d post-deploy) evaluation report for 5/26 paper deploys.

Generates a complete go/no-go decision matrix for the 5 paper strategies
deployed 5/26. Run on or after 6/02 to make LIVE-promotion / continue-paper /
mothball decisions per [[feedback_data_driven_decisions]] + [[portfolio_audit_playbook]].

Strategies evaluated:
  - sports_mlb_pitcher_edge_v1 (look-ahead biased baseline — expect ~0 EV)
  - sports_mlb_pitcher_edge_v2 (era≥2.5 PIT)
  - sports_mlb_pitcher_edge_v2_1 (era≥2.0 PIT + tier sizing)
  - sports_tennis_pinnacle_fade_v1 (3-8pp + ya 0.40-0.70 fade)
  - sports_npb_sharp_follow_v1 (3-8pp + ya 0.30-0.70 follow)

Output sections:
  1. Per-strategy 7d snapshot (n, WR, EV, ROI, total PnL)
  2. By-band counterfactual (per ya/price band)
  3. Paper-twin sanity (for strategies with LIVE-twin pairs)
  4. Decision per promo gate criteria (from memory)
  5. Caveats + small-n warnings

Usage:
    venv/bin/python scripts/eval_2026_06_02.py [--days 7]

Writes Markdown to /home/ubuntu/kalshi-arb/data/eval_2026_06_02.md.
"""
import argparse
import os
import sqlite3
from datetime import datetime, timezone

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
OUT = os.getenv("EVAL_OUT_PATH", "/home/ubuntu/kalshi-arb/data/eval_2026_06_02.md")

STRATEGIES = [
    {
        "name": "sports_mlb_pitcher_edge_v1",
        "label": "MLB pitcher v1 (look-ahead baseline)",
        "promo_gate": "Mothball if confirmed -EV (n≥15) since v1's backtest had season-aggregate bias.",
        "deploy_ts": "2026-05-26T13:55:00+00:00",
    },
    {
        "name": "sports_mlb_pitcher_edge_v2",
        "label": "MLB pitcher v2 (era≥2.5 PIT)",
        "promo_gate": "Continue paper if n≥10 + WR≥55%. LIVE if n≥30 + WR≥60% + EV≥+\\$0.05/$1.",
        "deploy_ts": "2026-05-26T13:50:00+00:00",
    },
    {
        "name": "sports_mlb_pitcher_edge_v2_1",
        "label": "MLB pitcher v2.1 (era≥2.0 PIT + tier $4/$6/$8)",
        "promo_gate": "Continue paper if n≥10 + WR≥55%. LIVE if n≥30 + WR≥60% + EV≥+\\$0.05/$1.",
        "deploy_ts": "2026-05-26T13:56:00+00:00",
    },
    {
        "name": "sports_tennis_pinnacle_fade_v1",
        "label": "Tennis Pinnacle fade",
        "promo_gate": "21d / n≥25 unique / WR≥60% / WTA EV>+\\$0.10. Don't promote on RG-only sample.",
        "deploy_ts": "2026-05-26T14:41:00+00:00",
    },
    {
        "name": "sports_npb_sharp_follow_v1",
        "label": "NPB sharp-follow",
        "promo_gate": "45d / n≥20 unique / WR≥65%. Direction asymmetry doesn't apply within TIGHT gate.",
        "deploy_ts": "2026-05-26T15:01:00+00:00",
    },
]


def q(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return cur.fetchall()


def fmt_money(v):
    if v is None: return "$0"
    return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"


def fmt_pct(v):
    if v is None: return "—"
    return f"{v:.1f}%"


def section_strategy_snapshot(conn, strat, days):
    name = strat["name"]
    deploy_ts = strat["deploy_ts"]
    rows = q(conn, """
        SELECT
          COUNT(*) AS fires,
          SUM(CASE WHEN status='paper_settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN status='paper_settled' AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN status='paper_settled' AND pnl < 0 THEN 1 ELSE 0 END) AS losses,
          ROUND(SUM(CASE WHEN status='paper_settled' THEN pnl ELSE 0 END), 2) AS pnl,
          ROUND(SUM(CASE WHEN status='paper_settled' THEN size ELSE 0 END), 2) AS volume,
          ROUND(MAX(CASE WHEN status='paper_settled' THEN pnl END), 2) AS best,
          ROUND(MIN(CASE WHEN status='paper_settled' THEN pnl END), 2) AS worst
        FROM trades
        WHERE strategy = ?
          AND timestamp >= ?
          AND timestamp < datetime(?, '+' || ? || ' days')
    """, (name, deploy_ts, deploy_ts, days))
    if not rows:
        return None
    fires, settled, wins, losses, pnl, vol, best, worst = rows[0]
    wl = (wins or 0) + (losses or 0)
    wr = (100 * (wins or 0) / wl) if wl else None
    roi = (100 * (pnl or 0) / (vol or 1)) if vol else None
    return {
        "fires": fires, "settled": settled or 0, "wins": wins or 0, "losses": losses or 0,
        "pnl": pnl or 0, "vol": vol or 0, "wr": wr, "roi": roi,
        "best": best, "worst": worst,
    }


def section_by_band(conn, name, deploy_ts, days):
    """Counterfactual: PnL grouped by entry-price band (5pp bins, focused range)."""
    rows = q(conn, """
        SELECT
          CASE
            WHEN price < 0.15 THEN 'A_<0.15'
            WHEN price < 0.25 THEN 'B_0.15-0.25'
            WHEN price < 0.35 THEN 'C_0.25-0.35'
            WHEN price < 0.45 THEN 'D_0.35-0.45'
            WHEN price < 0.55 THEN 'E_0.45-0.55'
            WHEN price < 0.65 THEN 'F_0.55-0.65'
            WHEN price < 0.75 THEN 'G_0.65-0.75'
            ELSE 'H_>=0.75'
          END AS band,
          COUNT(*) AS n,
          SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
          ROUND(SUM(pnl), 2) AS pnl,
          ROUND(AVG(price), 3) AS avg_price
        FROM trades
        WHERE strategy = ?
          AND status = 'paper_settled'
          AND timestamp >= ?
          AND timestamp < datetime(?, '+' || ? || ' days')
        GROUP BY band ORDER BY band
    """, (name, deploy_ts, deploy_ts, days))
    return rows


def section_paper_vs_live(conn, name, deploy_ts, days):
    """For strategies with LIVE counterparts, compare PnL."""
    rows = q(conn, """
        SELECT
          CASE WHEN status LIKE 'paper%' THEN 'paper' ELSE 'live' END AS bucket,
          COUNT(*) AS fires,
          SUM(CASE WHEN status IN ('paper_settled','settled') THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN status IN ('paper_settled','settled') AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
          ROUND(SUM(CASE WHEN status IN ('paper_settled','settled') THEN pnl ELSE 0 END), 2) AS pnl
        FROM trades
        WHERE strategy = ?
          AND timestamp >= ?
          AND timestamp < datetime(?, '+' || ? || ' days')
        GROUP BY bucket
    """, (name, deploy_ts, deploy_ts, days))
    return rows


def decide(strat, snap):
    """Return (verdict, reasoning) per the strategy's promo gate."""
    if not snap or snap["settled"] == 0:
        return ("WAIT", "No settled fires yet — continue paper")
    wr = snap["wr"] or 0
    n = snap["settled"]
    ev_per_dollar = (snap["pnl"] / snap["vol"]) if snap["vol"] else 0
    name = strat["name"]
    if name == "sports_mlb_pitcher_edge_v1":
        if n >= 15 and wr < 50:
            return ("MOTHBALL", f"v1 baseline confirmed -EV (n={n}, WR {wr:.0f}%) — biased backtest")
        return ("CONTINUE", f"n={n} not yet conclusive")
    if name in ("sports_mlb_pitcher_edge_v2", "sports_mlb_pitcher_edge_v2_1"):
        if n >= 30 and wr >= 60 and ev_per_dollar >= 0.05:
            return ("PROMOTE LIVE", f"Hit LIVE gate (n={n}, WR {wr:.0f}%, EV +${ev_per_dollar:.3f}/$1)")
        if n >= 10 and wr >= 55:
            return ("CONTINUE", f"On track (n={n}, WR {wr:.0f}%)")
        if n >= 15 and wr < 45:
            return ("MOTHBALL", f"WR {wr:.0f}% confirms -EV at n={n}")
        return ("WAIT", f"n={n} too small to judge")
    if name == "sports_tennis_pinnacle_fade_v1":
        return ("WAIT (21d gate)", f"n={n} — 21d eval is at 2026-06-16, not 6/02")
    if name == "sports_npb_sharp_follow_v1":
        return ("WAIT (45d gate)", f"n={n} — 45d eval is at 2026-07-10, not 6/02")
    return ("REVIEW", "manual decision")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Window post-deploy")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA busy_timeout = 30000")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# 6/02 ({args.days}d) Eval Report — generated {now}",
        f"_5 paper strategies deployed 2026-05-26. Window = {args.days}d post-deploy._",
        "",
    ]

    summary_rows = []
    for strat in STRATEGIES:
        snap = section_strategy_snapshot(conn, strat, args.days)
        if not snap:
            lines.append(f"## {strat['label']} — no data\n")
            continue
        verdict, reasoning = decide(strat, snap)
        summary_rows.append((strat["label"], snap, verdict, reasoning))

        lines.append(f"## {strat['label']}")
        lines.append(f"**Promo gate:** {strat['promo_gate']}")
        lines.append("")
        lines.append(f"- Fires: **{snap['fires']}** total ({snap['settled']} settled)")
        if snap['wr'] is not None:
            lines.append(f"- WR: **{fmt_pct(snap['wr'])}** ({snap['wins']}W / {snap['losses']}L)")
            lines.append(f"- PnL: **{fmt_money(snap['pnl'])}** on {fmt_money(snap['vol'])} volume "
                         f"({fmt_pct(snap['roi'])} ROI)")
            lines.append(f"- Best: {fmt_money(snap['best'])} / Worst: {fmt_money(snap['worst'])}")
        lines.append(f"- **Verdict: {verdict}** — {reasoning}")
        lines.append("")

        # By-band counterfactual
        bands = section_by_band(conn, strat["name"], strat["deploy_ts"], args.days)
        if bands:
            lines.append("### By price band")
            lines.append("| Band | n | WR | PnL | avg_price |")
            lines.append("|---|---:|---:|---:|---:|")
            for band, n, wins, pnl, avg_price in bands:
                wr_b = (100 * wins / n) if n else 0
                lines.append(f"| {band} | {n} | {wr_b:.0f}% | {fmt_money(pnl)} | {avg_price} |")
            lines.append("")

        # Paper vs LIVE comparison (only if both buckets present)
        comp = section_paper_vs_live(conn, strat["name"], strat["deploy_ts"], args.days)
        if comp and len(comp) > 1:
            lines.append("### Paper vs LIVE (twin)")
            lines.append("| Bucket | Fires | Settled | Wins | PnL |")
            lines.append("|---|---:|---:|---:|---:|")
            for bucket, fires, settled, wins, pnl in comp:
                lines.append(f"| {bucket} | {fires} | {settled} | {wins} | {fmt_money(pnl)} |")
            lines.append("")

    # Summary table at top of report for quick read
    summary_lines = [
        "## Summary",
        "",
        "| Strategy | n | WR | PnL | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for label, snap, verdict, _ in summary_rows:
        summary_lines.append(
            f"| {label} | {snap['settled']} | {fmt_pct(snap['wr'])} "
            f"| {fmt_money(snap['pnl'])} | **{verdict}** |"
        )
    summary_lines.append("")

    # Insert summary after the title block (after first 3 lines)
    final = lines[:3] + summary_lines + lines[3:]
    final.append("")
    final.append("---")
    final.append("## Caveats")
    final.append("- Small-n: WR/ROI are noisy below n=20. Variance bracket on n=5-15.")
    final.append("- Tennis/NPB use mirror-pair markets; settled-count counts BOTH sides of the mirror "
                 "unless PAIR_FIRE_DEDUP=True was active for that ticker.")
    final.append("- MLB v1 baseline is look-ahead biased ([[mlb_pitcher_v1_lookahead_bias_2026-05-26]]); "
                 "use it only as a forward sanity check, not a target.")
    final.append("- Promo to LIVE requires DB-replay sim parity check "
                 "([[feedback_phase0_backtest_for_promo]]).")

    out = "\n".join(final)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out)} bytes)")
    print()
    print(out)


if __name__ == "__main__":
    main()
