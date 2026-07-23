"""One-shot week-1 report for the matched_game mismatch instrumentation.

Fires once ~7 days after the 2026-07-22T22:08:49Z instrumentation deploy.
Two questions, one Telegram message:

  (1) matched_game phase-1 measurement — does the observed over-cap rate
      match the pre-deploy prediction? If within +/-10pp, the instrumentation
      is trustworthy and the stage-2 skip switch is justified. The
      per-strategy real-money numbers (esp. mlb_pitcher_edge_v1/v2_1,
      predicted 55-60%) are the deciding figures.
  (2) pin_follow clean ledger — how many GATE-0-clean (correctly matched)
      paper fires has sports_mlb_live_pin_follow_v1 accumulated since the
      deploy, with WR / PnL. Feeds the stage-3 promotion decision.

Read-only on bot.db. Sends Telegram (or --dry-run to stdout).

Usage:
    venv/bin/python scripts/match_delta_week1_report.py --dry-run
    venv/bin/python scripts/match_delta_week1_report.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("match_delta_week1")

# Instrumentation deploy time — the era boundary. Only rows at/after this are
# measured (older rows have NULL match_delta_s).
CUTOFF = "2026-07-22T22:08:49"

# HHMM-precise series get a 6h cap; date-only series get 20h (anchor slop).
PRECISE_SERIES = ("KXMLBGAME", "KXNPBGAME", "KXKBOGAME")
CAP_PRECISE = 6 * 3600
CAP_DATE_ONLY = 20 * 3600

# Pre-deploy predictions from the 2026-07-22 investigation (over-cap %).
PRED_SERIES = {
    "KXMLBGAME": 32.8, "KXNBAGAME": 54.7, "KXATPMATCH": 25.2,
    "KXWNBAGAME": 4.2, "KXWTAMATCH": 24.8, "KXNHLGAME": 19.8,
    "KXNPBGAME": 41.2, "KXMLSGAME": 0.0, "KXUFCFIGHT": 0.0, "KXKBOGAME": 33.3,
}
PRED_STRATEGY = {
    "sports_underdog_v31": 34.0, "sports_longshot_v31": 25.0,
    "sports_mlb_pitcher_edge_v1": 60.5, "sports_mlb_pitcher_edge_v2_1": 55.0,
    "sports_tennis_underdog_v1": 18.2, "sports_tennis_live_fade_v1": 11.6,
}
LIVE_STRATS = tuple(PRED_STRATEGY.keys())


def _cap_expr(col_series: str) -> str:
    lst = ",".join(f"'{s}'" for s in PRECISE_SERIES)
    return (f"CASE WHEN {col_series} IN ({lst}) THEN {CAP_PRECISE} "
            f"ELSE {CAP_DATE_ONLY} END")


def _fmt_delta(observed: float, pred: float | None) -> str:
    if pred is None:
        return f"{observed:5.1f}%   (예측 없음)"
    gap = observed - pred
    flag = "✅" if abs(gap) <= 10 else "⚠️"
    return f"{observed:5.1f}%  vs 예측 {pred:4.1f}%  ({gap:+5.1f}pp) {flag}"


def _build_report(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"📐 matched_game 계량 1주차 리포트  ({now})",
         f"계량 시작(컷오프): {CUTOFF}Z", ""]

    # (1a) observer full-population, by series
    L.append("━━ (1) 오매칭 over-cap 비율 ━━")
    L.append("· odds_observations (전수 모집단), series별:")
    cap = _cap_expr("series")
    cur.execute(
        f"""SELECT series, COUNT(*) n,
                   SUM(match_delta_s IS NOT NULL) has_d,
                   SUM(match_delta_s IS NOT NULL AND ABS(match_delta_s) > {cap}) over_cap
            FROM odds_observations
            WHERE timestamp >= ?
            GROUP BY series HAVING n >= 10 ORDER BY n DESC""",
        (CUTOFF,),
    )
    rows = cur.fetchall()
    if not rows:
        L.append("  (데이터 없음 — 배포 후 관측 미기록?)")
    for series, n, has_d, over in rows:
        pct = 100.0 * (over or 0) / has_d if has_d else 0.0
        L.append(f"  {series:12s} n={n:4d}  " + _fmt_delta(pct, PRED_SERIES.get(series)))

    # (1b) real-money ledger, by LIVE strategy
    L.append("")
    L.append("· trades 실거래(settled/filled), LIVE 전략별:")
    ph = ",".join("?" * len(LIVE_STRATS))
    cur.execute(
        f"""SELECT strategy, COUNT(*) n,
                   SUM(match_delta_s IS NOT NULL) has_d,
                   SUM(match_delta_s IS NOT NULL AND ABS(match_delta_s) > 21600) over_cap
            FROM trades
            WHERE timestamp >= ? AND status IN ('settled','filled')
              AND strategy IN ({ph})
            GROUP BY strategy ORDER BY n DESC""",
        (CUTOFF, *LIVE_STRATS),
    )
    rows = cur.fetchall()
    if not rows:
        L.append("  (실거래 발동 아직 0건 — 표본 부족, 판단 보류)")
    total_n = 0
    total_over = 0
    for strat, n, has_d, over in rows:
        total_n += has_d or 0
        total_over += over or 0
        pct = 100.0 * (over or 0) / has_d if has_d else 0.0
        short = strat.replace("sports_", "")
        L.append(f"  {short:22s} n={n:3d}  " + _fmt_delta(pct, PRED_STRATEGY.get(strat)))
    if total_n:
        pooled = 100.0 * total_over / total_n
        L.append(f"  {'→ LIVE 6개 합산':22s} n={total_n:3d}  " + _fmt_delta(pooled, 32.5))

    # (2) pin_follow clean ledger — GATE-0-clean (correctly matched) paper fires
    L.append("")
    L.append("━━ (2) pin_follow 클린 원장 (3번용) ━━")
    cur.execute(
        """SELECT COUNT(*) n,
                  ROUND(SUM(pnl),2) net,
                  ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) wr
           FROM trades
           WHERE strategy='sports_mlb_live_pin_follow_v1'
             AND status='paper_settled' AND timestamp >= ?
             AND match_delta_s IS NOT NULL AND ABS(match_delta_s) <= 21600""",
        (CUTOFF,),
    )
    n, net, wr = cur.fetchone()
    n = n or 0
    if n == 0:
        L.append("  클린 발동 0건 (아직 새 원장 미축적)")
    else:
        L.append(f"  클린(정상매칭) 발동 n={n}  WR {wr}%  PnL ${net}")
        # how many were dropped as mis-matched, for context
        cur.execute(
            """SELECT COUNT(*) FROM trades
               WHERE strategy='sports_mlb_live_pin_follow_v1'
                 AND status='paper_settled' AND timestamp >= ?
                 AND match_delta_s IS NOT NULL AND ABS(match_delta_s) > 21600""",
            (CUTOFF,),
        )
        dropped = cur.fetchone()[0]
        L.append(f"  (오매칭으로 제외된 발동: {dropped}건)")
    L.append(f"  → 승격바 재설계는 클린 n이 충분히 쌓인 뒤 (memory 참조)")

    # decision hint
    L.append("")
    L.append("━━ 판단 ━━")
    L.append("· ✅=예측 ±10pp 이내 → 계량 신뢰, 2단계(스킵 전환) 정당")
    L.append("· ⚠️ 다수 → 계량기 자체 재검토 먼저")
    L.append("· ⚠️ 2단계 전 4월 revert(e6a5019) 이유 규명 필수")

    conn.close()
    return "\n".join(L)


async def _send(report: str):
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing to stdout instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    MAX = 3800
    parts = [report[i:i + MAX] for i in range(0, len(report), MAX)]
    for i, part in enumerate(parts):
        await notifier.send(part, msg_type=f"match_delta_week1_{i}")
    logger.info(f"Sent {len(parts)} Telegram message(s), {len(report)} chars.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/bot.db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)
    report = _build_report(args.db)
    if args.dry_run:
        print(report)
    else:
        asyncio.run(_send(report))


if __name__ == "__main__":
    main()
