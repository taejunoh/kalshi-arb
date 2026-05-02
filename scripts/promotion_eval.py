"""Sport-promotion eligibility evaluator — comprehensive analysis.

Multi-section report per OBSERVATION_ONLY sport, output to Telegram or stdout:

  Section 1  EXECUTIVE SUMMARY
    - Per sport: PROMOTE / HOLD / KILL with one-line reasoning
    - Compares against existing live-strategy baseline ROI (from trades table)

  Section 2  STRATEGY × SPORT REPLAY MATRIX
    - For each (strategy, sport): n_fired, win_rate, ROI mean + 95% bootstrap CI
    - Includes longshot_v31, midshot_v31, underdog_v31, underdog_v32, v4_edge
    - Plus V3 baselines (us region) for region-comparison

  Section 3  3-WAY LEG ANALYSIS (soccer only)
    - Per (sport, leg ∈ {HOME, AWAY, TIE}): replay results
    - TIE leg often behaves differently → must analyze separately

  Section 4  CLOSING LINE VALUE (CLV)
    - For each ticker: entry price (T-1h to T-24h) vs closing price (last
      pre-game obs). CLV+ = our entry beat the close. Forward-ROI predictor
      that's less noisy than realized ROI at small n.

  Section 5  DATA QUALITY
    - Per sport: n_books p10/p50, Pinnacle %, OI median (entry window),
      leg balance, settled coverage

  Section 6  DECISION CRITERIA & CAVEATS
    - Lists thresholds, current data window, known limitations

Usage:
    venv/bin/python scripts/promotion_eval.py
    venv/bin/python scripts/promotion_eval.py --window 7
    venv/bin/python scripts/promotion_eval.py --sport EPL --dry-run
    venv/bin/python scripts/promotion_eval.py --bootstrap-iter 2000  # tighter CI
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.strategy_replay import STRATEGIES, sim_pnl, sim_cost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("promotion_eval")

# Decision criteria (single source of truth — keep in sync with
# memory/sport_promotion_thresholds.md)
PROMOTE_CRITERIA = {
    "min_settled_games": 25,
    "min_top_strategy_roi": 0.03,    # +3% by ANY strategy beats threshold
    "min_n_books_p50": 8,
    "min_kalshi_oi_median": 500.0,
    "min_ci_lower_for_promote": -0.05,  # 95% CI lower bound > -5% (alpha not noise)
}
KILL_CRITERIA = {
    "max_overall_roi_with_n10": -0.10,
    "max_failed_criteria": 2,
}

# Sim parameters
MIN_N_BOOKS_FOR_SIM = 8
PRE_WINDOW_MIN_MINS = 60
PRE_WINDOW_MAX_MINS = 1440  # T-24h
TARGET_SPORTS = ["MLS", "EPL", "LAL", "UCL"]
LIVE_BASELINE_SPORTS = ["NBA", "MLB", "NHL"]
EDGE_BUCKETS = [(-1.0, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)]


# ----------------------------------------------------------------------
# Stat helpers
# ----------------------------------------------------------------------

def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = min(int(len(s) * p), len(s) - 1)
    return s[k]


def _bootstrap_ci(values: list[float], n_iter: int = 1000, alpha: float = 0.05) -> tuple:
    """95% bootstrap confidence interval for the mean. Returns (lo, hi)."""
    if len(values) < 5:
        return (None, None)
    means = []
    for _ in range(n_iter):
        sample = [random.choice(values) for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo_idx = int(n_iter * alpha / 2)
    hi_idx = int(n_iter * (1 - alpha / 2))
    return (means[lo_idx], means[hi_idx])


def _bucket_label(lo, hi):
    if lo < 0:
        return "<0%"
    return f"{int(lo*100)}-{int(hi*100)}%"


# ----------------------------------------------------------------------
# Data loaders
# ----------------------------------------------------------------------

def _load_entries(conn: sqlite3.Connection, sport: str, window_days: int) -> list[dict]:
    """Pick the FIRST observer row per ticker in the entry window with a settlement."""
    cursor = conn.execute(
        f"""
        SELECT o.kalshi_ticker, o.timestamp, o.kalshi_yes_ask, o.kalshi_no_ask,
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
    seen = set()
    entries = []
    for r in cursor.fetchall():
        if r[0] in seen:
            continue
        seen.add(r[0])
        entries.append({
            "ticker": r[0], "ts": r[1],
            "yes_ask": r[2], "no_ask": r[3] if r[3] else (1.0 - r[2]),
            "pin_prob": r[4], "eu_prob": r[5], "us_prob": r[6],
            "is_yes_home": r[7], "is_yes_draw": r[8],
            "n_books_eu": r[9], "has_pinnacle": r[10],
            "open_interest": r[11] or 0,
            "minutes_to_start": r[12], "result": r[14],
        })
    return entries


def _load_quality_stats(conn: sqlite3.Connection, sport: str, window_days: int) -> dict:
    cursor = conn.execute(
        f"""
        SELECT n_books_eu, has_pinnacle, kalshi_open_interest,
               is_yes_home, is_yes_draw, minutes_to_start, kalshi_ticker
        FROM odds_observations
        WHERE sport = ?
          AND timestamp >= datetime('now', '-{int(window_days)} days')
          AND n_books_eu IS NOT NULL
        """,
        (sport,),
    )
    rows = cursor.fetchall()
    if not rows:
        return {"n_rows": 0, "n_unique_tickers": 0}
    n_books = [r[0] for r in rows if r[0] is not None]
    pin_pre12h = [r[1] for r in rows if r[5] is not None and 0 < r[5] <= 720]
    pin_pct = (sum(pin_pre12h) / len(pin_pre12h) * 100) if pin_pre12h else 0
    entry_rows = [r for r in rows
                  if r[5] is not None and PRE_WINDOW_MIN_MINS <= r[5] <= PRE_WINDOW_MAX_MINS]
    oi_vals = [r[2] for r in entry_rows if r[2] is not None]
    home_legs = sum(1 for r in rows if r[3] == 1)
    away_legs = sum(1 for r in rows if r[3] == 0 and r[4] != 1)
    tie_legs = sum(1 for r in rows if r[4] == 1)
    return {
        "n_rows": len(rows),
        "n_unique_tickers": len({r[6] for r in rows}),
        "n_rows_entry_window": len(entry_rows),
        "n_books_p10": _percentile(n_books, 0.10),
        "n_books_p50": _percentile(n_books, 0.50),
        "pin_pct_pre12h": pin_pct,
        "oi_median": _percentile(oi_vals, 0.50),
        "home_legs": home_legs,
        "away_legs": away_legs,
        "tie_legs": tie_legs,
    }


def _load_live_baselines(conn: sqlite3.Connection, window_days: int) -> dict:
    """Per-strategy realized ROI categorized by current liveness status.

    Returns three groups:
      live_now: strategy is in config.LIVE_STRATEGIES env right now → only
        status='settled' (real-money) trades count. This is the comparison
        baseline for new-sport promotion decisions.
      demoted: strategy is NOT in LIVE_STRATEGIES env but has 'settled' trades
        in the window — i.e. was demoted to paper recently. Shown as historical
        context; do NOT use for go/no-go on new sports.
      paper_only: strategy never live in window — only paper_* statuses.
    """
    try:
        from core.config import Config
        live_set = set(Config().LIVE_STRATEGIES) if Config else set()
    except Exception:
        live_set = set()

    cursor = conn.execute(
        f"""
        SELECT strategy, status, COUNT(*) AS n,
               SUM(pnl) AS total_pnl,
               SUM(price * 100) AS total_size_proxy
        FROM trades
        WHERE timestamp >= datetime('now', '-{int(window_days)} days')
          AND status IN ('filled', 'settled', 'paper_filled', 'paper_settled')
          AND strategy IS NOT NULL
        GROUP BY strategy, status
        """
    )
    by_strat = defaultdict(lambda: {"live_n": 0, "live_pnl": 0.0, "live_size": 0.0,
                                      "paper_n": 0, "paper_pnl": 0.0, "paper_size": 0.0})
    for r in cursor.fetchall():
        strat, status, n, pnl, size_proxy = r
        if size_proxy is None or size_proxy <= 0:
            continue
        if status in ("settled", "filled"):
            by_strat[strat]["live_n"] += n
            by_strat[strat]["live_pnl"] += pnl or 0
            by_strat[strat]["live_size"] += size_proxy
        else:  # paper_*
            by_strat[strat]["paper_n"] += n
            by_strat[strat]["paper_pnl"] += pnl or 0
            by_strat[strat]["paper_size"] += size_proxy

    live_now, demoted, paper_only = {}, {}, {}
    for strat, d in by_strat.items():
        live_roi = d["live_pnl"] / d["live_size"] if d["live_size"] > 0 else None
        paper_roi = d["paper_pnl"] / d["paper_size"] if d["paper_size"] > 0 else None
        if strat in live_set:
            if d["live_n"] >= 5:
                live_now[strat] = {"n": d["live_n"], "roi": live_roi}
        elif d["live_n"] >= 5:
            # Was live in window, no longer in env
            demoted[strat] = {"n_live_historical": d["live_n"], "roi_live_historical": live_roi,
                              "n_paper": d["paper_n"], "roi_paper": paper_roi}
        elif d["paper_n"] >= 5:
            paper_only[strat] = {"n": d["paper_n"], "roi": paper_roi}
    return {"live_now": live_now, "demoted": demoted, "paper_only": paper_only,
            "live_strategies_env": sorted(live_set)}


def _load_clv(conn: sqlite3.Connection, sport: str, window_days: int) -> dict:
    """Closing-line value: entry price (first row in entry window) vs close (last
    pre-game row before commence). CLV+ = our entry beat the close."""
    cursor = conn.execute(
        f"""
        SELECT kalshi_ticker, timestamp, kalshi_yes_ask, kalshi_no_ask,
               minutes_to_start, is_yes_home, is_yes_draw
        FROM odds_observations
        WHERE sport = ?
          AND timestamp >= datetime('now', '-{int(window_days)} days')
          AND minutes_to_start IS NOT NULL
          AND minutes_to_start > 0
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY kalshi_ticker, timestamp
        """,
        (sport,),
    )
    by_ticker = defaultdict(list)
    for r in cursor.fetchall():
        by_ticker[r[0]].append(r)
    yes_clv_pos, yes_clv_total = 0, 0
    for ticker, rows in by_ticker.items():
        # Entry: first row with mts in [PRE_WINDOW_MIN, PRE_WINDOW_MAX]
        entry = next((r for r in rows
                      if PRE_WINDOW_MIN_MINS <= (r[4] or 0) <= PRE_WINDOW_MAX_MINS), None)
        # Close: last row with mts > 0 (right before kickoff)
        close = max(rows, key=lambda r: -(r[4] or 9999))  # smallest positive mts
        if not entry or not close or entry == close:
            continue
        if entry[2] is None or close[2] is None:
            continue
        # YES-side CLV: close price > entry price means market caught up to our cheap buy
        yes_clv_total += 1
        if close[2] > entry[2]:
            yes_clv_pos += 1
    return {
        "n_tickers": yes_clv_total,
        "clv_pos_pct": (yes_clv_pos / yes_clv_total * 100) if yes_clv_total else 0,
    }


# ----------------------------------------------------------------------
# Replay analysis
# ----------------------------------------------------------------------

def _replay_all_strategies(entries: list[dict], n_boot: int) -> dict:
    """Run all strategies against entries. Returns {strategy: {n, roi, ci_lo, ci_hi, win_rate}}.

    ROI denominator is total_cost (stake + fee), not stake alone, so losses
    cap at -100% per standard betting convention.
    """
    results = {}
    for strat_name, fire_fn in STRATEGIES.items():
        pnls, costs, per_trade_rois = [], [], []
        wins = 0
        for e in entries:
            sig = fire_fn(e)
            if sig is None:
                continue
            pnl = sim_pnl(sig["side"], sig["price"], sig["size_usdc"], e["result"])
            cost = sim_cost(sig["price"], sig["size_usdc"])
            pnls.append(pnl)
            costs.append(cost)
            per_trade_rois.append(pnl / cost if cost > 0 else 0.0)
            won = (sig["side"] == "yes" and e["result"] == "yes") or \
                  (sig["side"] == "no" and e["result"] == "no")
            if won:
                wins += 1
        if not pnls:
            results[strat_name] = {"n": 0, "roi": 0.0, "ci_lo": None, "ci_hi": None,
                                    "win_rate": 0.0, "total_pnl": 0.0, "total_cost": 0.0}
            continue
        total_pnl = sum(pnls)
        total_cost = sum(costs)
        roi = total_pnl / total_cost if total_cost > 0 else 0.0
        ci_lo, ci_hi = _bootstrap_ci(per_trade_rois, n_iter=n_boot)
        results[strat_name] = {
            "n": len(pnls),
            "roi": roi,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "win_rate": wins / len(pnls),
            "total_pnl": total_pnl,
            "total_cost": total_cost,
        }
    return results


def _replay_legs(entries: list[dict], n_boot: int) -> dict:
    """For 3-way sports, split entries by leg (HOME/AWAY/TIE) and replay each."""
    legs = {"HOME": [], "AWAY": [], "TIE": []}
    for e in entries:
        if e["is_yes_draw"] == 1:
            legs["TIE"].append(e)
        elif e["is_yes_home"] == 1:
            legs["HOME"].append(e)
        elif e["is_yes_home"] == 0:
            legs["AWAY"].append(e)
    return {leg: _replay_all_strategies(entries_l, n_boot) for leg, entries_l in legs.items()}


# ----------------------------------------------------------------------
# Decision logic
# ----------------------------------------------------------------------

def _decide(quality: dict, replay: dict, n_games: int, ci_required: bool = True) -> dict:
    failures = []
    if n_games < PROMOTE_CRITERIA["min_settled_games"]:
        failures.append(f"n_games={n_games} < {PROMOTE_CRITERIA['min_settled_games']}")
    n_books_p50 = quality.get("n_books_p50") or 0
    if n_books_p50 < PROMOTE_CRITERIA["min_n_books_p50"]:
        failures.append(f"n_books_p50={n_books_p50} < {PROMOTE_CRITERIA['min_n_books_p50']}")
    oi_med = quality.get("oi_median") or 0
    if oi_med < PROMOTE_CRITERIA["min_kalshi_oi_median"]:
        failures.append(f"oi_median=${oi_med:.0f} < ${PROMOTE_CRITERIA['min_kalshi_oi_median']:.0f}")

    # Best strategy by ROI (where ci_lo > -5% if CI available)
    best_strat = None
    best_roi = -999.0
    for strat_name, r in replay.items():
        if r["n"] < 10:  # too few to evaluate
            continue
        if r["roi"] > best_roi:
            ok_ci = (not ci_required) or (r["ci_lo"] is None) or (r["ci_lo"] >= PROMOTE_CRITERIA["min_ci_lower_for_promote"])
            if ok_ci:
                best_roi = r["roi"]
                best_strat = strat_name
    if best_strat is None or best_roi < PROMOTE_CRITERIA["min_top_strategy_roi"]:
        failures.append(f"no strategy ROI ≥ +{PROMOTE_CRITERIA['min_top_strategy_roi']:.0%} with CI lower ≥ {PROMOTE_CRITERIA['min_ci_lower_for_promote']:+.0%}")

    # KILL conditions — ROI denominator = total cost (stake + fee), losses cap at -100%
    overall_pnl = sum(r["total_pnl"] for r in replay.values() if r["n"] >= 10)
    overall_cost = sum(r["total_cost"] for r in replay.values() if r["n"] >= 10)
    overall_roi = (overall_pnl / overall_cost) if overall_cost > 0 else 0.0
    if overall_roi < KILL_CRITERIA["max_overall_roi_with_n10"] and n_games >= 10:
        return {"verdict": "KILL", "reason": f"overall_roi={overall_roi:+.1%} (n={n_games})", "best_strategy": best_strat}
    if len(failures) >= KILL_CRITERIA["max_failed_criteria"] + 1:
        return {"verdict": "KILL", "reason": f"{len(failures)} criteria failed", "best_strategy": None,
                "failures": failures}

    if not failures:
        return {"verdict": "PROMOTE", "reason": f"all met; best={best_strat} +{best_roi:.1%}",
                "best_strategy": best_strat, "best_roi": best_roi}
    return {"verdict": "HOLD", "reason": f"{len(failures)} fail(s)",
            "best_strategy": best_strat, "best_roi": best_roi, "failures": failures}


# ----------------------------------------------------------------------
# Report builders
# ----------------------------------------------------------------------

def _section_summary(results: dict, baselines: dict) -> list[str]:
    lines = ["<b>📌 EXECUTIVE SUMMARY</b>"]
    for sport in TARGET_SPORTS:
        r = results.get(sport)
        if not r:
            lines.append(f"  {sport}: (no data)")
            continue
        d = r["decision"]
        emoji = {"PROMOTE": "✅", "HOLD": "🟡", "KILL": "🔴"}[d["verdict"]]
        line = f"  {emoji} <b>{sport}</b>: {d['verdict']} — {d['reason']}"
        if d.get("best_strategy") and d.get("best_roi") is not None:
            line += f" (best: {d['best_strategy']} +{d['best_roi']:.1%})"
        lines.append(line)

    # Currently-live baselines — the comparison bar for promotion decisions
    live_now = baselines.get("live_now", {})
    if live_now:
        lines.append("")
        lines.append("<b>📊 LIVE baseline (currently in LIVE_STRATEGIES env, settled-only)</b>")
        for strat in sorted(live_now):
            b = live_now[strat]
            roi_str = f"{b['roi']:+.1%}" if b["roi"] is not None else "n/a"
            lines.append(f"  {strat}: n={b['n']}, ROI {roi_str}")

    demoted = baselines.get("demoted", {})
    if demoted:
        lines.append("")
        lines.append("<i>⚠️ Demoted strategies (NOT live anymore — historical context only):</i>")
        for strat in sorted(demoted):
            b = demoted[strat]
            live_roi_str = f"{b['roi_live_historical']:+.1%}" if b["roi_live_historical"] is not None else "n/a"
            paper_roi_str = f"{b['roi_paper']:+.1%}" if b["roi_paper"] is not None else "n/a"
            lines.append(
                f"  {strat}: live_hist n={b['n_live_historical']} ROI {live_roi_str} | "
                f"paper n={b['n_paper']} ROI {paper_roi_str}"
            )

    paper_only = baselines.get("paper_only", {})
    if paper_only:
        lines.append("")
        lines.append("<i>📝 Paper-only strategies (last 7d):</i>")
        for strat in sorted(paper_only):
            b = paper_only[strat]
            roi_str = f"{b['roi']:+.1%}" if b["roi"] is not None else "n/a"
            lines.append(f"  {strat}: n={b['n']}, ROI {roi_str}")

    return lines


def _section_strategy_matrix(results: dict) -> list[str]:
    lines = ["", "<b>🎯 STRATEGY × SPORT REPLAY</b>",
             "<i>Per-trade ROI mean ± 95% bootstrap CI (n iter=1000). "
             "Strategies that fired <10 trades shown as '-'.</i>", "<pre>"]
    strategies_to_show = list(STRATEGIES.keys())
    # Header
    lines.append(f"{'sport':5s} {'strategy':16s} {'n':>4s} {'WR':>4s} {'ROI':>7s} {'CI_lo':>7s} {'CI_hi':>7s}")
    for sport in TARGET_SPORTS:
        r = results.get(sport)
        if not r:
            continue
        for strat in strategies_to_show:
            s = r["replay"].get(strat)
            if not s or s["n"] == 0:
                continue
            ci_lo_str = f"{s['ci_lo']*100:+5.0f}%" if s.get("ci_lo") is not None else "  -  "
            ci_hi_str = f"{s['ci_hi']*100:+5.0f}%" if s.get("ci_hi") is not None else "  -  "
            wr_str = f"{s['win_rate']*100:>3.0f}%"
            lines.append(
                f"{sport:5s} {strat:16s} {s['n']:>4d} {wr_str:>4s} "
                f"{s['roi']*100:>+5.1f}% {ci_lo_str:>7s} {ci_hi_str:>7s}"
            )
    lines.append("</pre>")
    return lines


def _section_legs(results: dict) -> list[str]:
    soccer_sports = ["MLS", "EPL", "LAL", "UCL"]
    has_data = any(results.get(s, {}).get("legs") for s in soccer_sports)
    if not has_data:
        return []
    lines = ["", "<b>⚽ 3-WAY LEG ANALYSIS (soccer only)</b>",
             "<i>Each game has 3 binary markets (HOME/AWAY/TIE). Replay run per leg.</i>",
             "<pre>"]
    lines.append(f"{'sport':5s} {'leg':4s} {'strategy':16s} {'n':>4s} {'WR':>4s} {'ROI':>7s}")
    for sport in soccer_sports:
        r = results.get(sport)
        if not r or not r.get("legs"):
            continue
        for leg, leg_replay in r["legs"].items():
            for strat, s in leg_replay.items():
                if s["n"] < 5:
                    continue
                lines.append(
                    f"{sport:5s} {leg:4s} {strat:16s} {s['n']:>4d} "
                    f"{s['win_rate']*100:>3.0f}% {s['roi']*100:>+5.1f}%"
                )
    lines.append("</pre>")
    return lines


def _section_clv(results: dict) -> list[str]:
    lines = ["", "<b>📈 CLOSING LINE VALUE (CLV)</b>",
             "<i>CLV+ = our entry-window price beat the closing-line price. "
             "Forward-ROI predictor; less noisy than realized ROI at small n.</i>", "<pre>"]
    lines.append(f"{'sport':5s} {'n_tickers':>9s} {'clv_pos%':>9s}")
    for sport in TARGET_SPORTS:
        r = results.get(sport)
        if not r or "clv" not in r:
            continue
        c = r["clv"]
        lines.append(f"{sport:5s} {c['n_tickers']:>9d} {c['clv_pos_pct']:>8.0f}%")
    lines.append("</pre>")
    return lines


def _section_quality(results: dict) -> list[str]:
    lines = ["", "<b>🔍 DATA QUALITY</b>", "<pre>"]
    lines.append(f"{'sport':5s} {'rows':>5s} {'tickers':>7s} "
                 f"{'books_p50':>9s} {'p10':>4s} {'pin%':>4s} {'OI_med':>7s} {'leg_check':>10s}")
    for sport in TARGET_SPORTS:
        r = results.get(sport)
        if not r:
            continue
        q = r["quality"]
        h, a, t = q.get("home_legs", 0), q.get("away_legs", 0), q.get("tie_legs", 0)
        if t > 0:
            mn, mx = min(h, a, t), max(h, a, t)
            leg = "OK" if mn == mx else f"!{h}/{a}/{t}"
        else:
            leg = "OK" if h == a else f"!{h}/{a}"
        lines.append(
            f"{sport:5s} {q.get('n_rows', 0):>5d} {q.get('n_unique_tickers', 0):>7d} "
            f"{q.get('n_books_p50', 0):>9d} {q.get('n_books_p10', 0):>4d} "
            f"{q.get('pin_pct_pre12h', 0):>3.0f}% {int(q.get('oi_median', 0)):>7d} {leg:>10s}"
        )
    lines.append("</pre>")
    return lines


def _section_criteria(window_days: int) -> list[str]:
    return [
        "", "<b>📋 DECISION CRITERIA</b>",
        f"<i>Window: last {window_days} days. Entry filter: pre-game, "
        f"T-1h to T-24h, n_books_eu≥{MIN_N_BOOKS_FOR_SIM}.</i>",
        f"PROMOTE (all required):",
        f"  • n_settled_games ≥ {PROMOTE_CRITERIA['min_settled_games']}",
        f"  • some strategy ROI ≥ +{PROMOTE_CRITERIA['min_top_strategy_roi']:.0%} with 95% CI lower ≥ {PROMOTE_CRITERIA['min_ci_lower_for_promote']:+.0%}",
        f"  • n_books_eu p50 ≥ {PROMOTE_CRITERIA['min_n_books_p50']}",
        f"  • Kalshi OI median (entry window) ≥ ${PROMOTE_CRITERIA['min_kalshi_oi_median']:.0f}",
        f"KILL: overall_roi < {KILL_CRITERIA['max_overall_roi_with_n10']:+.0%} (n≥10) OR ≥{KILL_CRITERIA['max_failed_criteria']+1} criteria fail",
        f"<i>Pinnacle % shown for context only — live v3.x strategies use eu consensus.</i>",
    ]


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------

def _evaluate(db_path: str, window_days: int, sport_filter: str | None,
              n_boot: int, ci_required: bool) -> tuple[dict, dict]:
    conn = sqlite3.connect(db_path)
    sports = [sport_filter] if sport_filter else TARGET_SPORTS
    results = {}
    for sport in sports:
        entries = _load_entries(conn, sport, window_days)
        quality = _load_quality_stats(conn, sport, window_days)
        clv = _load_clv(conn, sport, window_days)
        replay = _replay_all_strategies(entries, n_boot)
        is_3way = quality.get("tie_legs", 0) > 0
        legs = _replay_legs(entries, n_boot) if is_3way else None
        decision = _decide(quality, replay, len(entries), ci_required)
        results[sport] = {
            "quality": quality, "clv": clv, "replay": replay,
            "legs": legs, "n_games": len(entries), "decision": decision,
        }
    baselines = _load_live_baselines(conn, window_days)
    conn.close()
    return results, baselines


def _build_report(results: dict, baselines: dict, window_days: int) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🎯 <b>Sport promotion evaluation — {now_iso}</b>", ""]
    lines += _section_summary(results, baselines)
    lines += _section_strategy_matrix(results)
    lines += _section_legs(results)
    lines += _section_clv(results)
    lines += _section_quality(results)
    lines += _section_criteria(window_days)
    return "\n".join(lines)


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
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--window", type=int, default=7)
    parser.add_argument("--sport", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bootstrap-iter", type=int, default=1000)
    parser.add_argument("--no-ci-gate", action="store_true",
                        help="Disable CI lower-bound check (allow PROMOTE on small n)")
    args = parser.parse_args()
    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)
    random.seed(42)  # reproducible bootstrap
    results, baselines = _evaluate(args.db, args.window, args.sport,
                                    args.bootstrap_iter, ci_required=not args.no_ci_gate)
    report = _build_report(results, baselines, args.window)
    if args.dry_run:
        print(report)
        return
    asyncio.run(send(report))


if __name__ == "__main__":
    main()
