"""Daily ④ REAL_FEE_GATE paper-mode report.

Since `REAL_FEE_GATE_MODE=paper` was activated (2026-06-05 00:36 UTC), the PAPER
(non-live) strategies evaluate their +EV gate with the REAL Kalshi fee
`0.07*p*(1-p)` (a fraction of price ~4-9x lower than the old flat $0.07), so they
fire on marginal trades the inflated flat-fee gate was rejecting. The LIVE v31
family stays on the legacy gate (is_live=True) — unchanged, used here as a control.

This tracks, day over day, (1) whether the ④-affected paper strategies now fire
MORE than their pre-activation baseline, (2) where (price band), (3) that LIVE v31
firing stayed flat, and (4) whether the post-④ paper cohort is +EV — the evidence
needed before any `REAL_FEE_GATE_MODE=all` (live) flip.

Read-only on the DB. Armed via a daily systemd timer. Pass --print-only to skip Telegram.
Context: memory ec2_git_reconcile_2026-06-04.md.
"""
import asyncio
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import Config

ACT = "2026-06-05 00:36:13"  # REAL_FEE_GATE_MODE=paper activation (UTC)
DB = "/home/ubuntu/kalshi-arb/data/bot.db"


def build_report(cfg) -> str:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA query_only=1")

    def q(sql, args=()):
        return con.execute(sql, args).fetchall()

    live = sorted(cfg.LIVE_STRATEGIES)
    ph = ",".join("?" * len(live)) if live else "''"  # placeholders for LIVE set
    days = q("SELECT (julianday('now') - julianday(?))", (ACT,))[0][0] or 0.0
    days = max(days, 1e-9)

    L = ["\U0001F4CA *④ real-fee gate — daily paper report*",
         f"_{days:.1f}d since paper-mode activation (06-05 00:36 UTC, mode={cfg.REAL_FEE_GATE_MODE})_", ""]

    # --- 1. ④-affected (non-live) paper firing: post vs pre-7d daily baseline ---
    post = q(f"SELECT count(*) FROM trades WHERE datetime(timestamp)>=datetime(?) "
             f"AND status LIKE 'paper%' AND strategy NOT IN ({ph})", (ACT, *live))[0][0]
    pre = q(f"SELECT count(*) FROM trades WHERE datetime(timestamp)>=datetime(?,'-7 days') "
            f"AND datetime(timestamp)<datetime(?) AND status LIKE 'paper%' "
            f"AND strategy NOT IN ({ph})", (ACT, ACT, *live))[0][0]
    pre_daily, post_daily = pre / 7.0, post / days
    L.append("*Paper firing (④-affected, non-live):*")
    L.append(f"• post-④: {post} fires ({post_daily:.1f}/day) vs pre-7d {pre_daily:.1f}/day")
    if pre_daily <= 0:
        L.append("  → (no pre-baseline)" if post == 0 else f"  → ▲ new firing (pre baseline 0)")
    elif post_daily > pre_daily * 1.05:
        L.append(f"  → ▲ {(post_daily / pre_daily - 1) * 100:.0f}% more — ④ opening trades")
    elif post_daily < pre_daily * 0.95:
        L.append("  → ▼ fewer (schedule/variance — likely too little data yet)")
    else:
        L.append("  → ~flat")

    # --- 2. post-④ paper fires by price band (④ opens 0.34-0.35 payoff + wider edge windows) ---
    bands = q(f"""SELECT CASE
        WHEN price<0.30 THEN '<.30' WHEN price<0.35 THEN '.30-.35'
        WHEN price<0.40 THEN '.35-.40' WHEN price<0.50 THEN '.40-.50'
        WHEN price<0.60 THEN '.50-.60' ELSE '.60+' END AS band, count(*) AS n
        FROM trades WHERE datetime(timestamp)>=datetime(?) AND status LIKE 'paper%'
        AND strategy NOT IN ({ph}) GROUP BY band ORDER BY band""", (ACT, *live))
    if bands:
        L.append("• by price band: " + "  ".join(f"{b}={n}" for b, n in bands))

    # --- 3. top firing paper strategies post-④ ---
    tops = q(f"SELECT strategy, count(*) AS n FROM trades WHERE datetime(timestamp)>=datetime(?) "
             f"AND status LIKE 'paper%' AND strategy NOT IN ({ph}) GROUP BY strategy "
             f"ORDER BY n DESC LIMIT 5", (ACT, *live))
    if tops:
        L.append("• top: " + ", ".join(f"{s}({n})" for s, n in tops))

    # --- 4. control: LIVE v31 firing (must stay ~flat — ④ didn't touch live) ---
    lpost = q(f"SELECT count(*) FROM trades WHERE datetime(timestamp)>=datetime(?) "
              f"AND status IN ('filled','settled') AND strategy IN ({ph})", (ACT, *live))[0][0] if live else 0
    lpre = q(f"SELECT count(*) FROM trades WHERE datetime(timestamp)>=datetime(?,'-7 days') "
             f"AND datetime(timestamp)<datetime(?) AND status IN ('filled','settled') "
             f"AND strategy IN ({ph})", (ACT, ACT, *live))[0][0] if live else 0
    L.append(f"*Control — LIVE v31:* post {lpost} ({lpost / days:.1f}/day) vs pre {lpre / 7.0:.1f}/day _(should be ~flat)_")

    # --- 5. +EV of the post-④ paper cohort (settled) ---
    n, pnl, wins = q(f"SELECT count(*), COALESCE(sum(pnl),0), "
                     f"COALESCE(sum(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) FROM trades "
                     f"WHERE datetime(timestamp)>=datetime(?) AND status='paper_settled' "
                     f"AND strategy NOT IN ({ph})", (ACT, *live))[0]
    if n:
        L.append(f"*+EV of post-④ paper cohort (settled n={n}):* "
                 f"PnL ${pnl:+.2f} | WR {wins / n * 100:.0f}% | avg ${pnl / n:+.3f}/trade")
    else:
        L.append("*+EV of post-④ paper cohort:* none settled yet")

    L.append("")
    L.append("_Flip REAL_FEE_GATE_MODE=all (live) only once this post-④ cohort shows a stable "
             "+EV by-band at n≥~50. Markets are efficient — expect a small effect._")
    con.close()
    return "\n".join(L)


async def main():
    cfg = Config()
    text = build_report(cfg)
    print(text)
    if "--print-only" not in sys.argv and cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
        from monitoring.notifier import TelegramNotifier
        await TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID).send(
            text, msg_type="real_fee_gate_report")


if __name__ == "__main__":
    asyncio.run(main())
