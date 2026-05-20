"""One-shot verifier for the tennis tuple URL bug fix (commit 33a9664).

Pre-fix, every tennis CLV snapshot since 2026-05-18 had NULL
us_consensus_prob / eu_consensus_prob / pinnacle_age_s because
_fetch_odds(tuple) returned [] and the dual-region lookup had no game
to match. Post-fix (deployed 2026-05-20 00:04:45 UTC, commit 33a9664),
the dispatch site passes a per-game sport_key string instead.

This script fires once via systemd timer ~12h post-deploy and Telegrams
a VERIFIED / STILL BROKEN / INCONCLUSIVE verdict based on the post-
restart tennis CLV row population.

Memory: tennis_tuple_url_bug_2026-05-19.md
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import subprocess
import sys

# systemd runs us as `python scripts/foo.py` which only puts scripts/ on
# sys.path, not the repo root — so `from core.config import Config` fails.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tennis_clv_fix_verify")

# Anchor: prod bot restart on commit 33a9664 (the fix deploy).
RESTART_ISO = "2026-05-20 00:04:45"

# Verdict thresholds.
MIN_N_FOR_VERDICT = 3        # below this, declare INCONCLUSIVE
NON_NULL_FRACTION_TO_PASS = 0.80


def _query_post_restart_tennis_clv(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT cs.snapshot_at, cs.snapshot_time, t.market_id, t.strategy,
                  cs.us_consensus_prob, cs.eu_consensus_prob, cs.pinnacle_age_s
           FROM clv_snapshots cs JOIN trades t ON t.id = cs.trade_id
           WHERE (t.market_id LIKE 'KXATPMATCH%' OR t.market_id LIKE 'KXWTAMATCH%')
             AND cs.snapshot_time >= ?
           ORDER BY cs.snapshot_time DESC""",
        (RESTART_ISO,),
    ).fetchall()
    cols = ["snapshot_at", "snapshot_time", "market_id", "strategy",
            "us_consensus_prob", "eu_consensus_prob", "pinnacle_age_s"]
    return [dict(zip(cols, r)) for r in rows]


def _count_tuple_url_404s() -> int | None:
    """Count tuple-stringified Odds API URL errors in journal since restart.
    Returns None if journalctl isn't available (e.g. local dry-run on dev)."""
    try:
        out = subprocess.run(
            ["sudo", "journalctl", "-u", "kalshi-bot",
             "--since", RESTART_ISO, "--no-pager"],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return sum(1 for line in out.stdout.splitlines() if "('tennis_" in line)


def _classify(rows: list[dict], tuple_404s: int | None) -> tuple[str, str]:
    """Return (verdict, one-line explanation)."""
    n = len(rows)
    if n < MIN_N_FOR_VERDICT:
        return ("INCONCLUSIVE",
                f"only {n} post-restart tennis CLV rows (<{MIN_N_FOR_VERDICT}); "
                f"recheck in another 12h")
    n_us = sum(1 for r in rows if r["us_consensus_prob"] is not None)
    n_eu = sum(1 for r in rows if r["eu_consensus_prob"] is not None)
    frac_us = n_us / n
    frac_eu = n_eu / n
    tuple_msg = (f", tuple-URL 404s={tuple_404s}" if tuple_404s is not None else "")
    if frac_us >= NON_NULL_FRACTION_TO_PASS and frac_eu >= NON_NULL_FRACTION_TO_PASS:
        return ("VERIFIED",
                f"{n_us}/{n} us, {n_eu}/{n} eu non-NULL "
                f"(pre-fix baseline: 0% non-NULL){tuple_msg}")
    return ("STILL BROKEN",
            f"{n_us}/{n} us, {n_eu}/{n} eu non-NULL — fix did not take effect"
            f"{tuple_msg}; verify commit 33a9664 is HEAD on host")


def _format_report(rows: list[dict], tuple_404s: int | None,
                   verdict: str, explanation: str) -> str:
    lines = [
        f"🎾 <b>Tennis tuple URL bug — 12h post-deploy verdict</b>",
        f"",
        f"<b>Verdict: {verdict}</b>",
        f"{explanation}",
        f"",
        f"Deploy: commit 33a9664, 2026-05-20 00:04:45 UTC",
        f"Pre-fix CLV NULL rate: 24/24 = 100% (5/18 → 5/19)",
        f"Post-restart tennis CLV rows: {len(rows)}",
    ]
    if tuple_404s is not None:
        lines.append(f"Journal tuple-URL 404 count: {tuple_404s} (target: 0)")
    if rows:
        lines.append("")
        lines.append("<b>Sample rows (newest first):</b>")
        for r in rows[:5]:
            us = r["us_consensus_prob"]
            eu = r["eu_consensus_prob"]
            age = r["pinnacle_age_s"]
            us_s = f"{us:.3f}" if us is not None else "NULL"
            eu_s = f"{eu:.3f}" if eu is not None else "NULL"
            age_s = f"{age}s" if age is not None else "NULL"
            lines.append(
                f"  {r['market_id'][:30]} {r['strategy'][:24]} "
                f"us={us_s} eu={eu_s} age={age_s}"
            )
    if verdict == "STILL BROKEN":
        lines.append("")
        lines.append("Suggested next step: ssh to host, "
                     "<code>cd ~/kalshi-arb &amp;&amp; git log -1 --oneline</code>; "
                     "confirm HEAD == 33a9664, restart kalshi-bot if not.")
    return "\n".join(lines)


async def _send_telegram(report: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing to stdout instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await notifier.send(report, msg_type="tennis_clv_fix_verify")
    logger.info(f"Sent Telegram message, {len(report)} chars.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        rows = _query_post_restart_tennis_clv(conn)
    finally:
        conn.close()

    tuple_404s = _count_tuple_url_404s()
    verdict, explanation = _classify(rows, tuple_404s)
    report = _format_report(rows, tuple_404s, verdict, explanation)

    if args.dry_run:
        print(report)
        return
    asyncio.run(_send_telegram(report))


if __name__ == "__main__":
    main()
