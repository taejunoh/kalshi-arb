"""Sport-promotion eligibility evaluator.

Run anytime to get a PROMOTE / HOLD / KILL recommendation per OBSERVATION_ONLY
sport, based on observer + settlement data accumulated so far.

Decision criteria (all must pass for PROMOTE):
- n_settled_games >= 25 (game-level dedup)
- simulated ROI > +3% in the top edge bucket (matches existing MIN_EDGE)
- n_books_eu p50 >= 8 (consensus quality — same gate as MIN_N_BOOKS_FOR_SIM)
- Kalshi OI median > $500 (live tradability)

NOTE on Pinnacle: live strategies (longshot/midshot/underdog v3.x) do NOT
require Pinnacle — they trade against eu sharp-book CONSENSUS (Pinnacle is
one of ~15 books in eu, not the anchor). MLB runs profitably at 50% Pinnacle
coverage. Pinnacle % is reported here for context only, not gated.

Bands (for ROI bucketing):
  edge = eu_yes_prob - kalshi_yes_ask  (positive = Kalshi YES underpriced
  vs eu sharp-book consensus, our anchor for live trading)
  buckets: <0, 0-5%, 5-10%, 10-20%, 20%+

ROI sim:
  ENTRY: kalshi_yes_ask snapshot at first observation matching:
    - phase = 'pre' (pre-game)
    - minutes_to_start in [60, 1440]  (1h to 24h before kickoff)
    - n_books_eu >= MIN_N_BOOKS_FOR_SIM (default 8)
  SETTLEMENT: ticker_settlements.result == 'yes'/'no'
  PnL per $1 entry:
    win:  (1 - entry_price) - kalshi_fee
    lose: -entry_price - kalshi_fee
    where kalshi_fee = 0.07 * min(price, 1-price)

Usage:
    venv/bin/python scripts/promotion_eval.py
    venv/bin/python scripts/promotion_eval.py --sport EPL
    venv/bin/python scripts/promotion_eval.py --dry-run     # no Telegram
    venv/bin/python scripts/promotion_eval.py --window 7    # last N days
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("promotion_eval")

# Sport-promotion thresholds (single source of truth — keep in sync with
# memory/decision_thresholds.md). Tuned against live-strategy reality:
#   - n_games=25: matches v36_playoff_strategy promotion gate
#   - top_bucket_roi=3%: matches existing live MIN_EDGE in v3.x evaluate()
#   - n_books_p50=8: same as MIN_N_BOOKS_FOR_SIM (no double-gate)
#   - oi_median=$500: live tradability floor (below = partial-fill bleed)
# Pinnacle % deliberately NOT gated — live strategies trade against eu
# consensus, not Pinnacle directly. MLB runs at 50% Pinnacle coverage.
PROMOTE_CRITERIA = {
    "min_settled_games": 25,
    "min_top_bucket_roi": 0.03,
    "min_n_books_p50": 8,
    "min_kalshi_oi_median": 500.0,
}
KILL_CRITERIA = {
    "max_overall_roi": -0.10,          # -10% across all entries = abandon
    "max_failed_criteria": 2,           # 2+ thresholds missed = KILL
}

# Sim parameters
KALSHI_FEE_PCT = 0.07
MIN_N_BOOKS_FOR_SIM = 8     # filter thin-coverage entries
PRE_WINDOW_MIN_MINS = 60    # T-1h
PRE_WINDOW_MAX_MINS = 1440  # T-24h

# Sports we care about (only OBSERVATION_ONLY)
TARGET_SPORTS = ["NPB", "KBO", "MLS", "EPL", "LAL", "UCL", "BUN"]

# Edge buckets (in cents)
EDGE_BUCKETS = [(-1.0, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)]


def _bucket_label(lo, hi):
    if lo < 0:
        return "<0%"
    return f"{lo*100:.0f}-{hi*100:.0f}%"


def _sim_pnl(entry_price: float, won: bool) -> float:
    """PnL per $1 entry. Kalshi fee = 0.07 × min(price, 1-price)."""
    fee = KALSHI_FEE_PCT * min(entry_price, 1.0 - entry_price)
    if won:
        return (1.0 - entry_price) - fee
    return -entry_price - fee


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = min(int(len(s) * p), len(s) - 1)
    return s[k]


def _load_entries(conn: sqlite3.Connection, sport: str, window_days: int) -> list[dict]:
    """Pick the FIRST observer row per ticker that satisfies the entry filter.
    Returns list of dicts with entry context + settlement outcome.

    Entry filter mirrors what live strategies actually need:
      - eu consensus available (eu_yes_prob NOT NULL)
      - n_books_eu >= MIN_N_BOOKS_FOR_SIM (consensus quality)
      - phase=pre, T-1h to T-24h
    Pinnacle presence NOT required — eu consensus is the live anchor.
    """
    cursor = conn.execute(
        f"""
        SELECT o.kalshi_ticker, o.timestamp, o.kalshi_yes_ask,
               o.pinnacle_yes_prob, o.eu_yes_prob, o.us_yes_prob,
               o.is_yes_home, o.is_yes_draw,
               o.n_books_eu, o.has_pinnacle, o.kalshi_open_interest,
               o.minutes_to_start, o.phase,
               s.result
        FROM odds_observations o
        JOIN ticker_settlements s ON o.kalshi_ticker = s.kalshi_ticker
        WHERE o.sport = ?
          AND o.timestamp >= datetime('now', '-{int(window_days)} days')
          AND o.n_books_eu >= ?
          AND o.minutes_to_start BETWEEN ? AND ?
          AND o.phase = 'pre'
          AND o.kalshi_yes_ask IS NOT NULL
          AND o.eu_yes_prob IS NOT NULL
          AND s.result IN ('yes', 'no')
        ORDER BY o.kalshi_ticker, o.timestamp
        """,
        (sport, MIN_N_BOOKS_FOR_SIM, PRE_WINDOW_MIN_MINS, PRE_WINDOW_MAX_MINS),
    )
    seen_tickers = set()
    entries = []
    for r in cursor.fetchall():
        ticker = r[0]
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        entries.append({
            "ticker": ticker,
            "ts": r[1],
            "yes_ask": r[2],
            "pin_prob": r[3],
            "eu_prob": r[4],
            "us_prob": r[5],
            "is_yes_home": r[6],
            "is_yes_draw": r[7],
            "n_books_eu": r[8],
            "has_pinnacle": r[9],
            "open_interest": r[10] or 0,
            "minutes_to_start": r[11],
            "result": r[13],
        })
    return entries


def _load_quality_stats(conn: sqlite3.Connection, sport: str, window_days: int) -> dict:
    """Health stats — n_books p50, Pinnacle %, OI median, leg balance.

    OI median uses ENTRY-WINDOW rows only (T-1h to T-24h pre-game). Why:
    Kalshi seeds OI as games approach — far-future rows have OI ~$0-10
    which is irrelevant to "would we have liquidity at the point we'd
    enter?" Without this filter, MLS/EPL OI median collapses from $1k+
    to <$10 as the far-future long-tail dominates.
    """
    cursor = conn.execute(
        f"""
        SELECT n_books_eu, has_pinnacle, kalshi_open_interest,
               is_yes_home, is_yes_draw, minutes_to_start
        FROM odds_observations
        WHERE sport = ?
          AND timestamp >= datetime('now', '-{int(window_days)} days')
          AND n_books_eu IS NOT NULL
        """,
        (sport,),
    )
    rows = cursor.fetchall()
    if not rows:
        return {"n_rows": 0}
    n_books = [r[0] for r in rows if r[0] is not None]
    # Pinnacle % only for T-12h pre-game (where Pinnacle is reliably present)
    pin_pre12h = [r[1] for r in rows if r[5] is not None and 0 < r[5] <= 720]
    pin_pct = (sum(pin_pre12h) / len(pin_pre12h) * 100) if pin_pre12h else 0
    # OI median only for entry-window rows (T-1h to T-24h pre-game)
    entry_rows = [
        r for r in rows
        if r[5] is not None and PRE_WINDOW_MIN_MINS <= r[5] <= PRE_WINDOW_MAX_MINS
    ]
    oi_vals = [r[2] for r in entry_rows if r[2] is not None]
    home_legs = sum(1 for r in rows if r[3] == 1)
    away_legs = sum(1 for r in rows if r[3] == 0 and r[4] != 1)
    tie_legs = sum(1 for r in rows if r[4] == 1)
    return {
        "n_rows": len(rows),
        "n_rows_entry_window": len(entry_rows),
        "n_books_p10": _percentile(n_books, 0.10),
        "n_books_p50": _percentile(n_books, 0.50),
        "pin_pct_pre12h": pin_pct,
        "oi_median": _percentile(oi_vals, 0.50),
        "home_legs": home_legs,
        "away_legs": away_legs,
        "tie_legs": tie_legs,
    }


def _bucket_roi(entries: list[dict]) -> dict:
    """Bucket entries by eu-consensus-implied edge, compute simulated ROI.

    Edge = eu_yes_prob - kalshi_yes_ask. Positive = Kalshi YES underpriced
    relative to eu sharp-book consensus (the anchor live strategies use).
    """
    by_bucket = defaultdict(list)
    for e in entries:
        edge = e["eu_prob"] - e["yes_ask"]
        for lo, hi in EDGE_BUCKETS:
            if lo <= edge < hi:
                won = (e["result"] == "yes")
                pnl = _sim_pnl(e["yes_ask"], won)
                by_bucket[(lo, hi)].append({"pnl": pnl, "won": won, "edge": edge})
                break
    out = {}
    for (lo, hi), trades in by_bucket.items():
        if not trades:
            continue
        n = len(trades)
        wins = sum(1 for t in trades if t["won"])
        avg_pnl = sum(t["pnl"] for t in trades) / n
        out[_bucket_label(lo, hi)] = {
            "n": n,
            "win_rate": wins / n,
            "roi_per_dollar": avg_pnl,
        }
    return out


def _decide(quality: dict, buckets: dict, n_games: int, overall_roi: float) -> dict:
    """Apply criteria → PROMOTE / HOLD / KILL with reasoning.

    Pinnacle % is reported in quality but NOT a gate — live strategies use
    eu consensus, not Pinnacle directly.
    """
    failures = []

    if n_games < PROMOTE_CRITERIA["min_settled_games"]:
        failures.append(f"n_games={n_games} < {PROMOTE_CRITERIA['min_settled_games']}")

    if buckets:
        top_bucket_roi = max(b["roi_per_dollar"] for b in buckets.values())
        if top_bucket_roi < PROMOTE_CRITERIA["min_top_bucket_roi"]:
            failures.append(f"top_roi={top_bucket_roi:+.1%} < +{PROMOTE_CRITERIA['min_top_bucket_roi']:.0%}")
    elif n_games == 0:
        failures.append("no settled entries")

    n_books_p50 = quality.get("n_books_p50") or 0
    if n_books_p50 < PROMOTE_CRITERIA["min_n_books_p50"]:
        failures.append(f"n_books_p50={n_books_p50} < {PROMOTE_CRITERIA['min_n_books_p50']}")

    oi_med = quality.get("oi_median") or 0
    if oi_med < PROMOTE_CRITERIA["min_kalshi_oi_median"]:
        failures.append(f"oi_median=${oi_med:.0f} < ${PROMOTE_CRITERIA['min_kalshi_oi_median']:.0f}")

    # Kill conditions
    if overall_roi < KILL_CRITERIA["max_overall_roi"] and n_games >= 10:
        return {"verdict": "KILL", "reason": f"overall_roi={overall_roi:+.1%} < {KILL_CRITERIA['max_overall_roi']:+.0%} (n={n_games})"}
    if len(failures) >= KILL_CRITERIA["max_failed_criteria"] + 1:
        return {"verdict": "KILL", "reason": f"{len(failures)} criteria failed: {'; '.join(failures)}"}

    if not failures:
        return {"verdict": "PROMOTE", "reason": "all criteria met"}
    return {"verdict": "HOLD", "reason": f"{len(failures)} fail(s): {'; '.join(failures)}"}


def _format_report(results: dict, window_days: int) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"🎯 <b>Sport promotion evaluation — {now_iso}</b>",
        f"<i>Window: last {window_days} days. Entry filter: pre-game, T-1h to T-24h, "
        f"n_books_eu≥{MIN_N_BOOKS_FOR_SIM}, eu consensus available, settled. "
        f"Edge = eu_yes_prob − kalshi_yes_ask.</i>",
        "",
    ]
    for sport in TARGET_SPORTS:
        r = results.get(sport)
        if not r:
            lines.append(f"<b>{sport}</b>: (no data)\n")
            continue
        verdict_emoji = {"PROMOTE": "✅", "HOLD": "🟡", "KILL": "🔴"}.get(r["decision"]["verdict"], "?")
        lines.append(f"{verdict_emoji} <b>{sport}: {r['decision']['verdict']}</b>")
        lines.append(f"   <i>{r['decision']['reason']}</i>")
        q = r["quality"]
        lines.append(
            f"   quality: n_rows={q.get('n_rows', 0)}, "
            f"books_p50={q.get('n_books_p50', 0)} (p10={q.get('n_books_p10', 0)}), "
            f"pin_pct_T-12h={q.get('pin_pct_pre12h', 0):.0f}%, "
            f"OI_median=${q.get('oi_median', 0):.0f}"
        )
        h, a, t = q.get("home_legs", 0), q.get("away_legs", 0), q.get("tie_legs", 0)
        if t > 0:
            mn, mx = min(h, a, t), max(h, a, t)
            leg_status = "balanced" if mn == mx else f"⚠️ H={h} A={a} T={t}"
            lines.append(f"   3-way legs: {leg_status}")
        lines.append(f"   sim n_games_settled: {r['n_games']}, overall_roi: {r['overall_roi']:+.1%}")
        if r["buckets"]:
            lines.append("   <pre>")
            lines.append(f"   {'edge':>8s} {'n':>5s} {'WR':>5s} {'ROI':>8s}")
            for label, b in sorted(r["buckets"].items()):
                lines.append(
                    f"   {label:>8s} {b['n']:>5d} {b['win_rate']*100:>4.0f}% "
                    f"{b['roi_per_dollar']*100:>+7.1f}%"
                )
            lines.append("   </pre>")
        lines.append("")
    lines.append(f"<i>Criteria (all required): n_games≥{PROMOTE_CRITERIA['min_settled_games']}, "
                 f"top_roi≥+{PROMOTE_CRITERIA['min_top_bucket_roi']:.0%}, "
                 f"books_p50≥{PROMOTE_CRITERIA['min_n_books_p50']}, "
                 f"OI≥${PROMOTE_CRITERIA['min_kalshi_oi_median']:.0f}. "
                 f"Pinnacle % shown for context, not gated.</i>")
    return "\n".join(lines)


def _evaluate(db_path: str, window_days: int, sport_filter: str | None) -> dict:
    conn = sqlite3.connect(db_path)
    sports = [sport_filter] if sport_filter else TARGET_SPORTS
    results = {}
    for sport in sports:
        entries = _load_entries(conn, sport, window_days)
        quality = _load_quality_stats(conn, sport, window_days)
        buckets = _bucket_roi(entries)
        n_games = len(entries)
        overall_roi = (sum(_sim_pnl(e["yes_ask"], e["result"] == "yes") for e in entries) / n_games) if entries else 0.0
        decision = _decide(quality, buckets, n_games, overall_roi)
        results[sport] = {
            "quality": quality, "buckets": buckets, "n_games": n_games,
            "overall_roi": overall_roi, "decision": decision,
        }
    conn.close()
    return results


async def send(report: str):
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
        await notifier.send(part, msg_type=f"promotion_eval_{i}")
    logger.info(f"Sent {len(parts)} Telegram message(s), {len(report)} chars total.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db", help="Path to bot.db")
    parser.add_argument("--window", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--sport", default=None, help="Single sport (e.g., EPL)")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    results = _evaluate(args.db, args.window, args.sport)
    report = _format_report(results, args.window)
    if args.dry_run:
        print(report)
        return
    asyncio.run(send(report))


if __name__ == "__main__":
    main()
