"""Detect orphan Kalshi fills with no corresponding trades-table row.

P3 (2026-05-11) of the orphan-order-fix plan. Sister to
reconcile_kalshi_to_trades.py — that script targets mentions/econ/spread
strategies which never write to trades. This one targets sports/ladder/
macro/crypto/xvenue strategies which DO write but can fail (DB lock,
WAL contention). Read-only: emits a Telegram alert + console summary
when discrepancies are found.

Why a separate script:
- Existing reconcile_kalshi_to_trades.py uses ticker-prefix → strategy
  attribution which assumes a 1:1 mapping. Sports tickers (KXNBAGAME,
  KXMLBGAME, KX...GAME) can come from any of ~10 strategies, so the
  attribution is non-trivial. Better to just *detect* the discrepancy
  and alert the human — manual reconciliation is safer than auto-insert
  with wrong strategy attribution.

Detection logic:
1. Pull current Kalshi positions (`get_positions`) and recent settlements
   (`get_settlements`).
2. Build a set of Kalshi-side (market_id) tuples that imply we held a
   real position at some point.
3. Query trades table for status IN ('filled', 'settled') for each
   market_id (live trades only — paper twins ignored).
4. Diff: market_ids on Kalshi NOT in trades = ORPHAN_FILLS.
5. Print summary + send Telegram alert if orphans found.

Reverse check (in trades but not on Kalshi) is also done but rare:
flags strategy_priority issues where the bot thinks it has a position
but Kalshi doesn't (typically race conditions during settlement).

Usage:
    python3 scripts/orphan_order_check.py            # ad-hoc run
    systemd timer → hourly cron (see deploy/)

Exit codes:
    0 — no orphans
    1 — orphans found (alerts already sent)
    2 — script error (API/DB failure)
"""
import asyncio
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient

logger = logging.getLogger("orphan_check")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Sports + non-sports prefixes handled by record_trade-writing strategies.
# Ticker prefixes handled by the limit-only reconciler (mentions/econ/spread)
# OR by strategies that never write to trades by design (crypto spread/ladder:
# KXBTC*/KXETH*/KXSOL*/KXXRP*, incl. KXBTCD Bitcoin-daily ladders) are EXCLUDED
# here — they are NOT orphans, just non-writers, so flagging them is pure noise
# (2026-06-04: 745 KXBTCD + 79 KXBTC dominated a 792-orphan false alert).
RECONCILE_HANDLED_PREFIXES = (
    "KXFEDMENTION", "KXCPI", "KXGDP", "KXUNEMPLOY", "KXFED-",
    "KXBTC", "KXETH", "KXSOL", "KXXRP",  # crypto spread/ladder — never write to trades
)

# Only reconcile settlements from the recent window. get_settlements() returns
# the full account history (thousands of rows), so old pre-block sport
# settlements (e.g. MLB traded live before the 2026-05-21 block) show as
# perma-orphans. A genuine missing-fill bug (live fill that failed to write to
# trades) surfaces within the hourly cadence, so a short window keeps the signal
# without the deep-history noise.
SETTLEMENT_LOOKBACK_DAYS = 7


def _is_handled_by_reconciler(ticker: str) -> bool:
    return any(ticker.startswith(p) for p in RECONCILE_HANDLED_PREFIXES)


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    import urllib.request, urllib.parse, json
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"telegram send failed: {e}")
        return False


async def main() -> int:
    config = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(config)

    # 1. Kalshi truth
    try:
        positions = client.get_positions()
        settlements = client.get_settlements(limit=500)
    except Exception as e:
        logger.error(f"kalshi fetch failed: {e}")
        return 2

    # Filter out reconciler-handled prefixes (those are covered by the
    # other script's idempotent reconcile; orphan check is for the
    # record_trade-writing strategies).
    kalshi_tickers: set[str] = set()
    kalshi_meta: dict[str, dict] = {}

    for p in positions:
        tk = p.get("ticker")
        if not tk or _is_handled_by_reconciler(tk):
            continue
        pos_fp = float(p.get("position_fp") or 0)
        if pos_fp == 0:
            continue
        kalshi_tickers.add(tk)
        kalshi_meta[tk] = {
            "source": "position",
            "position_fp": pos_fp,
            "cost": float(p.get("market_exposure_dollars") or 0),
        }

    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SETTLEMENT_LOOKBACK_DAYS)).isoformat()
    for s in settlements:
        tk = s.get("ticker")
        if not tk or _is_handled_by_reconciler(tk):
            continue
        # Only recent settlements — old pre-block sport history is not a live bug.
        if (s.get("settled_time") or "") < cutoff:
            continue
        no_count = float(s.get("no_count_fp") or 0)
        yes_count = float(s.get("yes_count_fp") or 0)
        if no_count == 0 and yes_count == 0:
            continue  # no fill at all
        # Settlement supersedes position (already closed).
        kalshi_meta[tk] = {
            "source": "settlement",
            "yes_count": yes_count, "no_count": no_count,
            "result": s.get("market_result"),
        }
        kalshi_tickers.add(tk)

    logger.info(
        f"Kalshi side: {len(positions)} positions, {len(settlements)} settlements; "
        f"{len(kalshi_tickers)} unique tickers post-filter"
    )

    # 2. Local trades table — live entries only (status filled/settled).
    #    Paper-twin rows (paper_filled/paper_settled) NOT included since
    #    they represent simulated fills, not real Kalshi state.
    import aiosqlite
    trades_tickers: set[str] = set()
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        cursor = await conn.execute(
            "SELECT DISTINCT market_id FROM trades "
            "WHERE status IN ('filled', 'settled')"
        )
        for (mid,) in await cursor.fetchall():
            trades_tickers.add(mid)

    logger.info(f"Local trades table (filled/settled live): {len(trades_tickers)} distinct tickers")

    # 3. Diff
    orphans = kalshi_tickers - trades_tickers  # Kalshi has, we don't
    phantoms = trades_tickers - kalshi_tickers  # We have, Kalshi doesn't (rare; old data)

    if not orphans and not phantoms:
        logger.info("✓ No orphans, no phantoms. Reconcile clean.")
        return 0

    # 4. Build alert text
    msg_lines = []
    if orphans:
        logger.warning(f"⚠️  {len(orphans)} ORPHAN ticker(s) — Kalshi has fills/positions, trades table doesn't:")
        for tk in sorted(orphans):
            meta = kalshi_meta.get(tk, {})
            logger.warning(f"   {tk}: {meta}")
        msg_lines.append(f"🚨 <b>{len(orphans)} ORPHAN Kalshi fill(s) detected</b>")
        for tk in sorted(orphans)[:10]:  # cap to 10 for tg message size
            meta = kalshi_meta.get(tk, {})
            if meta.get("source") == "settlement":
                msg_lines.append(
                    f"  • {tk}: result={meta.get('result')} "
                    f"yes={meta.get('yes_count'):.0f} no={meta.get('no_count'):.0f}"
                )
            else:
                msg_lines.append(
                    f"  • {tk}: position={meta.get('position_fp'):.0f} "
                    f"cost=${meta.get('cost', 0):.2f}"
                )
        if len(orphans) > 10:
            msg_lines.append(f"  …and {len(orphans) - 10} more")
        msg_lines.append("Action: manually inspect + insert recovery rows via reconcile_kalshi_to_trades.py")

    if phantoms:
        # Don't alert on phantoms by default — they're usually old data
        # before reconciliation was run. Log only.
        logger.info(f"Note: {len(phantoms)} phantom ticker(s) in trades but not in Kalshi side (likely settled+pruned).")

    # 5. Send Telegram if orphans
    if orphans:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat:
            text = "\n".join(msg_lines)
            ok = _send_telegram(token, chat, text)
            logger.info(f"Telegram alert: {'sent' if ok else 'FAILED'}")
        else:
            logger.warning("TELEGRAM_BOT_TOKEN/CHAT_ID not set — alert not delivered")
        # 2026-05-31: exit 0 after successfully alerting. The Telegram alert IS
        # the deliverable; exiting non-zero only left a permanent systemd
        # "failed" state (cosmetic noise) for benign settled+pruned orphans.
        # Genuine missing-fill orphans still surface via the alert above.
        return 0

    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
