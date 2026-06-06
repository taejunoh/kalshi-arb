#!/usr/bin/env python3
"""+EV-betting (soft-book vs sharp line) forward CLV verification recorder.

The 6/05 exhaustive Kalshi sweep proved Kalshi is sharp (≈Pinnacle) → no edge
THERE. The documented real sports-betting edge is the OPPOSITE: SOFT books
(FanDuel/DraftKings/BetMGM/...) lag the sharp Pinnacle line, so betting a soft
book at better-than-sharp (no-vig) odds is +EV by construction. A current
snapshot shows ~8/58 game-sides +EV (mostly stale outliers; ~3 in the bettable
0.5-6% band at +3.5%).

This records, several times a day, every soft-book +EV opportunity vs the
Pinnacle no-vig line (the "open" line), so the measure script can later verify
CLOSING-LINE VALUE (did these +EV bets beat the Pinnacle CLOSE?) and realized
settlement EV. Positive CLV = the edge is real and capturable; ~0 CLV = the
divergences were stale noise that mean-reverted. NO BETTING — record only.

Run via kalshi-softbook-ev.timer. Reads ODDS_API_KEY from .env.
"""
import os, json, urllib.request, sqlite3, time
from datetime import datetime, timezone

OUT = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
SOFT = {"fanduel", "draftkings", "betmgm", "betrivers", "espnbet", "fanatics", "caesars", "bovada"}


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
    url = (f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
           f"?apiKey={key}&regions=us,eu&markets=h2h&oddsFormat=decimal")
    return json.loads(urllib.request.urlopen(url, timeout=25).read())


def soon(commence_time, max_hours=72):
    """Only near-term games — far-future lines (NFL months out) aren't sharp."""
    if not commence_time:
        return False
    try:
        gt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        hrs = (gt - datetime.now(timezone.utc)).total_seconds() / 3600
        return -6 <= hrs <= max_hours
    except Exception:
        return True


def init_db():
    c = sqlite3.connect(OUT)
    c.execute("""CREATE TABLE IF NOT EXISTS softbook_ev(
        recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
        team TEXT, opponent TEXT, pinnacle_fair_prob REAL, pinnacle_price REAL,
        best_soft_book TEXT, best_soft_price REAL, snapshot_ev REAL,
        UNIQUE(recorded_at, event_id, team))""")
    c.commit()
    return c


def main():
    key = _key()
    if not key:
        print("no ODDS_API_KEY"); return
    now = datetime.now(timezone.utc).isoformat()
    db = init_db()
    n = 0
    for sport in active_sports(key):
        try:
            games = get(sport, key)
        except Exception as e:
            print(f"{sport}: {e}"); continue
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
            names = list(fair)
            for team in names:
                opp = names[1] if names[0] == team else names[0]
                best_b, best_p, best_ev = None, None, -9
                for bkey, b in bk.items():
                    if bkey not in SOFT:
                        continue
                    try:
                        so = {o["name"]: o["price"] for o in b["markets"][0]["outcomes"]}
                    except Exception:
                        continue
                    if team in so:
                        ev = fair[team] * so[team] - 1
                        if ev > best_ev:
                            best_ev, best_b, best_p = ev, bkey, so[team]
                # Record the WIDE odds range on purpose (NO 1.50-4.00 betting cut
                # here): the bet list cuts extreme odds, but recording them lets
                # the CLV verdict prove, BY BAND, whether heavy-fav/longshot
                # signals are real edge or stale noise. Don't blind the evidence.
                if best_b and best_ev > 0.005:   # record only the bettable +EV side
                    try:
                        db.execute("INSERT OR IGNORE INTO softbook_ev VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (now, sport, g.get("id"), g.get("commence_time"),
                             team, opp, fair[team], po[team], best_b, best_p, best_ev))
                        n += 1
                    except Exception:
                        pass
        db.commit()
        time.sleep(0.3)
    print(f"[softbook-ev] {now}: recorded {n} +EV soft-book opportunities -> {OUT}")


if __name__ == "__main__":
    main()
