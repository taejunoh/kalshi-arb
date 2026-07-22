#!/usr/bin/env python3
"""Post the daily KeepMyPick results card to X.

The card is a CREDIBILITY artifact, not a pick feed: it reports YESTERDAY's
already-graded picks — losses included — and deliberately carries **no
cumulative ROI%**. That number (+6.4% lifetime) is ~82% carried by picks whose
games start while a US reader is asleep, so headlining it would advertise a
record the reader cannot reproduce. Day-level facts only; the full ledger lives
on keepmypick.com.

Layout is GAME FIRST, then the picks on that game — a totals label ("Over 6.5")
names no game on its own, so a matchup-less card is unreadable (and two totals
on different games look like both sides of one game).

No link in the post body: X charges $0.20/post with a URL vs $0.015 without,
and the algorithm suppresses link posts. Site + 21+/not-advice disclaimers live
in the profile bio.

Usage (run on EC2, cwd anywhere — scripts/ is added to sys.path by its dir):
    venv/bin/python scripts/keepmypick_x_post.py [--day YYYY-MM-DD] [--live]
Dry-run unless --live (or X_POST_LIVE=1): composes and prints, posts nothing.
"""
import argparse
import datetime
import os
import sqlite3
import sys

EV_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")

# Eastern time: the audience is US, so "yesterday" is an ET calendar day.
ET = datetime.timezone(datetime.timedelta(hours=-4))
# One unit = the ledger's flat $2 stake, matching web/index.html's `UNIT`.
UNIT = 2.0
# X Premium raises the cap to 25k; we still truncate rather than fail a post.
MAX_CHARS = int(os.getenv("X_CARD_MAX_CHARS", "25000"))

MARK = {"win": "✅", "lose": "❌", "push": "➡️", "void": "➡️"}


def _american(dec):
    """Decimal -> the American number US books display (mirrors the app/site)."""
    if not dec or dec <= 1:
        return "-"
    a = round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))
    return "%+d" % a


def _pick_line(p):
    res = p.get("result")
    # push/void are spelled out: they are not wins, and hiding them would be a lie
    # by omission on the one artifact whose whole job is "nothing hidden".
    extra = " (%s)" % res if res in ("push", "void") else ""
    return "%s %s %s%s · %s" % (MARK.get(res, "•"), p["label"],
                                _american(p["odds"]), extra, p["book"])


def _assemble(header, blocks, footer, omitted):
    tail = footer if not omitted else "+%d more\n\n%s" % (omitted, footer)
    if not blocks:
        return "%s\n\n%s" % (header, tail)
    return "%s\n\n%s\n\n%s" % (header, "\n\n".join(blocks), tail)


def compose_card(rows, day, unit=UNIT, max_chars=MAX_CHARS):
    """rows: graded picks for `day` (dicts: event_id/away/home/label/book/odds/
    result/pnl), already ordered by start time. Returns the post text, or None
    when the day graded nothing (we stay silent rather than post an empty day).

    The Day line always totals ALL rows — truncation drops listed games, never
    changes the record.
    """
    if not rows:
        return None

    games = {}
    for r in rows:
        games.setdefault(r["event_id"], []).append(r)

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "lose")
    pnl = sum(r.get("pnl") or 0 for r in rows)

    header = "%s — graded" % day.strftime("%b %-d")
    footer = "Day: %d-%d  %+.2fu" % (w, l, pnl / unit)

    blocks = []
    for picks in games.values():
        p0 = picks[0]
        lines = ["%s @ %s" % (p0.get("away") or "?", p0.get("home") or "?")]
        lines += [_pick_line(p) for p in picks]
        blocks.append("\n".join(lines))

    for n in range(len(blocks), -1, -1):
        text = _assemble(header, blocks[:n], footer, len(blocks) - n)
        if len(text) <= max_chars:
            return text
    return _assemble(header, [], footer, len(blocks))


def fetch_day(day, paper_db=PAPER_DB, ev_db=EV_DB):
    """Graded first-entry GAME picks whose game STARTED on `day` (ET) — the same
    cohort the public site publishes (see keepmypick_results_export.py)."""
    import softbook_ev_core as core

    pp = sqlite3.connect(paper_db)
    ev = sqlite3.connect(ev_db)
    cache = {}

    def is_fe(eid, market, side, player):
        k = (eid, market, player or "")
        if k not in cache:
            try:
                cache[k] = core.market_first_entry_sides(ev, *k)
            except Exception:
                cache[k] = (False, frozenset())
        seen, sides = cache[k]
        return seen and (side in sides)

    out = []
    for (eid, away, home, ct, label, market, side, player, book, odds,
         result, pnl) in pp.execute(
            "SELECT event_id, away, home, commence_time, label, market, side,"
            " player, book, odds, result, pnl FROM paper_bet"
            " WHERE tier='High' AND result IS NOT NULL"
            " AND market IN ('h2h','spread','total') ORDER BY commence_time"):
        try:
            d = datetime.datetime.fromisoformat(
                (ct or "").replace("Z", "+00:00")).astimezone(ET).date()
        except Exception:
            continue
        if d != day or not is_fe(eid, market, side, player):
            continue
        out.append({"event_id": eid, "away": away, "home": home, "label": label,
                    "book": book, "odds": odds, "result": result, "pnl": pnl,
                    "commence_time": ct})
    return out


def post_to_x(text, send=None):
    """POST the card. Credentials come from the environment (EC2 .env), never
    from source. Raises on a failed send so systemd surfaces it in the journal."""
    import requests
    from requests_oauthlib import OAuth1

    auth = OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                  os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
    resp = (send or requests.post)(
        "https://api.twitter.com/2/tweets", auth=auth, json={"text": text},
        timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError("X post failed %s: %s" % (resp.status_code, resp.text[:300]))
    return resp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="ET date YYYY-MM-DD (default: yesterday ET)")
    ap.add_argument("--live", action="store_true",
                    help="actually post (default: dry-run, print only)")
    args = ap.parse_args(argv)

    if args.day:
        day = datetime.date.fromisoformat(args.day)
    else:
        day = (datetime.datetime.now(ET) - datetime.timedelta(days=1)).date()

    rows = fetch_day(day)
    text = compose_card(rows, day)
    if text is None:
        print("no graded picks for %s ET — nothing to post" % day)
        return 0

    live = args.live or os.getenv("X_POST_LIVE") == "1"
    print("--- card for %s ET (%d chars, %d picks) ---" % (day, len(text), len(rows)))
    print(text)
    if not live:
        print("--- DRY RUN (no post). Enable with --live or X_POST_LIVE=1 ---")
        return 0
    post_to_x(text)
    print("--- POSTED ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
