"""Check actual edge distribution across all sports markets."""
import asyncio
import httpx
from core.config import Config
from core.kalshi_client import KalshiClient
from strategies.sports_trader import ODDS_TO_KALSHI_SHORT

c = Config()
k = KalshiClient(c)
fee = c.KALSHI_FEE_PER_CONTRACT


async def check():
    http = httpx.AsyncClient(timeout=10)

    print("SPORTS: actual net edge distribution")
    print("=" * 50)
    edges = []

    sport_map = {
        "KXNBAGAME": "basketball_nba",
        "KXMLBGAME": "baseball_mlb",
        "KXNHLGAME": "icehockey_nhl",
    }

    for series, sport in sport_map.items():
        resp = await http.get(
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
            params={"apiKey": c.ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"},
        )
        games = resp.json()

        data = k._get(f"/trade-api/v2/markets?status=open&series_ticker={series}&limit=200")
        markets = data.get("markets", []) if data else []

        for m in markets:
            ya = float(m.get("yes_ask_dollars", "0"))
            na = float(m.get("no_ask_dollars", "0"))
            if ya < 0.05 or na < 0.05:
                continue

            title = m["title"].lower()
            ys = m.get("yes_sub_title", "").lower()

            matched = None
            for g in games:
                home = g["home_team"].lower()
                away = g["away_team"].lower()
                hc = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
                ac = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
                if hc in title and ac in title:
                    matched = g
                    break
            if not matched:
                continue

            home_team = matched["home_team"]
            away_team = matched["away_team"]
            hp, ap = [], []
            for bm in matched.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    for o in mkt.get("outcomes", []):
                        odds = o.get("price", 0)
                        if odds <= 1:
                            continue
                        prob = 1.0 / odds
                        if o["name"] == home_team:
                            hp.append(prob)
                        elif o["name"] == away_team:
                            ap.append(prob)
            if not hp or not ap:
                continue
            ah = sum(hp) / len(hp)
            aa = sum(ap) / len(ap)
            t = ah + aa
            fair_h = ah / t
            fair_a = aa / t

            hs = ODDS_TO_KALSHI_SHORT.get(home_team.lower(), "")
            as_ = ODDS_TO_KALSHI_SHORT.get(away_team.lower(), "")
            hc = " ".join(home_team.lower().split()[:-1])
            ac = " ".join(away_team.lower().split()[:-1])

            if (hs and hs == ys) or (hc in ys or ys in hc):
                fp = fair_h
            elif (as_ and as_ == ys) or (ac in ys or ys in ac):
                fp = fair_a
            else:
                continue

            yfp = min(fee, ya, 1 - ya) / ya if ya > 0 else 0
            nfp = min(fee, na, 1 - na) / na if na > 0 else 0
            ye = fp - ya * (1 + yfp)
            ne = (1 - fp) - na * (1 + nfp)
            best_net = max(ye, ne)
            best_gross = max(fp - ya, (1 - fp) - na)
            side = "YES" if ye >= ne else "NO"
            edges.append((best_net, best_gross, m["ticker"], fp, ya, na, side))

    edges.sort(key=lambda x: x[0], reverse=True)
    print(f"Total markets with odds match: {len(edges)}")
    print(f"\nTop 20 by net edge:")
    for net, gross, ticker, fp, ya, na, side in edges[:20]:
        print(f"  {ticker:45s} fair={fp:.2f} ya={ya:.2f} na={na:.2f} gross={gross:+.2f} net={net:+.3f} {side}")

    print(f"\nEdge distribution:")
    for threshold in [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
        count = sum(1 for e in edges if e[0] > threshold)
        print(f"  net > {threshold:.0%}: {count} trades would execute")

    await http.aclose()


asyncio.run(check())
