"""CLV-gate alert — Telegram alert when rolling CLV crosses demote/collapse thresholds.

Built 2026-05-25 after by-band audit (see [[by-band-audit-2026-05-25]]). CLV
(Closing Line Value) is a clean leading indicator of realized PnL ON THE
DOWNSIDE only:

  CLV ≤ −0.04 → 4/4 losers (realized −$72 to −$638 / 30d, all demoted)

Surfaces two actionable, validated signals:

  DEMOTE_RISK         LIVE  + 14d n≥20 + 14d CLV ≤ −0.05
  CLV_COLLAPSE        LIVE  + 30d CLV ≥ +0.05 BUT 7d CLV ≤ −0.05  (alpha decay)

PROMOTE arm REMOVED 2026-05-29: +CLV has NO predictive validity for realized
PnL on the upside. Verified across all strategies with CLV data: of those with
30d CLV ≥ +0.10, soccer_underdog_v1 was −$51 (WR 29%) and underdog_v37 +$8,
while the biggest winner midshot_v31 (+$311) was not even flagged (CLV +0.097).
The arm also structurally surfaced only demoted (ex-LIVE) strategies, because
its query requires real settled/filled trades. Only the −CLV direction holds.
See [[clv_entry_gate_finding_2026-05-26]].

CLV is computed per-trade as (closing_price - entry_price) on the direction
side, joining clv_snapshots (snapshot_at='entry' vs 'closing') with trades.

Run modes:
  (default)  Print only (dry-test)
  --send     Send Telegram (used by systemd timer)

Read-only on DB. Idempotent.
"""
import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

logger = logging.getLogger("clv_gate_alert")

DEMOTE_CLV_MAX = -0.05
DEMOTE_N_MIN_14D = 20
COLLAPSE_30D_CLV_MIN = 0.05
COLLAPSE_7D_CLV_MAX = -0.05
COLLAPSE_N_MIN_7D = 8


def compute_clv_window(cursor, strategy: str, days: int) -> tuple[int, float] | None:
    """Return (n, avg_clv) for trades in last `days` days, or None if no data.

    NOTE: datetime(t.timestamp) wrap is required — stored timestamps are ISO-T
    (2026-05-28T04:22:25+00:00) while datetime('now',...) is space-separated;
    a raw TEXT compare ("T" > " ") over-includes the cutoff date.
    See [[feedback-sqlite-timestamp-t-space-compare]].
    """
    cursor.execute(
        """
        SELECT t.direction,
               e.yes_ask AS e_yes, e.no_ask AS e_no,
               c.yes_ask AS c_yes, c.no_ask AS c_no
        FROM trades t
        JOIN clv_snapshots e ON e.trade_id = t.id AND e.snapshot_at = 'entry'
        JOIN clv_snapshots c ON c.trade_id = t.id AND c.snapshot_at = 'closing'
        WHERE t.strategy = ?
          AND t.status IN ('settled', 'filled')
          AND datetime(t.timestamp) >= datetime('now', ?)
          AND e.yes_ask IS NOT NULL AND e.no_ask IS NOT NULL
          AND c.yes_ask IS NOT NULL AND c.no_ask IS NOT NULL
        """,
        (strategy, f"-{days} days"),
    )
    rows = cursor.fetchall()
    if not rows:
        return None
    clvs = []
    for direction, e_yes, e_no, c_yes, c_no in rows:
        if direction == "yes":
            clvs.append(c_yes - e_yes)
        else:
            clvs.append(c_no - e_no)
    if not clvs:
        return None
    return len(clvs), sum(clvs) / len(clvs)


def list_strategies_with_clv(cursor, lookback_days: int = 30) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT t.strategy
        FROM trades t
        JOIN clv_snapshots c ON c.trade_id = t.id
        WHERE datetime(t.timestamp) >= datetime('now', ?)
          AND t.strategy LIKE 'sports_%'
        """,
        (f"-{lookback_days} days",),
    )
    return [row[0] for row in cursor.fetchall()]


async def audit(send: bool = False):
    config = Config()
    db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
    live_strategies = config.LIVE_STRATEGIES if hasattr(config, "LIVE_STRATEGIES") else set()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    strategies = list_strategies_with_clv(cursor)

    demote_risk = []
    collapse = []
    ok = []

    for s in strategies:
        c30 = compute_clv_window(cursor, s, 30)
        c14 = compute_clv_window(cursor, s, 14)
        c7 = compute_clv_window(cursor, s, 7)
        is_live = s in live_strategies

        # PROMOTE arm removed 2026-05-29 (see module docstring): +CLV does not
        # predict +PnL. Only the −CLV demote/collapse directions are actionable.
        if is_live and c14 and c14[0] >= DEMOTE_N_MIN_14D and c14[1] <= DEMOTE_CLV_MAX:
            demote_risk.append((s, c14[0], c14[1]))
        elif (is_live and c30 and c30[1] >= COLLAPSE_30D_CLV_MIN
              and c7 and c7[0] >= COLLAPSE_N_MIN_7D and c7[1] <= COLLAPSE_7D_CLV_MAX):
            collapse.append((s, c30[1], c7[1], c7[0]))
        else:
            ok.append((s, c30[1] if c30 else None, c30[0] if c30 else 0, is_live))

    conn.close()

    lines = [f"<b>📊 CLV-Gate Alert</b> ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"]
    lines.append("")
    if collapse:
        lines.append(f"<b>🟡 CLV COLLAPSE</b> (LIVE, 30d +CLV but 7d ≤ {COLLAPSE_7D_CLV_MAX:.2f}):")
        for s, clv30, clv7, n7 in sorted(collapse, key=lambda x: x[2]):
            lines.append(f"  • <b>{s}</b>  30d=+{clv30:.3f}  7d={clv7:+.3f} (n={n7})")
        lines.append("")
    if demote_risk:
        lines.append(f"<b>🔴 DEMOTE RISK</b> (LIVE, 14d CLV ≤ {DEMOTE_CLV_MAX:.2f}, n ≥ {DEMOTE_N_MIN_14D}):")
        for s, n, clv in sorted(demote_risk, key=lambda x: x[2]):
            lines.append(f"  • <b>{s}</b>  n={n}  14d CLV={clv:+.3f}")
        lines.append("")

    if not (demote_risk or collapse):
        lines.append("<b>✅ No CLV-gate flags</b>")
        lines.append("")

    lines.append(f"<i>OK: {len(ok)} strategies — see clv_gate_alert.py thresholds.</i>")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    if not send:
        return

    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        logger.warning("Telegram credentials missing — skipping send.")
        return

    if not (demote_risk or collapse):
        logger.info("No CLV flags — skipping Telegram send.")
        return

    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(msg, msg_type="clv_gate")
    logger.info(
        f"Telegram sent: {len(collapse)} collapse / {len(demote_risk)} demote / {len(ok)} OK"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true", help="Send Telegram.")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(audit(send=args.send))


if __name__ == "__main__":
    main()
