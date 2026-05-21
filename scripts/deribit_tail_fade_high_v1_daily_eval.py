"""Daily eval gate for deribit_tail_fade_high_v1 paper trader.

Built 2026-05-21 PM after the strategy's day-1 cohort (n=9, −$41.56) showed
the lottery profile expected by design but raised three structural watch
items: cascade multi-fire concentration, mega-event 1-cell artifact, and
KXBTC tail wipe rate (the same failure mode that retired the parent v1
gate on KXBTC). See [[deribit_tail_fade_high_v1_eval_gate_2026-05-28]].

Reports:
  • n + total PnL + ROI (paper, $20 size assumed)
  • per-day breakdown (last 7d)
  • per-event concentration (top 3 events as % gross)
  • cascade detection (any market_id with ≥4 fires that are net-loss)
  • per-asset split (KXBTC vs KXETH)

Status emitted (one of):
  PASS                — sample still building; no retire/promote trigger
  RETIRE-{1..5}       — one of the retire conditions met
  PROMOTE-candidate   — all 5 promote conditions met

RETIRE-#4 semantics (refined 2026-05-21): triggers when the NET cascade
EV (sum across all ≥4-fire events) is ≤ 0. The earlier "any losing
cascade exists" formulation false-triggered when the strategy had
simultaneous winning + losing cascades whose net was positive — observed
2026-05-21 with B77150 4× −$67 vs B77650 3× +$215 (net +$148).

Skips with no-op (exit 0, no Telegram) on the first day after deploy
(< 2026-05-22) to avoid evaluating day-1 noise.

Read-only on the DB. Idempotent.

Run modes:
  --console  Print to stdout only (no Telegram). Default for dry-test.
  --send     Send Telegram and print. Used by the systemd timer.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database

logger = logging.getLogger("deribit_tail_fade_high_v1_eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STRATEGY = "deribit_tail_fade_high_v1"
SIZE_USDC = 20.0
SKIP_BEFORE = datetime(2026, 5, 22, tzinfo=timezone.utc)

# Retire / promote thresholds (see memory entry).
MIN_N_FOR_DECISION = 30
SINGLE_EVENT_MAX_PCT = 50.0
CASCADE_MIN_FIRES = 4

# Event key: strip the binary leg suffix (-B<strike> / -T<strike>).
def _event_key(market_id: str) -> str:
    if not market_id or "-" not in market_id:
        return market_id or ""
    return market_id.rsplit("-", 1)[0]


def _asset(market_id: str) -> str:
    return (market_id or "").split("-")[0]


async def eval_strategy(send: bool):
    now = datetime.now(timezone.utc)
    if now < SKIP_BEFORE:
        logger.info(f"Today {now.date()} is before eval start {SKIP_BEFORE.date()} — skipping.")
        return

    config = Config()
    db = Database()
    await db.initialize()

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows_cur = await conn.execute(
            "SELECT timestamp, market_id, price, pnl "
            "FROM trades WHERE strategy = ? AND status = 'paper_settled' "
            "ORDER BY timestamp",
            (STRATEGY,),
        )
        rows = [dict(r) for r in await rows_cur.fetchall()]

    if not rows:
        msg = (
            f"📊 <b>{STRATEGY}</b> daily eval\n\n"
            f"No paper_settled trades yet. Check strategy is firing."
        )
        await _emit(config, msg, send)
        return

    n = len(rows)
    pnl_total = sum(r["pnl"] for r in rows)
    stake = n * SIZE_USDC
    roi = (pnl_total / stake) * 100.0 if stake > 0 else 0.0

    # Per-day breakdown (last 7d).
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    by_day: dict[str, tuple[int, float]] = {}
    days_pos = 0
    days_seen = set()
    for r in rows:
        if r["timestamp"] < cutoff_7d:
            continue
        d = r["timestamp"][:10]
        cur_n, cur_p = by_day.get(d, (0, 0.0))
        by_day[d] = (cur_n + 1, cur_p + r["pnl"])
        days_seen.add(d)
    for _, (_, p) in by_day.items():
        if p > 0:
            days_pos += 1

    # Per-event concentration.
    by_event: dict[str, float] = {}
    for r in rows:
        k = _event_key(r["market_id"])
        by_event[k] = by_event.get(k, 0.0) + r["pnl"]
    gross_pos = sum(p for p in by_event.values() if p > 0)
    top_events = sorted(by_event.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top1_pct = (top_events[0][1] / gross_pos * 100.0) if top_events and gross_pos > 0 else 0.0

    # Cascade detection: ALL market_ids with >=CASCADE_MIN_FIRES fires
    # (winners + losers). The RETIRE-#4 rule uses the NET cascade EV, not
    # "any losing cascade exists" — that earlier formulation produced false
    # retire signals when the strategy had simultaneous winning and losing
    # cascades whose sum was positive (2026-05-21 evening: B77150 4× −$67
    # losing cascade + B77650 3× +$215 winning cascade → net +$148).
    by_market: dict[str, list[float]] = {}
    for r in rows:
        by_market.setdefault(r["market_id"], []).append(r["pnl"])
    cascade_events = [
        (mid, pnls) for mid, pnls in by_market.items()
        if len(pnls) >= CASCADE_MIN_FIRES
    ]
    cascade_losers = [
        (mid, pnls) for mid, pnls in cascade_events if sum(pnls) <= 0
    ]
    cascade_net = sum(sum(pnls) for _, pnls in cascade_events)

    # Per-asset split.
    by_asset: dict[str, tuple[int, float]] = {}
    for r in rows:
        a = _asset(r["market_id"])
        cur_n, cur_p = by_asset.get(a, (0, 0.0))
        by_asset[a] = (cur_n + 1, cur_p + r["pnl"])

    # Decision logic.
    retire_triggers = []
    if n >= MIN_N_FOR_DECISION and pnl_total <= 0:
        retire_triggers.append(("2", f"n={n}≥{MIN_N_FOR_DECISION} AND net=${pnl_total:.2f}≤0"))
    if n >= MIN_N_FOR_DECISION and top1_pct > SINGLE_EVENT_MAX_PCT:
        retire_triggers.append(("3", f"top event = {top1_pct:.1f}% of gross >50%"))
    if cascade_events and cascade_net < 0:
        cascades_str = ", ".join(
            f"{mid} ({len(p)}× net ${sum(p):.0f})" for mid, p in cascade_losers[:3]
        )
        retire_triggers.append((
            "4",
            f"cascade net EV ${cascade_net:.2f}≤0 across "
            f"{len(cascade_events)} cascade event(s); losers: {cascades_str}"
        ))
    # Check 5: KXBTC-only +EV with KXETH dead.
    btc_n, btc_pnl = by_asset.get("KXBTC", (0, 0.0))
    eth_n, eth_pnl = by_asset.get("KXETH", (0, 0.0))
    if n >= MIN_N_FOR_DECISION and btc_n >= 5 and btc_pnl > 0 and eth_n >= 5 and eth_pnl <= 0:
        retire_triggers.append(("5", f"KXBTC-only +EV (${btc_pnl:.2f}) with KXETH dead (${eth_pnl:.2f})"))

    promote_conditions_met = (
        n >= MIN_N_FOR_DECISION
        and pnl_total > 0
        and days_pos >= 2
        and top1_pct <= SINGLE_EVENT_MAX_PCT
        and btc_n >= 1 and btc_pnl > 0
        and eth_n >= 1 and eth_pnl > 0
    )

    if retire_triggers:
        status_icon = "🚨"
        triggers_str = "; ".join(f"#{t[0]}" for t in retire_triggers)
        status = f"RETIRE ({triggers_str})"
    elif promote_conditions_met:
        status_icon = "✅"
        status = "PROMOTE-candidate"
    else:
        status_icon = "⏳"
        gap_to_n = max(0, MIN_N_FOR_DECISION - n)
        status = f"PASS (n→{MIN_N_FOR_DECISION} needs +{gap_to_n})"

    # Format output.
    lines = [
        f"📊 <b>{STRATEGY}</b> daily eval — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"{status_icon} <b>{status}</b>",
        "",
        f"<b>Aggregate:</b> n={n}  PnL=${pnl_total:.2f}  ROI={roi:+.2f}%",
        "",
        "<b>Per-asset:</b>",
    ]
    for a in sorted(by_asset.keys()):
        an, ap = by_asset[a]
        a_roi = (ap / (an * SIZE_USDC)) * 100 if an > 0 else 0
        lines.append(f"  {a}: n={an}  PnL=${ap:.2f}  ROI={a_roi:+.2f}%")

    if by_day:
        lines.append("")
        lines.append("<b>Per-day (last 7d):</b>")
        for d in sorted(by_day.keys()):
            dn, dp = by_day[d]
            lines.append(f"  {d}: n={dn}  ${dp:+.2f}")

    if top_events:
        lines.append("")
        lines.append("<b>Top events:</b>")
        for ek, ep in top_events:
            pct = (ep / gross_pos * 100) if gross_pos > 0 else 0
            lines.append(f"  {ek}: ${ep:+.2f} ({pct:+.1f}% of gross)")

    if cascade_events:
        lines.append("")
        lines.append(
            f"<b>Cascades (≥{CASCADE_MIN_FIRES} fires, "
            f"net ${cascade_net:+.2f} across {len(cascade_events)}):</b>"
        )
        for mid, pnls in sorted(
            cascade_events, key=lambda kv: sum(kv[1])
        )[:5]:
            tag = "🚨" if sum(pnls) <= 0 else "✓"
            lines.append(f"  {tag} {mid}: {len(pnls)}× net ${sum(pnls):+.2f}")

    if retire_triggers:
        lines.append("")
        lines.append("<b>Retire trigger detail:</b>")
        for tid, desc in retire_triggers:
            lines.append(f"  #{tid}: {desc}")

    lines.append("")
    lines.append(
        f"<i>Gates: n≥{MIN_N_FOR_DECISION}, top-event ≤{SINGLE_EVENT_MAX_PCT}%, "
        f"no cascade ≥{CASCADE_MIN_FIRES} net-loss, both KXBTC+KXETH +EV.</i>"
    )

    msg = "\n".join(lines)
    await _emit(config, msg, send)


async def _emit(config, msg: str, send: bool):
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    if not send:
        return
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        logger.warning("Telegram credentials missing — skipping send.")
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(msg, msg_type="phase1_eval")
    logger.info("Telegram sent")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                   help="Send Telegram (default: console-only dry print).")
    p.add_argument("--force", action="store_true",
                   help="Bypass SKIP_BEFORE gate (for dry-run during eval-rule iteration).")
    args = p.parse_args()
    if args.force:
        global SKIP_BEFORE
        SKIP_BEFORE = datetime(1970, 1, 1, tzinfo=timezone.utc)
    asyncio.run(eval_strategy(send=args.send))


if __name__ == "__main__":
    main()
