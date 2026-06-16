#!/usr/bin/env python3
"""App-side settlement for app_bet (the companion app's real bets).

Reuses the same final-score / player-stat graders the paper ledgers use
(softbook_ev_paper_pnl.settle). Runs as its own timer — does NOT touch the
existing pipeline tables (paper_bet / paper_bet_high).

PnL uses fill_odds (the odds the user actually got at fill time).
Legacy rows migrated from real_fill (sport IS NULL) are skipped — can't grade
without sport/home/away.
"""
import os
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

import softbook_ev_core as core
import softbook_ev_stats as stats
import softbook_ev_npb as npb
import softbook_app_db as adb
import softbook_results as results


def _fetch_scores(sport, key):
    """{event_id: {team_name: score}} for COMPLETED games (Odds API /scores)."""
    out = {}
    try:
        games = json.loads(urllib.request.urlopen(
            f"{core.B}/{sport}/scores/?apiKey={key}&daysFrom=3", timeout=25).read())
    except Exception:
        return out
    for g in games:
        if not g.get("completed"):
            continue
        sc = {}
        for s in (g.get("scores") or []):
            try:
                sc[s["name"]] = float(s["score"]) if s["score"] is not None else None
            except (TypeError, ValueError):
                sc[s["name"]] = None
        if len(sc) >= 2 and all(v is not None for v in sc.values()):
            out[g["id"]] = sc
    return out


def settle_app_bets(db, key=None, fetch_scores=_fetch_scores,
                    player_stat=stats.player_stat):
    """Settle open app_bet rows using the same graders as the paper ledgers.

    Returns the count of rows settled in this call.
    fetch_scores and player_stat are injectable for testing.
    fetch_scores is called at most once per distinct sport.
    """
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player, home, away,"
        " commence_time, fill_odds, stake"
        " FROM app_bet WHERE settled=0"
    ).fetchall()

    # cache scores per sport so we fetch each at most once
    scores_cache = {}
    res_cache = {}               # softbook_results per-(sport,date) fetch cache
    done = 0

    for (row_id, sport, event_id, market, side, point, player,
         home, away, commence_time, fill_odds, stake) in rows:

        # skip legacy migrated rows that have no sport metadata
        if not sport:
            continue

        res = None
        if results.handles(sport):
            # tennis/KBO/NCAA/MMA -> ESPN/Naver (Odds API has no scores for these)
            res = results.grade_bet(sport, home, away, commence_time, market, side,
                                    point, player or "", cache=res_cache)
            if res is None:
                continue
            won = 1 if res == "win" else (0 if res == "lose" else None)
            pnl = (stake * (fill_odds - 1) if res == "win"
                   else (-stake if res == "lose" else 0))
            db.execute(
                "UPDATE app_bet SET settled=1, won=?, pnl=?, settled_at=? WHERE id=?",
                (won, round(pnl, 2), datetime.now(timezone.utc).isoformat(), row_id))
            done += 1
            continue

        # fetch scores for this sport (cached)
        if sport not in scores_cache:
            scores_cache[sport] = fetch_scores(sport, key)
        scores = scores_cache[sport]

        if market in core.ALL_PROPS:
            # prop market -> resolve via player stat source
            stat = player_stat(sport, away, home, commence_time, market, player)
            res = "void" if stat == stats.DNP else core.grade_prop(side, point, stat)
        elif market == "team_total":
            # player column holds the team name for team totals
            sc = scores.get(event_id)
            if sc is None and sport == "baseball_npb":
                sc = npb.scores_for(away, home, commence_time)
            res = core.grade_prop(side, point, sc.get(player)) if sc else None
        else:
            # game market (h2h, spreads, totals) -> team scores
            sc = scores.get(event_id)
            if sc is None and sport == "baseball_npb":
                sc = npb.scores_for(away, home, commence_time)
            res = core.grade(market, side, point, home, away, sc) if sc else None

        if res is None:
            continue  # result not available yet — leave open

        won = 1 if res == "win" else (0 if res == "lose" else None)
        pnl = (stake * (fill_odds - 1) if res == "win"
               else (-stake if res == "lose" else 0))

        db.execute(
            "UPDATE app_bet SET settled=1, won=?, pnl=?, settled_at=? WHERE id=?",
            (won, round(pnl, 2), datetime.now(timezone.utc).isoformat(), row_id))
        done += 1

    db.commit()
    return done


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY")
        return
    db = adb.connect()
    n = settle_app_bets(db, key)
    print(f"[softbook-app-settle] settled {n} app bets")
    db.close()


if __name__ == "__main__":
    main()
