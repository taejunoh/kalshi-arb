"""W23 tripwire for sports_underdog_v31 (WNBA) — demote-vs-keep decision.

Built 2026-06-07 after the v31 live-book diagnosis ([[v31_live_book_diagnosis_2026-06-07]]).
underdog_v31's WNBA edge is razor-thin (lifetime WR 38.7% vs ~36.4% post-fee
breakeven, +2.3pp) and showed a clean 4-week WR decline (W19 57% → W20 50% →
W21 33% → W22 0%), the same signature as the v31 MLB early-season-inefficiency
close. The user chose KEEP-LIVE-with-size-frozen for ONE more week (W23) as a
confirmation test instead of an immediate demote. This script reads the W23
result and fires a Telegram verdict so the keep/demote call is made on data,
not vibes.

W23 window = entries Mon 2026-06-08 .. Sun 2026-06-14 (entry-date attribution,
consistent with the diagnosis weekly cut). LIVE = status='settled'.

Verdict logic:
  n < 5                              -> INSUFFICIENT  (extend watch to W24)
  WR < breakeven OR net PnL < 0      -> BREACHED      (DEMOTE to paper now)
  WR >= breakeven+5pp AND PnL > 0    -> RECOVERED     (keep LIVE, may lift freeze)
  else (marginal, at/just-above BE)  -> MARGINAL      (hold freeze, extend)

Read-only on the DB. Mirrors scripts/high_v1_progress_alert.py. Scheduled by
deploy/kalshi-underdog-w23-tripwire.timer (fires 2026-06-15 and -06-16 14:00
UTC, after the weekend W23 games settle).
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database

logger = logging.getLogger("underdog_v31_w23_tripwire")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STRATEGY = "sports_underdog_v31"
W23_START = "2026-06-08"   # Monday
W23_END = "2026-06-14"     # Sunday (inclusive)
MIN_N = 5                  # below this = insufficient sample
RECOVER_BUFFER = 0.05      # WR must clear breakeven by 5pp to call "recovered"


def _breakeven_wr(avg_price: float) -> float:
    """Post-fee breakeven WR for a YES buy at avg_price. Kalshi taker fee =
    0.07*p*(1-p)/contract; win profit=(1-p)-fee, loss=p+fee, denom=1.0, so
    breakeven WR = p + fee."""
    fee = 0.07 * avg_price * (1.0 - avg_price)
    return avg_price + fee


async def run():
    config = Config()
    db = Database()
    await db.initialize()

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT price, size, pnl FROM trades "
            "WHERE strategy = ? AND status = 'settled' "
            "AND market_id LIKE 'KXWNBA%' "
            "AND date(timestamp) >= ? AND date(timestamp) <= ? "
            "ORDER BY timestamp",
            (STRATEGY, W23_START, W23_END),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    pnl = sum(r["pnl"] for r in rows)
    avg_price = (sum(r["price"] for r in rows) / n) if n else 0.0
    wr = (wins / n) if n else 0.0
    be = _breakeven_wr(avg_price) if n else 0.364

    if n < MIN_N:
        verdict = "⚠️ INSUFFICIENT SAMPLE"
        action = f"n={n} (<{MIN_N}). Extend the watch to W24 — do not demote on this little data. Keep the size freeze on."
    elif wr < be or pnl < 0:
        verdict = "🔴 TRIPWIRE BREACHED → DEMOTE"
        action = (f"W23 WR {wr*100:.1f}% < breakeven {be*100:.1f}% (or net PnL ${pnl:+.2f} < 0). "
                  f"This is the 2nd straight sub-breakeven week → the WNBA underdog edge looks closed. "
                  f"DEMOTE: remove sports_underdog_v31 from LIVE_STRATEGIES (.env) and restart the bot. "
                  f"LIVE then = sports_longshot_v31 only.")
    elif wr >= be + RECOVER_BUFFER and pnl > 0:
        verdict = "🟢 RECOVERED → KEEP LIVE"
        action = (f"W23 WR {wr*100:.1f}% clears breakeven {be*100:.1f}% by 5pp+ and net ${pnl:+.2f}. "
                  f"W22 was variance, not closure. Keep LIVE; you may lift the size freeze "
                  f"(restore cap 5→30 in sports_underdog_v31._get_trade_size) if you want full sizing back.")
    else:
        verdict = "🟡 MARGINAL → HOLD FREEZE, EXTEND"
        action = (f"W23 WR {wr*100:.1f}% sits at/just above breakeven {be*100:.1f}% (net ${pnl:+.2f}). "
                  f"Inconclusive. Keep LIVE with the size freeze ON and re-check after W24.")

    lines = [
        "🎯 <b>underdog_v31 W23 tripwire</b>",
        f"<i>WNBA LIVE, entries {W23_START}..{W23_END}</i>",
        "",
        f"n={n} · wins={wins} · WR={wr*100:.1f}%",
        f"net PnL ${pnl:+.2f} · avg px {avg_price:.3f} · breakeven WR {be*100:.1f}%",
        "",
        f"<b>{verdict}</b>",
        action,
    ]
    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        from monitoring.notifier import TelegramNotifier
        notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        await notifier.send(msg, msg_type="underdog_v31_w23_tripwire")
        logger.info("Telegram sent")
    else:
        logger.warning("Telegram credentials missing")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
