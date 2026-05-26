"""MLB v3 composite signal backtest.

Tests adding weather + lineup-confirmation gates to v2.1 (era≥2.0 + books).
Uses cached row JSON from earlier 7-signal combo backtest where possible;
augments with new gates pulled from weather_observations and
odds_observations.starter_home/away.

Signals added on top of base (era_diff≥2.0, books_deep, ERA range, ya band):
  - WEATHER_COLD: game-time temp < 60°F  (outdoor stadium)
  - WEATHER_WIND: game-time wind ≥ 10 km/h
  - LINEUP_CONFIRMED: odds_observations.starter_home + starter_away both non-null
                     at latest pre-game snapshot (vs probable)
  - HOME_PITCHER_RESTED: home pitcher days_rest ≥ 5 (proxy via mlb_pitcher_obs)

Composite test: which combination of 1-2 of these adds EV beyond base v2.1?
"""
import json, os, sqlite3
from collections import defaultdict
from datetime import datetime

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
CACHE = "/tmp/mlb_combo_rows.json"
FEE = 0.07

# Load base rows (already PIT, with sig dict)
with open(CACHE) as f:
    base_rows = json.load(f)
print(f"Loaded base rows from cache: {len(base_rows)}")

# Filter to v2.1 cohort: ERA_DIFF_2 + BOOKS_DEEP
v21 = [r for r in base_rows if r["sig"].get("ERA_DIFF_2") and r["sig"].get("BOOKS_DEEP")]
print(f"v2.1 cohort (ERA_DIFF≥2.0 + BOOKS_DEEP): n={len(v21)}")
total = sum(r["pnl"] for r in v21)
wins = sum(1 for r in v21 if r["won"])
print(f"  WR={100*wins/len(v21):.1f}%  EV={total/len(v21):+.3f}  total=${total:+.2f}\n")

# === Augment each row with new signals ===
c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

# Stadium coords (for weather lookup)
import json as _j
with open("/home/ubuntu/kalshi-arb/data/mlb_stadiums.json") as f:
    stadiums = {s["team"]: s for s in _j.load(f)["stadiums"]}

# Pre-fetch all weather data per stadium (in-memory)
# Use weather_observations table (already cached locally)
weather_cache = defaultdict(dict)  # team -> {date: (temp_c, wind_kmh)}
cur = c.execute("""
    SELECT team, fetched_at, current_temp_f, current_wind_mph
    FROM weather_observations
    WHERE fetched_at >= datetime('now', '-90 days')
""")
for team, fetched_at, t_f, w_mph in cur.fetchall():
    try:
        d = fetched_at[:10]
    except Exception:
        continue
    # Keep latest per (team, date)
    weather_cache[team][d] = (t_f, w_mph)

def get_weather(team, date_iso):
    # weather_observations is forecast, fetched on game day morning. Match date.
    return weather_cache.get(team, {}).get(date_iso)


# Iterate v21 rows and augment (weather-only; lineup deferred — needs index)
for r in v21:
    home = r["home"]
    di = r["date"]
    w = get_weather(home, di)
    if w:
        t_f, w_mph = w
        r["weather_cold"] = (t_f is not None and t_f < 60.0)
        r["weather_wind"] = (w_mph is not None and w_mph >= 10.0)
    else:
        r["weather_cold"] = False
        r["weather_wind"] = False
    r["lineup_confirmed"] = None  # placeholder, see lineup-specific test #7


# Analyze: composite signal effect
def stat(sub, label):
    if not sub:
        print(f"  {label:<30} n=0"); return
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(r["pnl"] for r in sub)
    avg_ya = sum(r["ya"] for r in sub) / n
    print(f"  {label:<30} n={n:>3}  WR={100*w/n:>5.1f}%  EV={p/n:+7.3f}  PnL={p:+7.2f}  avg_ya={avg_ya:.3f}")

print("=== Composite signals layered on v2.1 base ===")
stat(v21, "BASE v2.1 (era≥2.0+books)")

print("\n+1 SIGNAL:")
stat([r for r in v21 if r["weather_cold"]],  "+ WEATHER_COLD (<60°F)")
stat([r for r in v21 if r["weather_wind"]],  "+ WEATHER_WIND (≥10mph)")
stat([r for r in v21 if r["weather_cold"] or r["weather_wind"]], "+ COLD or WINDY")

print("\nALSO: NOT signal (avoiding cold/windy):")
stat([r for r in v21 if not r["weather_cold"]],  "  NOT cold (warm games)")
stat([r for r in v21 if not r["weather_wind"]],  "  NOT windy")
stat([r for r in v21 if not (r["weather_cold"] or r["weather_wind"])], "  NOT cold AND NOT windy")

print("\n=== Stats on weather availability ===")
total = len(v21)
print(f"  weather_cold:       {sum(1 for r in v21 if r['weather_cold'])}/{total}")
print(f"  weather_wind:       {sum(1 for r in v21 if r['weather_wind'])}/{total}")
