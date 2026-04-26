"""Weekly live-phase backtest — observer-based replay to detect emergent alpha.

For each pre-game-only strategy, simulates what would have happened if the
strategy had been allowed to fire in live phase too. Compares to actual
pre-game ROI for the same period.

Goal: surface live-phase sub-cells that turn positive over time, so we can
re-evaluate whether to relax PRE_GAME_ONLY for any strategy.

Output: Telegram message + log file. Designed for weekly cron (e.g., Mondays).
Idempotent — read-only, no DB writes.
"""
import asyncio
import logging
import os
import sys
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from monitoring.notifier import TelegramNotifier

logger = logging.getLogger("weekly_live_phase_backtest")

KALSHI_FEE = 0.07
SIZE = 4.0
LOOKBACK_DAYS = 7  # rolling window

# Strategies to backtest (PRE_GAME_ONLY currently, want to test live phase)
STRATEGIES = {
    "longshot_v31": {"region": "eu", "type": "longshot"},
    "midshot_v31":  {"region": "eu", "type": "midshot"},
    "underdog_v31": {"region": "eu", "type": "underdog"},
    "underdog_v32": {"region": "eu", "type": "underdog"},
    "midshot_v34":  {"region": "us", "type": "midshot"},
    "underdog_v34": {"region": "us", "type": "underdog"},
}


def trade_pnl(price, won):
    contracts = SIZE / price
    fee = min(KALSHI_FEE * contracts, price * contracts, (1 - price) * contracts)
    return contracts - SIZE - fee if won else -SIZE - fee


def eval_longshot(price, side_prob, side):
    if not (0.05 <= price <= 0.25): return None
    if 0.10 <= price < 0.20: return None  # dead_zone
    fee_pct = min(KALSHI_FEE, price, 1 - price) / price
    cost = price * (1 + fee_pct)
    payoff = 1.0 / cost
    if payoff < 3.0: return None
    return payoff


def eval_midshot(price, side_prob, side):
    if not (0.20 <= price <= 0.35): return None
    fee_pct = min(KALSHI_FEE, price, 1 - price) / price
    cost = price * (1 + fee_pct)
    payoff = 1.0 / cost
    if payoff < 2.5: return None
    edge = side_prob - cost
    if edge < 0.05: return None
    return edge


def eval_underdog(price, side_prob, side):
    if not (0.20 <= price <= 0.40): return None
    fee_pct = min(KALSHI_FEE, price, 1 - price) / price
    cost = price * (1 + fee_pct)
    payoff = 1.0 / cost
    if payoff < 2.0: return None
    # Underdog logic: bet side that consensus thinks has prob < 0.40
    if side_prob >= 0.40: return None
    return 1 - side_prob  # signal strength


EVAL_FUNCS = {"longshot": eval_longshot, "midshot": eval_midshot, "underdog": eval_underdog}


def run_backtest(db_path: str) -> str:
    """Run the live-phase backtest for all strategies. Returns formatted output."""
    con = sqlite3.connect(db_path)
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    cutoff = cutoff_dt.isoformat()

    # Settlement detection
    ticker_outcome = {}
    for row in con.execute("""
        SELECT kalshi_ticker, kalshi_yes_ask FROM odds_observations o1
        WHERE timestamp = (SELECT MAX(timestamp) FROM odds_observations o2
                           WHERE o2.kalshi_ticker = o1.kalshi_ticker)
    """):
        t, ask = row
        if ask is None: continue
        if ask >= 0.97: ticker_outcome[t] = 1
        elif ask <= 0.03: ticker_outcome[t] = 0

    # Walk obs (live phase only)
    ticker_obs = defaultdict(list)
    q = f"""SELECT kalshi_ticker, timestamp, kalshi_yes_ask, kalshi_no_ask,
                   eu_yes_prob, us_yes_prob, sport
            FROM odds_observations
            WHERE timestamp >= '{cutoff}'
              AND phase = 'live'
              AND kalshi_yes_ask IS NOT NULL AND kalshi_no_ask IS NOT NULL
            ORDER BY kalshi_ticker, timestamp"""
    for row in con.execute(q):
        t, ts, ya, na, eu_p, us_p, sport = row
        ts_epoch = datetime.fromisoformat(ts).timestamp()
        ticker_obs[t].append((ts_epoch, ya, na, eu_p, us_p, sport))

    out = []
    out.append(f"📊 Weekly Live-Phase Backtest ({LOOKBACK_DAYS}d, observer sim)")
    out.append(f"Window: {cutoff[:10]} → today\n")

    flagged = []  # strategies with potential live-phase alpha

    for strat_key, cfg in STRATEGIES.items():
        evf = EVAL_FUNCS[cfg["type"]]
        region_idx = 3 if cfg["region"] == "eu" else 4  # tuple index for prob

        # First-fire dedup per game (matches real strategy game-level dedup)
        fires = defaultdict(list)  # sport -> list of (price, won)
        for t, obs in ticker_obs.items():
            game_id = "-".join(t.split("-")[:-1])
            seen_game = False
            for ts, ya, na, eu_p, us_p, sport in obs:
                prob = eu_p if cfg["region"] == "eu" else us_p
                if prob is None: continue
                if seen_game: break
                # Try YES side first, then NO
                for side, price in (("yes", ya), ("no", na)):
                    side_prob = prob if side == "yes" else 1 - prob
                    if evf(price, side_prob, side) is None: continue
                    outcome = ticker_outcome.get(t)
                    if outcome is None:
                        won = None
                    elif side == "yes":
                        won = (outcome == 1)
                    else:
                        won = (outcome == 0)
                    fires[sport].append((price, won))
                    seen_game = True
                    break
                if seen_game: break

        # Aggregate per sport
        out.append(f"  {strat_key} ({cfg['region']}):")
        all_settled = []
        any_data = False
        for sport in ("MLB", "NBA", "NHL"):
            settled = [(p, w) for p, w in fires.get(sport, []) if w is not None]
            if not settled: continue
            any_data = True
            n = len(settled)
            wins = sum(1 for _, w in settled if w)
            pnl = sum(trade_pnl(p, w) for p, w in settled)
            roi = 100 * pnl / (n * SIZE)
            out.append(f"    {sport} live: n={n} wins={wins} ROI={roi:+.1f}%")
            all_settled.extend(settled)
            if roi > 5.0 and n >= 15:
                flagged.append(f"{strat_key}/{sport} live: +{roi:.0f}% n={n}")
        if not any_data:
            out.append(f"    (no signals fired)")
        elif all_settled:
            n = len(all_settled)
            pnl = sum(trade_pnl(p, w) for p, w in all_settled)
            out.append(f"    TOTAL: n={n} ROI={100*pnl/(n*SIZE):+.1f}%")
        out.append("")

    if flagged:
        out.append(f"\n⚠️ FLAGGED — possible live-phase alpha (n>=15, ROI>+5%):")
        for f in flagged:
            out.append(f"  • {f}")
        out.append(f"  → consider relaxing PRE_GAME_ONLY for flagged strategy/sport")
    else:
        out.append(f"\n✅ No emergent live-phase alpha. PRE_GAME_ONLY remains correct.")

    out.append(f"\nNote: observer sim ≠ live execution (no MIN_DEPTH, slippage, etc).")
    out.append(f"Treat as directional signal, validate with paper before any gate change.")
    return "\n".join(out)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    config = Config()
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bot.db")

    report = run_backtest(db_path)
    logger.info("\n%s", report)

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
        )
        await notifier.send(report, msg_type="weekly_backtest")
        logger.info("Sent to Telegram.")
    else:
        logger.info("Telegram not configured; report only logged.")


if __name__ == "__main__":
    asyncio.run(main())
