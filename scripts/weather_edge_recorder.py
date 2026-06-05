#!/usr/bin/env python3
"""Forward weather-edge experiment recorder (record-only, NO TRADING).

The ONLY Kalshi vector not proven-efficient by the 2026-06-05 sweep: weather.
Historical backtest is impossible (settled markets' closing price is degenerate
0/1 — the market trades until the daily high is already determined). The edge,
if any, lives in the DAY-AHEAD price (still uncertain) vs a good forecast. So we
record it forward and measure EV at settlement.

Each run, for each city, captures the next-1-2-day (still-uncertain) Kalshi
high-temp bucket prices + the NWS point forecast for that city's EXACT
settlement station, into data/weather_edge.db. A separate measure script joins
the eventual observed high (NWS) to compute whether trading the forecast-vs-
market divergence is +EV net of fee.

Settlement stations (from market rules_primary):
  NY=Central Park, CHI=Chicago Midway, MIA=Miami Intl, AUS=Austin Bergstrom,
  LAX=LA Airport, DEN=Denver, PHIL=Philadelphia Intl.

Run twice daily via kalshi-weather-edge.timer.
"""
import urllib.request, json, time, sqlite3, os, re
from datetime import datetime, timezone

OUT = os.getenv("WEATHER_EDGE_DB", "/home/ubuntu/kalshi-arb/data/weather_edge.db")
# series -> (settlement-station lat, lon, label)
CITIES = {
    "KXHIGHNY":   (40.78, -73.97, "Central Park"),
    "KXHIGHCHI":  (41.79, -87.75, "Chicago Midway"),
    "KXHIGHMIA":  (25.79, -80.29, "Miami Intl"),
    "KXHIGHAUS":  (30.19, -97.67, "Austin Bergstrom"),
    "KXHIGHLAX":  (33.94, -118.41, "LA Airport"),
    "KXHIGHDEN":  (39.85, -104.66, "Denver"),
    "KXHIGHPHIL": (39.87, -75.24, "Philadelphia Intl"),
}
MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


def _get(url, ua, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.5 * (i + 1)); continue
            if e.code in (404, 400):
                return None
            time.sleep(1.0)
        except Exception:
            time.sleep(1.0)
    return None


def kget(path):
    return _get("https://api.elections.kalshi.com/trade-api/v2" + path, "weather-edge/1.0")


def nws_highs(lat, lon):
    """date 'YYYY-MM-DD' -> forecast daytime high (F) for the exact station gridpoint."""
    pts = _get(f"https://api.weather.gov/points/{lat},{lon}", "(kalshi-weather-edge, ops@example.com)")
    if not pts:
        return {}
    fc = _get(pts["properties"]["forecast"], "(kalshi-weather-edge, ops@example.com)")
    if not fc:
        return {}
    return {p["startTime"][:10]: p["temperature"]
            for p in fc["properties"]["periods"] if p.get("isDaytime")}


def init_db():
    c = sqlite3.connect(OUT)
    c.execute("""CREATE TABLE IF NOT EXISTS weather_snap(
        recorded_at TEXT, series TEXT, station TEXT, event_ticker TEXT, event_date TEXT,
        market_ticker TEXT, floor_strike REAL, cap_strike REAL,
        yes_bid REAL, yes_ask REAL, yes_bid_size REAL, yes_ask_size REAL,
        nws_forecast_high REAL,
        UNIQUE(recorded_at, market_ticker))""")
    c.execute("""CREATE TABLE IF NOT EXISTS weather_actual(
        series TEXT, station TEXT, event_date TEXT, actual_high REAL, fetched_at TEXT,
        UNIQUE(series, event_date))""")
    c.commit()
    return c


def main():
    now = datetime.now(timezone.utc).isoformat()
    db = init_db()
    nrec = 0
    for ser, (lat, lon, label) in CITIES.items():
        fhigh = nws_highs(lat, lon); time.sleep(0.5)
        mk = kget(f"/markets?series_ticker={ser}&status=open&limit=200"); time.sleep(0.6)
        ms = mk.get("markets", []) if mk else []
        from collections import defaultdict
        byev = defaultdict(list)
        for m in ms:
            byev[m.get("event_ticker")].append(m)
        for ev, buckets in byev.items():
            g = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev or "")
            if not g:
                continue
            edate = f"20{g.group(1)}-{MONTHS.get(g.group(2),0):02d}-{int(g.group(3)):02d}"
            fh = fhigh.get(edate)
            # only record while STILL UNCERTAIN (no bucket priced >0.85)
            mids = []
            for m in buckets:
                try: mids.append((float(m["yes_bid_dollars"]) + float(m["yes_ask_dollars"])) / 2)
                except Exception: pass
            if not mids or max(mids) > 0.85:
                continue
            for m in buckets:
                def fv(k):
                    v = m.get(k)
                    try: return float(v)
                    except (TypeError, ValueError): return None
                try:
                    db.execute("INSERT OR IGNORE INTO weather_snap VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now, ser, label, ev, edate, m["ticker"],
                         fv("floor_strike"), fv("cap_strike"),
                         fv("yes_bid_dollars"), fv("yes_ask_dollars"),
                         fv("yes_bid_size_fp"), fv("yes_ask_size_fp"), fh))
                    nrec += 1
                except Exception:
                    pass
        db.commit()
    print(f"[weather-edge] {now}: recorded {nrec} bucket snapshots across {len(CITIES)} cities -> {OUT}")


if __name__ == "__main__":
    main()
