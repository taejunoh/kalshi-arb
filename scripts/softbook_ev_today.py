#!/usr/bin/env python3
"""ACTIONABLE +EV bet list — what to bet RIGHT NOW on US-legal soft books.

Fetches current odds, computes each soft book's edge vs the sharp Pinnacle
no-vig line, and prints a clean 'place these bets' list (NY-legal books only,
realistic 1-6% EV band, with suggested stake). Pinnacle is the REFERENCE only
(US-unavailable) — you BET the listed soft book.

Usage: venv/bin/python scripts/softbook_ev_today.py [bankroll]
"""
import os, json, urllib.request, sys

# NY-legal soft books you can actually bet at (verified: NY's licensed mobile
# operators). ESPN BET / Penn is NOT licensed in NY -> excluded. Fanatics holds
# PointsBet's former NY license -> included.
NY_LEGAL = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
            "caesars": "Caesars", "betrivers": "BetRivers", "fanatics": "Fanatics"}
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


def send_telegram(text):
    """Push the bet list to the user's phone (uses the bot's existing creds)."""
    import urllib.parse
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="): tok = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("TELEGRAM_CHAT_ID="): cho = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return
    if not (tok and cho):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
    except Exception:
        pass


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
    # GROUP by game: same game across books = ONE bet. Show ALL +EV books per
    # game (best first) so you can line-shop at bet time, but BET ONLY ONE.
    from collections import defaultdict
    games = defaultdict(list)
    stakes = {}
    for ev, sport, team, book, odds, fair, stake in bets:
        games[(sport, team)].append((ev, book, odds))
        stakes[(sport, team)] = (fair, stake)
    ordered = sorted(games.items(), key=lambda kv: -max(o[0] for o in kv[1]))
    want_tg = "--telegram" in sys.argv
    if not ordered:
        print("No +EV bets in the 1-8% band right now. Re-run later (lines move).")
        return
    print(f"\n=== +EV BETS ({len(ordered)} games, bankroll ${bankroll:.0f}, ref=Pinnacle no-vig) ===")
    print("Each game = ONE bet. Books listed best-first; take whichever is best when you bet.\n")
    lines = []
    for (sport, team), offers in ordered:
        offers.sort(reverse=True)
        fair, stake = stakes[(sport, team)]
        opts = "  ".join(f"{bk} {od:.2f}({ev*100:+.1f}%)" for ev, bk, od in offers)
        row = f"• {team[:24]:<24} fair {fair*100:.0f}% | ${stake:.0f} | {opts}"
        print(row)
        lines.append(f"• {team[:24]} (fair {fair*100:.0f}%, ${stake:.0f}) — bet ONE:\n   " +
                     " / ".join(f"{bk} {od:.2f} {ev*100:+.1f}%" for ev, bk, od in offers))
    print("\nBet each GAME once (pick the best book that's still up). Different games = bet all you want.")
    print("Long-run edge — you won't win every one. Bet small.")
    if want_tg:
        msg = ("🎯 +EV bets — each game = ONE bet (pick best book still up):\n\n" +
               "\n".join(lines) +
               "\n\nDifferent games = bet all. Same game = pick one. +EV vs sharp line, bet small.")
        send_telegram(msg)


if __name__ == "__main__":
    main()
