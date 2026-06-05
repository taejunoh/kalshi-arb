#!/usr/bin/env python3
"""Measure the weather-edge experiment (run after ~1-2 weeks of recording).

For each recorded DAY-AHEAD snapshot (still-uncertain market), fetch the bucket's
eventual Kalshi settlement (YES = actual high landed in that bucket). Build the
NWS-forecast-implied bucket probability, find market-vs-forecast divergences, and
simulate trading the FORECAST side (buy under-priced buckets / sell over-priced)
held to settlement, net of real fee. Reports EV/$1 by divergence threshold with
bootstrap CI, and the honest verdict (is weather an exploitable edge for us?).

GATE (same discipline as the rest of the 6-05 sweep): a real edge needs EV/$1 that
is positive AND grows/holds at larger thresholds AND bootstrap CI excludes 0 at
match(event)-level aggregation. Single-threshold / autocorrelated / CI-spans-0 = noise.
"""
import sqlite3, json, urllib.request, urllib.error, time, math, os, sys
import numpy as np

DB = os.getenv("WEATHER_EDGE_DB", "/home/ubuntu/kalshi-arb/data/weather_edge.db")
SIGMA = float(os.getenv("WEATHER_SIGMA", "3.0"))   # day-ahead high-temp forecast error (F)


def kget(path, tries=5):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(
                "https://api.elections.kalshi.com/trade-api/v2" + path,
                headers={"User-Agent": "weather-edge/1.0"}), timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.5 * (i + 1)); continue
            if e.code in (404, 400):
                return None
            time.sleep(1.0)
        except Exception:
            time.sleep(1.0)
    return None


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def nws_prob(fh, lo, cap):
    if fh is None or lo is None:
        return None
    hi = cap if cap is not None else lo + 0.999
    return ncdf((hi + 0.5 - fh) / SIGMA) - ncdf((lo - 0.5 - fh) / SIGMA)


def fee(p):
    return 0.07 * p * (1 - p) if 0 < p < 1 else 0


def main():
    db = sqlite3.connect(DB)
    # earliest snapshot per (market_ticker) = the most day-ahead read (most uncertain)
    rows = db.execute("""SELECT market_ticker, floor_strike, cap_strike, yes_bid, yes_ask,
                                nws_forecast_high, event_date, series, MIN(recorded_at)
                         FROM weather_snap WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
                         GROUP BY market_ticker""").fetchall()
    print(f"# {len(rows)} unique day-ahead bucket snapshots recorded")
    # fetch settlement per market (cache)
    trades = []  # (event_date, edge_size, pnl_per_$1)
    settle_cache = {}
    by_event = {}
    for (tk, fs, cap, yb, ya, fh, edate, ser, rec) in rows:
        if tk not in settle_cache:
            m = kget(f"/markets/{tk}"); time.sleep(0.3)
            res = (m.get("market", {}) if m else {}).get("result")
            settle_cache[tk] = res
        res = settle_cache.get(tk)
        if res not in ("yes", "no"):
            continue
        s = 1 if res == "yes" else 0
        np_ = nws_prob(fh, fs, cap)
        if np_ is None:
            continue
        a = ya; b = yb
        mid = (a + b) / 2
        edge = np_ - mid   # forecast says bucket more likely than market => buy YES
        pnl = None
        if edge > 0:
            pnl = (s - a - fee(a)) / a            # buy YES at ask
        elif edge < 0:
            pnl = ((1 - s) - (1 - b) - fee(b)) / (1 - b)  # buy NO
        if pnl is not None:
            trades.append((edate, abs(edge), pnl))
    print(f"# {len(trades)} tradeable bucket-divergences settled\n")
    if len(trades) < 30:
        print("insufficient settled data yet — keep recording."); return
    print(f"{'|forecast-market| thr':>22} | {'n':>5} | {'EV/$1':>8} | {'event-level bootCI95':>22}")
    rng = np.random.RandomState(11)
    for thr in [0.05, 0.08, 0.12, 0.18]:
        sub = [t for t in trades if t[1] >= thr]
        if len(sub) < 15:
            continue
        # event-level aggregation (avoid pseudo-replication across a city-day's buckets)
        by_ev = {}
        for ed, _, pnl in sub:
            by_ev.setdefault(ed, []).append(pnl)
        ev_means = [np.mean(v) for v in by_ev.values()]
        boots = [np.mean(rng.choice(ev_means, len(ev_means), replace=True)) for _ in range(3000)]
        cl, ch = np.percentile(boots, [2.5, 97.5])
        snap_ev = np.mean([p for _, _, p in sub])
        flag = "  <<< edge (CI>0, ROI>1%)" if (np.mean(ev_means) > 0.01 and cl > 0) else ""
        print(f"{thr*100:>20.0f}c | {len(sub):5} | {snap_ev:+8.4f} | "
              f"event-EV {np.mean(ev_means):+.4f} [{cl:+.3f},{ch:+.3f}]{flag}")
    print("\nVERDICT: edge only if some threshold shows event-level EV>1% with bootstrap CI>0,")
    print("and it holds/grows at larger thresholds. Else weather is efficient too (close it).")


if __name__ == "__main__":
    main()
