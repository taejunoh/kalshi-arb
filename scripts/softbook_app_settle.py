#!/usr/bin/env python3
"""App-side settlement for app_bet (the companion app's real bets).

Reuses the same final-score / player-stat graders the paper ledgers use
(softbook_ev_paper_pnl.settle). Runs as its own timer — does NOT touch the
existing pipeline tables (paper_bet / paper_bet_high).

PnL uses fill_odds (the odds the user actually got at fill time).
Legacy rows migrated from real_fill (sport IS NULL) are skipped — can't grade
without sport/home/away.

A bet that cannot be graded is retried on the next run, forever, in silence.
That is right for a few hours and wrong after a few days, because both result
sources have a horizon: the Odds API /scores call asks for daysFrom=3, and the
ESPN/Naver graders behind softbook_results stop returning old fixtures too. Past
it, the bet is not "not settled yet", it is unsettleable — and it keeps its place
in every open-bet query. Found 2026-08-05: a KBO bet from 07-21 had been retried
every 15 minutes for 15 days, alongside a boxing and two WNBA bets from 07-17/18.

So anything still open STUCK_AFTER_H past its start gets reported once. It is not
auto-resolved: grading a bet from a guess would put a wrong number in a ledger of
real money, and the ledger rule is that rows are records, not estimates.
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


def unpriceable(stake, fill_odds):
    """A bet with no stake or no fill odds, which no result can settle.

    PnL is stake*(fill_odds-1) or -stake, so a row missing either has no PnL to
    compute however good the result is. Leaving the stake box blank in the app's
    CaptureSheet writes exactly such a row today (it posts `stake ? Number(stake)
    : undefined`, and BetIn.stake is `float | None`).

    Not coerced to 0: that writes a fabricated number into a ledger of real money
    and hides the corruption, and the ledger rule (softbook_app_db) is that
    app_bet rows are records — never repriced. So the row stays open and
    report_stuck asks the user for the missing value instead.
    """
    return stake is None or fill_odds is None


def settle_app_bets(db, key=None, fetch_scores=_fetch_scores,
                    player_stat=stats.player_stat):
    """Settle open app_bet rows using the same graders as the paper ledgers.

    Returns the count of rows settled in this call.
    fetch_scores and player_stat are injectable for testing.
    fetch_scores is called at most once per distinct sport.

    Every row is graded inside its own try/except. One row must not be able to
    end the run: there is a single db.commit() after the loop, so an escaping
    exception dropped every settlement in the batch AND skipped the report_stuck
    call in main() that is supposed to say something is wrong — every 15 minutes,
    in silence. A row that errors is left open and retried; if it keeps erroring
    it crosses the STUCK_AFTER_H horizon and report_stuck picks it up there.
    """
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player, home, away,"
        " commence_time, fill_odds, stake"
        " FROM app_bet WHERE settled=0 AND not_placed=0"
    ).fetchall()

    # cache scores per sport so we fetch each at most once
    scores_cache = {}
    res_cache = {}               # softbook_results per-(sport,date) fetch cache

    def grade(sport, event_id, market, side, point, player, home, away, commence_time):
        """'win'/'lose'/'void', or None if the result is not available yet."""
        if results.handles(sport):
            # tennis/KBO/NCAA/MMA -> ESPN/Naver (Odds API has no scores for these)
            return results.grade_bet(sport, home, away, commence_time, market, side,
                                     point, player or "", cache=res_cache)

        # fetch scores for this sport (cached)
        if sport not in scores_cache:
            scores_cache[sport] = fetch_scores(sport, key)
        scores = scores_cache[sport]

        if market in core.ALL_PROPS:
            # prop market -> resolve via player stat source
            stat = player_stat(sport, away, home, commence_time, market, player)
            return "void" if stat == stats.DNP else core.grade_prop(side, point, stat)

        sc = scores.get(event_id)
        if sc is None and sport == "baseball_npb":
            sc = npb.scores_for(away, home, commence_time)
        if not sc:
            return None
        if market == "team_total":
            # player column holds the team name for team totals
            return core.grade_prop(side, point, sc.get(player))
        # game market (h2h, spreads, totals) -> team scores
        return core.grade(market, side, point, home, away, sc)

    done = failed = 0
    for (row_id, sport, event_id, market, side, point, player,
         home, away, commence_time, fill_odds, stake) in rows:
        try:
            if not sport:
                continue      # legacy migrated row — no sport metadata to grade on
            if unpriceable(stake, fill_odds):
                continue      # no PnL to compute — report_stuck asks for the value

            res = grade(sport, event_id, market, side, point, player,
                        home, away, commence_time)
            if res is None:
                continue      # result not available yet — leave open

            won = 1 if res == "win" else (0 if res == "lose" else None)
            pnl = (stake * (fill_odds - 1) if res == "win"
                   else (-stake if res == "lose" else 0))
            db.execute(
                "UPDATE app_bet SET settled=1, won=?, pnl=?, settled_at=? WHERE id=?",
                (won, round(pnl, 2), datetime.now(timezone.utc).isoformat(), row_id))
            done += 1
        except Exception as e:                     # one row must not end the run
            failed += 1
            print(f"[softbook-app-settle] bet {row_id} errored, left open: {e!r}")

    db.commit()
    if failed:
        print(f"[softbook-app-settle] {failed} bet(s) errored this run")
    return done


STUCK_AFTER_H = 72          # both result sources are dry by ~3 days
_STUCK_STATE = "/home/ubuntu/kalshi-arb/data/.app_settle_stuck"


HORIZON = "horizon"              # outran both result sources
UNPRICEABLE = "unpriceable"      # no stake / no fill odds to compute PnL from


def stuck_bets(db, hours=STUCK_AFTER_H):
    """Open bets that retrying will never settle, tagged with which kind.

    HORIZON      the game started more than `hours` ago, past every result
                 source's window. The user has to grade it themselves.
    UNPRICEABLE  stake or fill_odds is NULL (see unpriceable()). The user has to
                 supply the missing value. Deliberately NOT on the horizon clock:
                 it is true the moment the row is written, and waiting three days
                 to ask means asking after they have stopped remembering what
                 they staked — while every settle run skips the row in silence.
    """
    rows = [dict(zip(("id", "sport", "label", "book", "stake", "fill_odds",
                      "commence_time"), r))
            for r in db.execute(
                "SELECT id, sport, label, book, stake, fill_odds, commence_time"
                " FROM app_bet"
                " WHERE settled=0 AND not_placed=0 AND sport IS NOT NULL"
                "   AND (stake IS NULL OR fill_odds IS NULL"
                "        OR (commence_time IS NOT NULL"
                "            AND datetime(replace(commence_time,'Z',''))"
                "                < datetime('now', ?)))"
                " ORDER BY commence_time", (f"-{int(hours)} hours",))]
    for r in rows:
        r["reason"] = (UNPRICEABLE if unpriceable(r["stake"], r["fill_odds"])
                       else HORIZON)
    return rows


_COPY = {
    UNPRICEABLE: ("스테이크/체결 배당 누락",
                  "기록에 스테이크나 체결 배당이 비어 있어 손익을 계산할 수 없습니다"
                  " — 앱에서 값을 채워 주세요."),
    HORIZON: ("시작 후 {hours}시간 경과",
              "결과 소스 조회 한계를 넘겼습니다 — 앱에서 직접 정산해 주세요."),
}


def _stuck_line(r):
    # a missing stake is shown as missing, never as $0 — the fabricated number is
    # the whole thing the settle-side guard exists to keep out of the ledger
    stake = "스테이크 미입력" if r["stake"] is None else f"${r['stake']:.0f}"
    return (f"   • {r['label']} ({r['sport']}) {r['book']} {stake}"
            f" — {(r['commence_time'] or '')[:10]}")


def _stuck_message(group, reason, hours):
    head, remedy = _COPY[reason]
    lines = "\n".join(_stuck_line(r) for r in group[:10])
    return (f"⚠️ 정산 불가 {len(group)}건 ({head.format(hours=hours)})\n\n"
            f"{lines}\n\n{remedy}")


def report_stuck(db, notify=None, state_path=_STUCK_STATE, hours=STUCK_AFTER_H):
    """Report newly-stuck bets once each. Returns the ids reported.

    Once each: this runs every 15 minutes and the condition never clears on its
    own, so reporting every time would be a permanent alarm — which is the same
    as no alarm.
    """
    rows = stuck_bets(db, hours)
    if not rows:
        return []
    try:
        with open(state_path) as f:
            seen = {int(x) for x in f.read().split() if x.strip()}
    except (OSError, ValueError):
        seen = set()
    fresh = [r for r in rows if r["id"] not in seen]
    if not fresh:
        return []
    # one message per reason: the remedies differ (type in the missing value vs
    # grade it yourself), and telling the user a bet placed an hour ago outran
    # the result sources would be a false diagnosis
    send = notify or _notify
    for reason in (UNPRICEABLE, HORIZON):
        group = [r for r in fresh if r["reason"] == reason]
        if group:
            send(_stuck_message(group, reason, hours))
    try:
        with open(state_path, "w") as f:
            f.write(" ".join(str(r["id"]) for r in rows))
    except OSError:
        pass                      # reporting already happened; a lost state file
    return [r["id"] for r in fresh]   # just means one repeat, not a missed alert


def _notify(msg):
    import softbook_ev_recorder as rec
    rec.send_telegram(msg)


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY")
        return
    db = adb.connect()
    n = None
    try:
        n = settle_app_bets(db, key)
    except Exception as e:
        # report_stuck is the one channel that tells the user something is wrong,
        # so it must not go down with the failure it exists to surface
        print(f"[softbook-app-settle] settle failed: {e!r}")
    stuck = report_stuck(db)
    msg = f"settled {n} app bets" if n is not None else "settle FAILED (see above)"
    print(f"[softbook-app-settle] {msg}"
          + (f", reported {len(stuck)} unsettleable" if stuck else ""))
    db.close()


if __name__ == "__main__":
    main()
