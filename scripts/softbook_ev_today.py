#!/usr/bin/env python3
"""ACTIONABLE +EV bet list — what to bet RIGHT NOW on US-legal soft books.

Fetches current odds, computes each soft book's edge vs the sharp Pinnacle
no-vig line, and prints a clean 'place these bets' list (NY-legal books only,
realistic 1-6% EV band, with suggested stake). Pinnacle is the REFERENCE only
(US-unavailable) — you BET the listed soft book.

Usage: venv/bin/python scripts/softbook_ev_today.py [bankroll]
"""
import os, json, urllib.request, sys

# US-legal (NY) soft books you can actually bet at:
NY_LEGAL = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
            "caesars": "Caesars", "betrivers": "BetRivers", "espnbet": "ESPN BET",
            "fanatics": "Fanatics"}
SPORTS = ["baseball_mlb", "baseball_npb", "basketball_wnba", "baseball_kbo",
          "soccer_usa_mls", "icehockey_nhl", "soccer_uefa_champs_league"]
EV_MIN, EV_MAX = 0.01, 0.08   # bettable band; >8% is usually a stale/limited line


def _key():
    for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
        if line.startswith("ODDS_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("ODDS_API_KEY")


def get(sport, key):
    return json.loads(urllib.request.urlopen(
        f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
        f"?apiKey={key}&regions=us,eu&markets=h2h&oddsFormat=decimal", timeout=25).read())


def main():
    bankroll = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    key = _key()
    bets = []
    for sport in SPORTS:
        try:
            games = get(sport, key)
        except Exception:
            continue
        for g in games:
            bk = {b["key"]: b for b in g.get("bookmakers", [])}
            pin = bk.get("pinnacle")
            if not pin:
                continue
            try:
                po = {o["name"]: o["price"] for o in pin["markets"][0]["outcomes"]}
            except Exception:
                continue
            if len(po) != 2:
                continue
            inv = {k: 1.0 / v for k, v in po.items()}
            tot = sum(inv.values())
            fair = {k: inv[k] / tot for k in inv}
            for team in fair:
                for bkey, label in NY_LEGAL.items():
                    b = bk.get(bkey)
                    if not b:
                        continue
                    try:
                        so = {o["name"]: o["price"] for o in b["markets"][0]["outcomes"]}
                    except Exception:
                        continue
                    if team not in so:
                        continue
                    ev = fair[team] * so[team] - 1
                    if EV_MIN <= ev <= EV_MAX:
                        # quarter-Kelly stake (conservative), capped at 2% bankroll
                        edge = ev
                        kelly = edge / (so[team] - 1) if so[team] > 1 else 0
                        stake = min(bankroll * 0.02, bankroll * kelly * 0.25)
                        bets.append((ev, sport, team, label, so[team], fair[team], max(2.0, stake)))
    bets.sort(reverse=True)
    if not bets:
        print("No +EV bets in the 1-8% band right now. Re-run later (lines move).")
        return
    print(f"\n=== ACTIONABLE +EV BETS (bankroll ${bankroll:.0f}, sharp ref = Pinnacle no-vig) ===")
    print(f"{'EV':>5} | {'BET ON (book)':<12} | {'TEAM':<22} | {'odds':>5} | {'fair%':>5} | stake")
    for ev, sport, team, book, odds, fair, stake in bets:
        print(f"{ev*100:+4.1f}% | {book:<12} | {team[:22]:<22} | {odds:>5.2f} | {fair*100:4.0f}% | ${stake:.0f}")
    print(f"\n{len(bets)} bets. Place each at the named book. These are +EV (better than the sharp line);")
    print("you won't win every one — it's a long-run edge. Bet small, track results.")


if __name__ == "__main__":
    main()
