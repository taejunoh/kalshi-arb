#!/usr/bin/env python3
"""ACTIONABLE +EV bet list — what to bet RIGHT NOW on US-legal soft books.

Fetches current odds, computes each soft book's edge vs the sharp Pinnacle
no-vig line, and prints a clean 'place these bets' list (NY-legal books only,
realistic 1-6% EV band, with suggested stake). Pinnacle is the REFERENCE only
(US-unavailable) — you BET the listed soft book.

Usage: venv/bin/python scripts/softbook_ev_today.py [bankroll]
"""
import os, json, urllib.request, sys
from datetime import datetime, timezone

MAX_HOURS = 72   # only near-term games — the sharp line is only "true" close to start.


def soon(commence_time):
    """True if the game starts within the next MAX_HOURS (and isn't long over).
    Far-future lines (e.g. NFL games months out) aren't sharp -> not real +EV."""
    if not commence_time:
        return False
    try:
        gt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        hrs = (gt - datetime.now(timezone.utc)).total_seconds() / 3600
        return -6 <= hrs <= MAX_HOURS
    except Exception:
        return True

# NY-legal soft books you can actually bet at (verified: NY's licensed mobile
# operators). ESPN BET / Penn is NOT licensed in NY -> excluded. Fanatics holds
# PointsBet's former NY license -> included.
NY_LEGAL = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
            "caesars": "Caesars", "betrivers": "BetRivers", "fanatics": "Fanatics"}
EV_MIN, EV_MAX = 0.01, 0.08   # bettable band; >8% is usually a stale/limited line


def active_sports(key):
    """Every in-season h2h sport on the Odds API right now (auto-adapts to seasons)."""
    try:
        d = json.loads(urllib.request.urlopen(
            f"https://api.the-odds-api.com/v4/sports/?apiKey={key}", timeout=20).read())
        return [s["key"] for s in d if s.get("active") and not s.get("has_outrights")]
    except Exception:
        return ["baseball_mlb", "baseball_npb", "baseball_kbo", "basketball_wnba"]


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
    commence = {}
    for sport in active_sports(key):
        try:
            games = get(sport, key)
        except Exception:
            continue
        for g in games:
            if not soon(g.get("commence_time")):
                continue
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
                        commence[(sport, team)] = g.get("commence_time")
    # GROUP by game: same game across books = ONE bet. Show ALL +EV books per
    # game (best first) so you can line-shop at bet time, but BET ONLY ONE.
    from collections import defaultdict
    games = defaultdict(list)
    stakes = {}
    for ev, sport, team, book, odds, fair, stake in bets:
        games[(sport, team)].append((ev, book, odds))
        stakes[(sport, team)] = (fair, stake)
    ordered = sorted(games.items(), key=lambda kv: -max(o[0] for o in kv[1]))

    # --json PATH : write the bet list for the dashboard (writes even if empty)
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else "data/ev_bets_latest.json"
        out = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "bankroll": bankroll, "bets": []}
        for (sport, team), offers in ordered:
            offers.sort(reverse=True)
            fair, stake = stakes[(sport, team)]
            out["bets"].append({
                "team": team, "sport": sport.replace("_", " ").upper(),
                "commence_time": commence.get((sport, team)),
                "fair_prob": round(fair, 4), "stake": round(stake, 2),
                "best_ev": offers[0][0],
                "books": [{"book": bk, "odds": od, "ev": ev} for ev, bk, od in offers],
            })
        try:
            with open(path, "w") as f:
                json.dump(out, f)
            print(f"wrote {len(out['bets'])} bets -> {path}")
        except Exception as e:
            print("json write err:", e)

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
