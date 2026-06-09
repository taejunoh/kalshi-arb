#!/usr/bin/env python3
"""ACTIONABLE +EV bet list — what to bet RIGHT NOW on US-legal soft books.

FULL version: scans MONEYLINE, SPREAD and TOTAL (Over/Under) across every active
in-season sport, comparing each soft-book line to Pinnacle's sharp price AT THE
SAME LINE (via Pinnacle's alternate-line ladder; see softbook_ev_core.py). Prints
a clean 'place these bets' list — NY-legal books only, pre-game (<=72h), odds
1.50-4.00, EV 1-8%, one bet per (game, market). Pinnacle is the REFERENCE only
(US-unavailable) — you BET the listed soft book.

Usage: venv/bin/python scripts/softbook_ev_today.py [bankroll] [--json PATH] [--telegram]
"""
import os, json, sys, urllib.request, urllib.parse
from datetime import datetime, timezone
from collections import defaultdict
from softbook_ev_core import read_key, active_sports, scan_sport

JSON_CAP, TG_CAP = 40, 12   # dashboard / Telegram show top-N by EV (props can be many)
PROP_PER_GAME = 3           # cap props per game (correlated — don't stack a slate)


def cap_props_per_game(bets, n=PROP_PER_GAME):
    """Keep all game-market bets but only the top-n props per game (props in one
    game are correlated — a slugfest makes the Overs hit/miss together)."""
    seen, out = defaultdict(int), []
    for b in bets:                       # bets are pre-sorted by -ev
        if b["market"] in ("h2h", "spread", "total"):
            out.append(b)
        else:
            g = (b["away"], b["home"])
            if seen[g] < n:
                out.append(b); seen[g] += 1
    return out


def stake_for(ev, odds, bankroll):
    kelly = ev / (odds - 1) if odds > 1 else 0
    return round(max(2.0, min(bankroll * 0.02, bankroll * kelly * 0.25)), 2)


def send_telegram(text):
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
    bankroll = 240.0
    for a in sys.argv[1:]:
        try:
            bankroll = float(a); break
        except ValueError:
            continue
    key = read_key()

    bets, credits = [], 0
    for sport in active_sports(key):
        try:
            sb, c, _ = scan_sport(sport, key)
        except Exception:
            continue
        credits += c
        bets += sb
    bets.sort(key=lambda b: -b["ev"])
    bets_all = list(bets)              # uncapped: the CLV research set
    bets = cap_props_per_game(bets)
    for b in bets:
        b["stake"] = stake_for(b["ev"], b["odds"], bankroll)

    # Persist THIS scan as the canonical record: the dashboard json, the ev.db CLV
    # snapshots and the instant High alerts all come from the SAME scan, so what
    # the +EV tab shows is exactly what gets recorded/alerted (no two-scan drift —
    # tier flickers at the Med/High boundary made tab and ledger disagree before).
    try:
        import softbook_ev_recorder as rec
        now_iso = datetime.now(timezone.utc).isoformat()
        rdb = rec.init_db()
        rn = rec.record(rdb, bets_all, now_iso)
        ra = rec.alert_new_high(rdb, bets_all, now_iso)
        print(f"[record] {rn} snapshot rows, {ra} new High alerts")
    except Exception as e:
        print("record err:", e)

    # --json : dashboard feed (one entry per +EV market bet; keeps legacy fields)
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else "data/ev_bets_latest.json"
        out = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "bankroll": bankroll, "api_credits": credits, "bets": []}
        for b in bets[:JSON_CAP]:
            out["bets"].append({
                "team": b["label"], "matchup": f"{b['away']} @ {b['home']}",
                "sport": b["sport"].replace("_", " ").upper(),
                "market": b["market"], "side": b["side"], "point": b["point"],
                "player": b.get("player"),
                "commence_time": b["commence_time"],
                "fair_prob": round(b["fair"], 4), "stake": b["stake"],
                "best_ev": b["ev"],
                "n_books": b["n_books"], "n_ev": b["n_ev"],
                "conf": b["conf"], "tier": b["tier"],
                "books": [{"book": b["book"], "odds": b["odds"], "ev": b["ev"]}],
            })
        try:
            with open(path, "w") as f:
                json.dump(out, f)
            print(f"wrote {len(out['bets'])} bets -> {path} ({credits} credits)")
        except Exception as e:
            print("json write err:", e)

    if not bets:
        print(f"No +EV bets (pre-game, 1.50-4.00, 1-8%) right now. ({credits} credits) Re-run later.")
        return

    # group by game for readable display
    games = defaultdict(list)
    for b in bets:
        games[(b["away"], b["home"], b["commence_time"])].append(b)
    print(f"\n=== +EV BETS — {len(bets)} across {len(games)} games "
          f"(bankroll ${bankroll:.0f}, ref=Pinnacle, {credits} credits) ===")
    print("ML/spread/total. Each line = one bet (best book). Same market = pick one.\n")
    for (away, home, ct), gb in sorted(games.items(), key=lambda kv: -max(b["ev"] for b in kv[1])):
        gb.sort(key=lambda b: -b["ev"])
        print(f"• {away} @ {home}")
        for b in gb:
            print(f"    {b['market']:<8} {b['label']:<26} {b['book']:<11} "
                  f"{b['odds']:.2f}  (fair {b['fair']*100:.0f}%, EV {b['ev']*100:+.1f}%)  ${b['stake']:.0f}")
    print("\nLong-run edge — bet small, one per market. Different games/markets = diversify.")

    if "--telegram" in sys.argv:
        top = bets[:TG_CAP]
        tg = defaultdict(list)
        for b in top:
            tg[(b["away"], b["home"])].append(b)
        lines = []
        for (away, home), gb in sorted(tg.items(), key=lambda kv: -max(b["ev"] for b in kv[1])):
            gb.sort(key=lambda b: -b["ev"])
            lines.append(f"• {away[:16]} @ {home[:16]}\n" + "\n".join(
                f"   [{b['tier'][0]}] {b['label']} — {b['book']} {b['odds']:.2f} "
                f"({b['ev']*100:+.1f}%, {b['n_ev']}/{b['n_books']}bk) ${b['stake']:.0f}" for b in gb))
        more = len(bets) - len(top)
        msg = ("🎯 +EV bets (top by EV — ML/spread/total/props), one per market:\n\n"
               + "\n\n".join(lines)
               + (f"\n\n…+{more} more on dashboard." if more > 0 else "")
               + "\n\n+EV vs sharp Pinnacle. Bet small, pre-game only.")
        send_telegram(msg)


if __name__ == "__main__":
    main()
