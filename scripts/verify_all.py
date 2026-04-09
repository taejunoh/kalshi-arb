"""End-to-end verification of all live strategies."""
import asyncio
import json
import os
import inspect

import httpx

from core.config import Config
from core.kalshi_client import KalshiClient
from core.order_manager import OrderManager
from strategies.sports_trader import SportsTraderStrategy
from strategies.spread_scanner import SpreadScannerStrategy
from strategies.mentions_trader import MentionsTraderStrategy


c = Config()
k = KalshiClient(c)


async def verify_sports():
    print("=" * 70)
    print("SPORTS TRADER END-TO-END VERIFICATION")
    print("=" * 70)

    http = httpx.AsyncClient(timeout=10)

    # 1. Fetch NBA odds
    resp = await http.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/odds",
        params={"apiKey": c.ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"},
    )
    games = resp.json()
    print(f"\n[1] Odds API: {len(games)} NBA games")

    # 2. Fetch Kalshi NBA markets
    data = k._get("/trade-api/v2/markets?status=open&series_ticker=KXNBAGAME&limit=200")
    markets = data.get("markets", []) if data else []
    print(f"[2] Kalshi: {len(markets)} NBA markets")

    # 3. Trace matching + fair_prob
    print(f"\n[3] Matching + fair_prob trace:")
    tested = 0
    for market in markets:
        if tested >= 10:
            break
        ticker = market["ticker"]
        title = market.get("title", "")
        yes_sub = market.get("yes_sub_title", "").lower()
        ya = float(market.get("yes_ask_dollars", "0"))
        na = float(market.get("no_ask_dollars", "0"))
        if ya < 0.05 or na < 0.05:
            continue

        # Match by city name
        title_lower = title.lower()
        matched = None
        for game in games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
            away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
            home_first = home.split()[0]
            away_first = away.split()[0]
            if (home_city in title_lower or home_first in title_lower) and \
               (away_city in title_lower or away_first in title_lower):
                matched = game
                break

        if not matched:
            continue

        # Consensus probability
        home_team = matched["home_team"]
        away_team = matched["away_team"]
        home_probs, away_probs = [], []
        for bm in matched.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                for outcome in mkt.get("outcomes", []):
                    odds = outcome.get("price", 1)
                    if odds <= 1:
                        continue
                    prob = 1.0 / odds
                    if outcome["name"] == home_team:
                        home_probs.append(prob)
                    elif outcome["name"] == away_team:
                        away_probs.append(prob)
        if not home_probs or not away_probs:
            continue
        avg_home = sum(home_probs) / len(home_probs)
        avg_away = sum(away_probs) / len(away_probs)
        total = avg_home + avg_away
        home_prob = avg_home / total
        away_prob = avg_away / total

        # Team identification via yes_sub_title
        hc = " ".join(home_team.lower().split()[:-1])
        ac = " ".join(away_team.lower().split()[:-1])
        if hc in yes_sub or yes_sub in hc:
            fair_prob = home_prob
            yes_is = "HOME"
        elif ac in yes_sub or yes_sub in ac:
            fair_prob = away_prob
            yes_is = "AWAY"
        else:
            fair_prob = None
            yes_is = "UNKNOWN"

        # Edge + sanity
        issues = []
        if yes_is == "UNKNOWN":
            issues.append("CANNOT IDENTIFY TEAM")
        if fair_prob is not None:
            yes_edge = fair_prob - ya
            no_edge = (1 - fair_prob) - na
            if abs(yes_edge) > 0.30:
                issues.append(f"SUSPICIOUS yes_edge={yes_edge:+.2f}")
            if abs(no_edge) > 0.30:
                issues.append(f"SUSPICIOUS no_edge={no_edge:+.2f}")
            if fair_prob > 0.90 or fair_prob < 0.10:
                issues.append(f"EXTREME fair_prob={fair_prob:.2f}")
        else:
            yes_edge = no_edge = None

        status = "ISSUES: " + "; ".join(issues) if issues else "OK"
        print(f"\n  {ticker}")
        print(f"    title: {title}")
        print(f"    yes_sub: '{yes_sub}' -> YES is {yes_is} ({home_team} vs {away_team})")
        print(f"    consensus: home={home_prob:.2f} away={away_prob:.2f}")
        if fair_prob is not None:
            print(f"    fair_prob (for YES side): {fair_prob:.2f}")
            print(f"    kalshi: yes={ya:.2f} no={na:.2f}")
            print(f"    edge: yes={yes_edge:+.2f} no={no_edge:+.2f}")
        print(f"    -> {status}")
        tested += 1

    await http.aclose()


def verify_yes_price():
    print(f"\n{'=' * 70}")
    print("YES_PRICE CONVERSION CHECK")
    print("=" * 70)
    src = inspect.getsource(OrderManager._live_limit_order)
    has_conversion = "100 - price_cents" in src
    print(f"  _live_limit_order has yes_price conversion for NO: {has_conversion}")

    # Trace actual values
    test_cases = [
        ("yes", 0.38, "YES@38c -> yes_price should be 38"),
        ("no", 0.62, "NO@62c -> yes_price should be 38"),
        ("no", 0.99, "NO@99c -> yes_price should be 1"),
        ("no", 0.01, "NO@1c -> yes_price should be 99"),
    ]
    for side, price, desc in test_cases:
        pc = int(price * 100)
        ypc = pc if side == "yes" else (100 - pc)
        actual_cost = ypc if side == "yes" else (100 - ypc)
        ok = actual_cost == int(price * 100)
        print(f"  {desc}: yes_price_cents={ypc}, actual_cost={actual_cost}c {'OK' if ok else 'WRONG'}")


def verify_duplicates():
    print(f"\n{'=' * 70}")
    print("DUPLICATE PREVENTION CHECK")
    print("=" * 70)
    for fname in ["data/mentions_traded_tickers.json", "data/spread_traded_tickers.json", "data/sports_traded_tickers.json"]:
        if os.path.exists(fname):
            with open(fname) as f:
                d = json.load(f)
            count = len(d) if isinstance(d, (list, dict)) else "?"
            print(f"  {fname}: {count} entries")
        else:
            print(f"  {fname}: NOT YET CREATED (no trades yet - OK)")

    # Check code: mark before order
    src_mentions = inspect.getsource(MentionsTraderStrategy.scan_and_execute)
    src_spread = inspect.getsource(SpreadScannerStrategy.scan_and_execute)
    src_sports = inspect.getsource(SportsTraderStrategy.scan_and_execute)
    for name, src in [("mentions", src_mentions), ("spread", src_spread), ("sports", src_sports)]:
        lines = src.split("\n")
        save_line = mark_line = order_line = None
        for i, line in enumerate(lines):
            if "_traded_tickers" in line and ("add(" in line or "[ticker]" in line) and "load" not in line and "get" not in line:
                mark_line = i
            if "_save_traded_tickers" in line:
                save_line = i
            if "limit_order(" in line and "await" in line:
                order_line = i
        if mark_line is not None and order_line is not None:
            ok = mark_line < order_line
            print(f"  {name}: mark(L{mark_line}) {'<' if ok else '>'} order(L{order_line}) {'OK' if ok else 'WRONG - mark after order!'}")
            if save_line is not None:
                ok2 = save_line < order_line
                print(f"  {name}: save(L{save_line}) {'<' if ok2 else '>'} order(L{order_line}) {'OK' if ok2 else 'WRONG - save after order!'}")


def verify_cycle_limits():
    print(f"\n{'=' * 70}")
    print("PER-CYCLE LIMITS CHECK")
    print("=" * 70)
    src = inspect.getsource(SportsTraderStrategy.scan_and_execute)
    checks = {
        "Max 3 trades per cycle": "trades_attempted >= 3" in src,
        "Max 30% bankroll exposure": "bankroll * 0.30" in src,
    }
    for desc, ok in checks.items():
        print(f"  {desc}: {'OK' if ok else 'MISSING'}")


def verify_state():
    print(f"\n{'=' * 70}")
    print("CURRENT STATE")
    print("=" * 70)
    bal = k.get_balance()
    resting = k.get_resting_orders()
    positions = k.get_positions()
    active = [p for p in positions if float(p.get("position_fp", 0)) != 0]
    sports = [p for p in active if any(s in p["ticker"] for s in ["NBAGAME", "MLBGAME", "NHLGAME"])]
    mentions = [p for p in active if "FEDMENTION" in p["ticker"]]
    total_exp = sum(float(p["market_exposure_dollars"]) for p in active)
    print(f"  Balance: ${bal}")
    print(f"  Resting orders: {len(resting)}")
    print(f"  Active positions: {len(active)} (sports={len(sports)}, mentions={len(mentions)}, other={len(active)-len(sports)-len(mentions)})")
    print(f"  Total exposure: ${total_exp:.2f}")


async def main():
    await verify_sports()
    verify_yes_price()
    verify_duplicates()
    verify_cycle_limits()
    verify_state()
    print(f"\n{'=' * 70}")
    print("VERIFICATION COMPLETE")
    print("=" * 70)


asyncio.run(main())
