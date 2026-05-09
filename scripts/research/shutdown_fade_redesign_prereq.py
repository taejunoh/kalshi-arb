"""shutdown_fade V1 redesign — prereq Steps (1)–(4) per docs/future-work/shutdown-fade-redesign.md.

Pulls settled-market history for the funding-deadline-proxy / budget-resolution /
rhetoric-baseline series, fetches daily YES price candlesticks within a pre-close
window, and computes per-category base rate + fade-trigger sim ROI.

Output:
- scripts/research/shutdown_fade_data/markets_<series>.json (raw market dump)
- scripts/research/shutdown_fade_data/candles_<series>.json (raw candle dump)
- scripts/research/shutdown_fade_data/summary.txt (human-readable report)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.config import Config
from core.kalshi_client import KalshiClient

OUT_DIR = os.path.join(os.path.dirname(__file__), "shutdown_fade_data")
os.makedirs(OUT_DIR, exist_ok=True)

CATEGORIES = {
    "funding_deadline_proxy": ["KXDHSFUND", "KXDHSFUNDING"],
    "budget_resolution": ["KXSBUDGETRES", "KXHBUDGETRES", "KXVOTEBUDGETRESS"],
    "rhetoric_baseline": ["KXFEDMENTION"],
}

FADE_THRESHOLDS = [0.25, 0.35, 0.50]
PRE_CLOSE_WINDOW_DAYS = 14
KALSHI_FEE = 0.07  # per contract


def fetch_settled_markets(client: KalshiClient, series: str) -> list[dict]:
    """Paginate all settled markets for a series_ticker."""
    out = []
    cursor = None
    pages = 0
    while True:
        path = f"/trade-api/v2/markets?status=settled&series_ticker={series}&limit=200"
        if cursor:
            path += f"&cursor={cursor}"
        d = client._get(path)
        if not d:
            break
        markets = d.get("markets", [])
        out.extend(markets)
        cursor = d.get("cursor") or None
        pages += 1
        if not cursor or pages >= 20:
            break
    return out


def fetch_candles(client: KalshiClient, series: str, ticker: str, close_iso: str) -> list[dict]:
    """Daily candles for the PRE_CLOSE_WINDOW_DAYS window ending at close_time."""
    try:
        close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
    except Exception:
        return []
    end_unix = int(close_dt.timestamp())
    start_unix = end_unix - PRE_CLOSE_WINDOW_DAYS * 86400
    path = (
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={start_unix}&end_ts={end_unix}&period_interval=1440"
    )
    d = client._get(path)
    if not d:
        return []
    return d.get("candlesticks", []) or []


def candle_yes_close(c: dict) -> float | None:
    """Return YES close price in dollars (0..1)."""
    p = c.get("price") or {}
    s = p.get("close_dollars")
    if s is None:
        return None
    try:
        return float(s)
    except Exception:
        return None


def candle_yes_high(c: dict) -> float | None:
    p = c.get("price") or {}
    s = p.get("high_dollars")
    if s is None:
        return None
    try:
        return float(s)
    except Exception:
        return None


def analyze(markets: list[dict], candles_by_ticker: dict[str, list[dict]]) -> dict:
    """Compute base rate + fade sim per threshold for one category bucket."""
    n = len(markets)
    res_yes = sum(1 for m in markets if (m.get("result") or "").lower() == "yes")
    res_no = sum(1 for m in markets if (m.get("result") or "").lower() == "no")
    res_other = n - res_yes - res_no

    # Per-threshold fade sim:
    # - "trigger": within pre-close window, YES high ever ≥ threshold
    # - At trigger we BUY NO at price = (1 - threshold)
    # - Resolution NO → win 1.0 per contract (gross), else 0
    # - Per-contract PnL = (resolution payoff - entry_no_price - 2*fee) — fee on entry + settle? Kalshi takes fee on profit only.
    #   Conservative: apply fee on every fill (matches v3+ live convention in this repo).
    sims = {}
    for thr in FADE_THRESHOLDS:
        trig = []
        for m in markets:
            t = m.get("ticker", "")
            cs = candles_by_ticker.get(t, [])
            yes_max = 0.0
            for c in cs:
                h = candle_yes_high(c)
                if h is not None and h > yes_max:
                    yes_max = h
            if yes_max >= thr:
                trig.append((m, yes_max))
        n_trig = len(trig)
        n_trig_no = sum(1 for m, _ in trig if (m.get("result") or "").lower() == "no")
        n_trig_yes = sum(1 for m, _ in trig if (m.get("result") or "").lower() == "yes")
        # Sim PnL per contract
        # entry NO at no_price = 1 - thr; cost basis = no_price + fee
        # if resolves NO: payoff 1.0; PnL = 1 - no_price - fee = thr - fee
        # if resolves YES: payoff 0; PnL = -no_price - fee = -(1 - thr) - fee
        win_pnl = thr - KALSHI_FEE
        loss_pnl = -(1.0 - thr) - KALSHI_FEE
        total_pnl = n_trig_no * win_pnl + n_trig_yes * loss_pnl
        avg_pnl_per_trade = (total_pnl / n_trig) if n_trig else 0.0
        # ROI on capital deployed (no_price + fee per contract)
        capital_per = (1.0 - thr) + KALSHI_FEE
        roi = (avg_pnl_per_trade / capital_per) if capital_per else 0.0
        sims[f"{thr:.2f}"] = {
            "n_trigger": n_trig,
            "trigger_resolved_no": n_trig_no,
            "trigger_resolved_yes": n_trig_yes,
            "win_rate_no": (n_trig_no / n_trig) if n_trig else None,
            "avg_pnl_per_trade_usd": round(avg_pnl_per_trade, 4),
            "roi_pct": round(roi * 100, 2),
        }

    return {
        "n_markets": n,
        "result_yes": res_yes,
        "result_no": res_no,
        "result_other": res_other,
        "base_rate_yes": round(res_yes / n, 4) if n else None,
        "base_rate_no": round(res_no / n, 4) if n else None,
        "fade_sim": sims,
    }


def main() -> None:
    cfg = Config()
    client = KalshiClient(cfg)

    overall_summary: dict[str, dict] = {}
    raw_dump_paths: list[str] = []

    for cat, series_list in CATEGORIES.items():
        print(f"\n=== {cat} ===")
        all_markets: list[dict] = []
        candles_by_ticker: dict[str, list[dict]] = {}

        for series in series_list:
            print(f"  fetching settled markets: {series} ...", end="", flush=True)
            mkts = fetch_settled_markets(client, series)
            print(f" {len(mkts)}")
            all_markets.extend(mkts)
            mkt_path = os.path.join(OUT_DIR, f"markets_{series}.json")
            with open(mkt_path, "w") as f:
                json.dump(mkts, f, indent=2)
            raw_dump_paths.append(mkt_path)

        # Fetch candles for each market
        print(f"  fetching candles for {len(all_markets)} markets ...")
        for i, m in enumerate(all_markets, 1):
            tk = m.get("ticker", "")
            series = tk.split("-")[0]
            close_iso = m.get("close_time", "")
            if not tk or not close_iso:
                continue
            cs = fetch_candles(client, series, tk, close_iso)
            candles_by_ticker[tk] = cs
            if i % 25 == 0:
                print(f"    {i}/{len(all_markets)} done")
            time.sleep(0.05)
        cand_path = os.path.join(OUT_DIR, f"candles_{cat}.json")
        with open(cand_path, "w") as f:
            json.dump(candles_by_ticker, f, indent=2)
        raw_dump_paths.append(cand_path)

        cat_summary = analyze(all_markets, candles_by_ticker)
        overall_summary[cat] = cat_summary
        print(f"  base rate: NO={cat_summary['base_rate_no']} YES={cat_summary['base_rate_yes']} (n={cat_summary['n_markets']})")
        for thr_key, sim in cat_summary["fade_sim"].items():
            print(
                f"    thr={thr_key} trigger_n={sim['n_trigger']} "
                f"win_rate_no={sim['win_rate_no']} ROI={sim['roi_pct']}%"
            )

    # Write summary
    summary_path = os.path.join(OUT_DIR, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(overall_summary, f, indent=2)

    txt_path = os.path.join(OUT_DIR, "summary.txt")
    with open(txt_path, "w") as f:
        f.write("shutdown_fade redesign prereq — analysis summary\n")
        f.write(f"generated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"pre_close_window_days={PRE_CLOSE_WINDOW_DAYS}, fee={KALSHI_FEE}, thresholds={FADE_THRESHOLDS}\n\n")
        for cat, s in overall_summary.items():
            f.write(f"=== {cat} ===\n")
            f.write(f"  n_markets: {s['n_markets']}  YES={s['result_yes']}  NO={s['result_no']}  other={s['result_other']}\n")
            f.write(f"  base_rate_NO: {s['base_rate_no']}  base_rate_YES: {s['base_rate_yes']}\n")
            f.write(f"  fade sim (BUY NO when pre-close YES_high crosses threshold):\n")
            for thr_key, sim in s["fade_sim"].items():
                f.write(
                    f"    thr={thr_key}  triggered={sim['n_trigger']:>4d}  "
                    f"resolved_NO={sim['trigger_resolved_no']:>3d}  resolved_YES={sim['trigger_resolved_yes']:>3d}  "
                    f"win_rate_NO={sim['win_rate_no']}  avg_pnl=${sim['avg_pnl_per_trade_usd']}  ROI={sim['roi_pct']}%\n"
                )
            f.write("\n")
    print(f"\nSummary written: {txt_path}")
    print(f"Raw dumps: {len(raw_dump_paths)} files in {OUT_DIR}")


if __name__ == "__main__":
    main()
