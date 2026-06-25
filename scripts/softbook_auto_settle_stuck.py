#!/usr/bin/env python3
"""Auto-settle stuck past-dated +EV paper bets via Gemini (Google Search grounding).

The regular settler (softbook_ev_paper_pnl.settle) handles feed sports (Odds API
/scores) and ESPN/Naver sports (softbook_results). What it CAN'T settle lingers as
completed=0 until void_stale flips it after SOFTBOOK_STALE_DAYS=4 — fine for games
that were genuinely not played (rain/abandon/withdrawal -> void anyway), but WRONG
for boxing (no data feed at all), whose real W/L would be mis-voided. The user asked
this be handled automatically rather than by manual request (2026-06-23).

This runs daily on a timer, finds GAME-market bets (h2h/spread/total) still pending
>= MIN_HOURS after start, web-verifies each event's result with Gemini (google_search
grounding — free tier, no pay-as-you-go API), and settles HIGH-CONFIDENCE results via
the SAME grade convention as the pipeline. Anything uncertain is left pending
(stale-void remains the backstop). Conservative by design: game markets only,
confidence gate, capped per run, every decision logged + a Telegram summary.

Usage:
  python scripts/softbook_auto_settle_stuck.py            # verify + settle + telegram
  python scripts/softbook_auto_settle_stuck.py --dry-run  # verify + print, NO writes
"""
import json
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import softbook_ev_core as core

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
LEDGERS = ("paper_bet", "paper_bet_high")
GAME_MARKETS = ("h2h", "spread", "total")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = os.getenv("AUTOSETTLE_GEMINI_MODEL", "gemini-2.5-flash")   # free tier + grounding
MIN_HOURS = float(os.getenv("AUTOSETTLE_MIN_HOURS", "18"))   # pipeline has long since given up
MAX_EVENTS = int(os.getenv("AUTOSETTLE_MAX_EVENTS", "25"))   # bound cost per run


# ---- Gemini (google_search grounding) -------------------------------------------

def _gemini_key():
    if os.getenv("GEMINI_API_KEY"):
        return os.getenv("GEMINI_API_KEY")
    try:                                # fall back to .env (same pattern as core.read_key)
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def gemini_verify(prompt, model=MODEL, key=None):
    """One grounded Gemini call. Returns the concatenated answer text (raises on
    HTTP / API error). google_search grounding lets it look up the real result."""
    key = key or _gemini_key()
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={urllib.parse.quote(key)}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=70).read())
    if "error" in resp:
        raise RuntimeError(f"gemini {resp['error'].get('code')}: {str(resp['error'].get('message'))[:120]}")
    c = (resp.get("candidates") or [{}])[0]
    return "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))


_SCHEMA_HINT = (
    "Return ONLY a JSON object (no prose, no markdown fence) with EXACTLY these keys:\n"
    '  "status": one of "final" | "not_played"   '
    "(not_played = postponed/cancelled/abandoned/walkover/no-contest, OR the named "
    "matchup did not occur, e.g. a player withdrew pre-match and was replaced)\n"
    '  "winner": the EXACT winner name — must be either the home or away name given '
    "below, or null for a draw / void / unknown\n"
    '  "home_score": final numeric score for the HOME side (runs/goals; tennis = total '
    "games won by that player), or null\n"
    '  "away_score": same for the AWAY side, or null\n'
    '  "retired": true if a tennis match ended in mid-match retirement (RET), else false\n'
    '  "confidence": "high" | "medium" | "low"  (use "high" ONLY if you found the result '
    "from a reliable source — and the exact score when a score is requested)\n"
    '  "sources": array of source URLs you used\n'
)


def verify_event(ev, verify=None):
    """Web-verify one event's final result. Returns a parsed dict (schema above) or
    None on failure. `verify(prompt)->text` is injectable for tests."""
    verify = verify or gemini_verify
    need_score = any(b["market"] in ("spread", "total") for b in ev["bets"])
    prompt = (
        "You are settling a sports betting ledger. Use Google Search to find the FINAL "
        "result of this completed event. Cross-check independent sources (ESPN, official "
        "league sites, Wikipedia, BoxRec for boxing, flashscore/sofascore, news).\n\n"
        f"SPORT: {ev['sport']}\n"
        f"DATE (UTC): {ev['commence_time']}\n"
        f"HOME: {ev['home']}\nAWAY: {ev['away']}\n\n"
        + ("This settles spread/total bets, so the EXACT final score matters.\n\n" if need_score else "")
        + _SCHEMA_HINT
    )
    return _extract_json(verify(prompt))


def _extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)   # last balanced {...}, fence-tolerant
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


# ---- grading (same convention as softbook_ev_paper_pnl.settle) -------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")        # fold accents: Jović -> Jovic
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def grade_bet(market, side, point, home, away, res):
    """-> 'win' | 'lose' | 'void' | None (None = can't settle, leave pending).
    h2h settles by WINNER (robust to tennis upset-by-games / no team scores);
    spread/total settle via core.grade on the per-side scores; not_played / RET void."""
    status = res.get("status")
    if status == "not_played":
        return "void"
    if status != "final":
        return None
    if market == "h2h":
        w = res.get("winner")
        if not w:
            return "void"                     # draw / void
        nw, ns, nh, na = _norm(w), _norm(side), _norm(home), _norm(away)
        if nw not in (nh, na):                # winner must be one of the two listed sides
            return None                       # ambiguous -> leave pending
        return "win" if nw == ns else "lose"
    # spread / total need numeric scores
    if res.get("retired"):                    # tennis RET voids spread/total
        return "void"
    hs, as_ = res.get("home_score"), res.get("away_score")
    if hs is None or as_ is None:
        return None
    return core.grade(market, side, point, home, away, {home: hs, away: as_})


# ---- db io -----------------------------------------------------------------------

def stuck_events(db, min_hours=MIN_HOURS):
    """Game-market bets still pending >= min_hours after start, grouped by event."""
    events = {}
    for t in LEDGERS:
        rows = db.execute(
            f"""SELECT rowid, event_id, sport, home, away, market, side, point, player,
                       label, odds, stake, commence_time
                FROM {t}
                WHERE COALESCE(completed,0)=0
                  AND market IN ({','.join('?' * len(GAME_MARKETS))})
                  AND datetime(commence_time) < datetime('now', ?)
                ORDER BY commence_time""",
            (*GAME_MARKETS, f"-{int(min_hours)} hours")).fetchall()
        for (rid, eid, sport, home, away, market, side, point, player,
             label, odds, stake, ct) in rows:
            ev = events.setdefault(eid, {
                "event_id": eid, "sport": sport, "home": home, "away": away,
                "commence_time": ct, "bets": []})
            ev["bets"].append({"table": t, "rowid": rid, "market": market, "side": side,
                               "point": point, "player": player, "label": label,
                               "odds": odds, "stake": stake})
    return list(events.values())


def settle_row(db, table, rowid, res_str, odds, stake, now):
    won = 1 if res_str == "win" else (0 if res_str == "lose" else None)
    pnl = stake * (odds - 1) if res_str == "win" else (-stake if res_str == "lose" else 0)
    db.execute(
        f"UPDATE {table} SET completed=1, won=?, result=?, pnl=?, settled_at=? WHERE rowid=?",
        (won, res_str, round(pnl, 2), now, rowid))
    return round(pnl, 2)


# ---- telegram --------------------------------------------------------------------

def send_telegram(text):
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("TELEGRAM_CHAT_ID="):
                cho = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return
    if not (tok and cho):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage",
                               data=data, timeout=10).read()
    except Exception:
        pass


# ---- main ------------------------------------------------------------------------

def main(verify=None):
    dry = "--dry-run" in sys.argv
    db = sqlite3.connect(PAPER_DB)
    events = stuck_events(db)
    if not events:
        print("[auto-settle] no stuck game-market bets")
        return
    events = events[:MAX_EVENTS]
    if verify is None:
        if not _gemini_key():
            print(f"[auto-settle] {len(events)} stuck event(s) but no GEMINI_API_KEY in "
                  f"env/.env — skipping. Add the key to activate.")
            return
        verify = gemini_verify
    now = datetime.now(timezone.utc).isoformat()
    settled, skipped, lines, net = 0, 0, [], 0.0
    for ev in events:
        try:
            res = verify_event(ev, verify=verify)
        except Exception as e:
            print(f"  verify err {ev['event_id'][:8]} {ev['away']}@{ev['home']}: {e}")
            skipped += len(ev["bets"]); continue
        conf = (res or {}).get("confidence")
        if not res or conf != "high":
            print(f"  SKIP {ev['away']}@{ev['home']} (conf={conf}, status={(res or {}).get('status')})")
            skipped += len(ev["bets"]); continue
        for b in ev["bets"]:
            r = grade_bet(b["market"], b["side"], b["point"], ev["home"], ev["away"], res)
            if r is None:
                print(f"  SKIP {b['label']} (ungradeable)"); skipped += 1; continue
            pnl = (0.0 if dry else
                   settle_row(db, b["table"], b["rowid"], r, b["odds"], b["stake"], now))
            if r != "void" and not dry:
                net += pnl
            print(f"  {'DRY ' if dry else ''}{b['label']:<30} -> {r:<5} "
                  f"({(res or {}).get('status')}, {conf})")
            lines.append(f"{b['label']} → {r}")
            settled += 1
    if not dry:
        db.commit()
    print(f"[auto-settle] {'DRY-RUN ' if dry else ''}settled {settled}, skipped {skipped}, "
          f"net ${net:+.2f} across {len(events)} events")
    if settled and not dry:
        send_telegram("🤖 자동 정산 (stuck 과거 베팅, Gemini 웹검증):\n" + "\n".join(lines[:20])
                      + (f"\n…+{len(lines)-20}" if len(lines) > 20 else "")
                      + f"\n순손익 ${net:+.2f}")


if __name__ == "__main__":
    main()
