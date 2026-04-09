"""Verify MLB/NHL matching + same-city disambiguation + fee calculation."""
import asyncio
import httpx
from core.config import Config
from core.kalshi_client import KalshiClient
from strategies.sports_trader import ODDS_TO_KALSHI_SHORT

c = Config()
k = KalshiClient(c)


def match_team(yes_sub, home_team, away_team):
    """Replicate the exact logic from sports_trader.py scan_and_execute."""
    home = home_team.lower()
    away = away_team.lower()
    ys = yes_sub.lower()

    # First: exact short-name match for ambiguous cities
    home_short = ODDS_TO_KALSHI_SHORT.get(home, "")
    away_short = ODDS_TO_KALSHI_SHORT.get(away, "")
    if home_short and home_short == ys:
        return "HOME_SHORT"
    if away_short and away_short == ys:
        return "AWAY_SHORT"

    # Fallback: city name
    home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
    away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
    if home_city in ys or ys in home_city:
        return "HOME_CITY"
    if away_city in ys or ys in away_city:
        return "AWAY_CITY"
    return "FAILED"


def match_game(title, games):
    """Replicate _match_kalshi_to_odds logic."""
    title_lower = title.lower()
    for game in games:
        home = game.get("home_team", "").lower()
        away = game.get("away_team", "").lower()
        home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
        away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
        home_first = home.split()[0]
        away_first = away.split()[0]
        home_match = home_city in title_lower or home_first in title_lower
        away_match = away_city in title_lower or away_first in title_lower
        if home_match and away_match:
            return game
    return None


async def main():
    http = httpx.AsyncClient(timeout=10)

    for series, sport, label in [
        ("KXMLBGAME", "baseball_mlb", "MLB"),
        ("KXNHLGAME", "icehockey_nhl", "NHL"),
    ]:
        print("=" * 60)
        print(f"{label} MATCHING + TEAM ID VERIFICATION")
        print("=" * 60)

        resp = await http.get(
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
            params={"apiKey": c.ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"},
        )
        games = resp.json()
        data = k._get(f"/trade-api/v2/markets?status=open&series_ticker={series}&limit=200")
        markets = data.get("markets", []) if data else []
        print(f"Odds API: {len(games)} games, Kalshi: {len(markets)} markets\n")

        matched_count = 0
        failed_count = 0
        for m in markets:
            ya = float(m.get("yes_ask_dollars", "0"))
            if ya < 0.05:
                continue
            title = m["title"]
            yes_sub = m.get("yes_sub_title", "")
            game = match_game(title, games)
            if not game:
                continue

            result = match_team(yes_sub, game["home_team"], game["away_team"])
            if "FAILED" in result:
                failed_count += 1
                print(f"  FAIL {m['ticker']}: ys='{yes_sub}' home={game['home_team']} away={game['away_team']}")
            else:
                matched_count += 1
                if matched_count <= 6:
                    print(f"  OK   {m['ticker']}: ys='{yes_sub}' -> {result} ({game['home_team']} vs {game['away_team']})")

        print(f"\n  Matched: {matched_count}, Failed: {failed_count}\n")

    # Same-city disambiguation
    print("=" * 60)
    print("SAME-CITY DISAMBIGUATION TEST")
    print("=" * 60)
    tests = [
        ("los angeles d", "Los Angeles Dodgers", "San Diego Padres", "HOME_SHORT"),
        ("san diego", "Los Angeles Dodgers", "San Diego Padres", "AWAY_CITY"),
        ("los angeles l", "Los Angeles Lakers", "Boston Celtics", "HOME_SHORT"),
        ("boston", "Los Angeles Lakers", "Boston Celtics", "AWAY_CITY"),
        ("new york m", "New York Mets", "St. Louis Cardinals", "HOME_SHORT"),
        ("st. louis", "New York Mets", "St. Louis Cardinals", "AWAY_CITY"),
        ("new york y", "New York Yankees", "Toronto Blue Jays", "HOME_SHORT"),
        ("chicago c", "Chicago Cubs", "Cleveland Guardians", "HOME_SHORT"),
        ("chicago ws", "Chicago White Sox", "Miami Marlins", "HOME_SHORT"),
        ("brooklyn", "Brooklyn Nets", "Charlotte Hornets", "HOME_SHORT"),
        ("new york r", "New York Rangers", "Columbus Blue Jackets", "HOME_SHORT"),
        ("new york i", "New York Islanders", "Buffalo Sabres", "HOME_SHORT"),
    ]
    all_pass = True
    for yes_sub, home, away, expected in tests:
        result = match_team(yes_sub, home, away)
        ok = result == expected
        if not ok:
            all_pass = False
        status = "OK" if ok else f"WRONG (got {result})"
        print(f"  ys='{yes_sub:20s}' home={home:25s} -> {result:15s} {status}")

    print(f"\n  All pass: {all_pass}")

    # Fee calc
    print(f"\n{'=' * 60}")
    print("FEE-ADJUSTED EDGE VERIFICATION")
    print("=" * 60)
    fee = c.KALSHI_FEE_PER_CONTRACT
    for desc, price, fair in [
        ("YES@50c fair=60%", 0.50, 0.60),
        ("NO@40c fair=50%", 0.40, 0.50),
        ("YES@10c fair=25%", 0.10, 0.25),
        ("NO@30c fair=55%", 0.30, 0.55),
        ("NO@80c fair=95%", 0.80, 0.95),
    ]:
        fp = min(fee, price, 1 - price) / price if price > 0 else 0
        gross = fair - price
        net = fair - price * (1 + fp)
        trade = "TRADE" if net > 0.08 else "SKIP"
        print(f"  {desc:20s} gross={gross:+.2f} fee%={fp:.1%} net={net:+.3f} -> {trade}")

    await http.aclose()


asyncio.run(main())
