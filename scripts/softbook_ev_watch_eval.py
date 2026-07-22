#!/usr/bin/env python3
"""Forward re-measure of the ML + EV>=5% watch cell (core.is_ev_watch), to Telegram.

Set up 2026-07-22 when the cell was flagged for observation instead of cut. The
in-sample case was strong but in-sample: 4-21, WR 16.0% vs BE 41.3%, ROI -60.8%,
spread over 18 days / 5 sports / both months. Cutting it would have taken the game
cohort from +6.4% to +16.6% — which is exactly the kind of number that does not
survive contact with new data, hence this script.

WHY THE FORWARD SAMPLE EXISTS AT ALL: core.is_bet was deliberately NOT changed, so
the recorder keeps booking these picks into paper_bet regardless of whether the user
actually places them. The flag only decorates the alert. If someone later "fixes"
that by gating is_bet, this measurement dies with it.

THE BAR (user, 2026-07-22): not a repeat of the in-sample ROI — n will be small over
three weeks, so ROI is noise-dominated. The question is whether WIN RATE STAYS UNDER
BREAKEVEN, i.e. does the direction hold. A cell that is genuinely mispriced-against-us
keeps losing; a cell that was a 25-bet accident regresses to ~BE.

Reads paper_bet (one bet per event+market = the first-entry ledger, the same table
the -4.5% ML figure came from), NOT paper_bet_high (exact-line keyed, a different
population). Anchored on placed_at: when the pick fired is what "forward" means.

Run:  softbook_ev_watch_eval.py [--telegram] [--window]
  --window : stay silent outside the reporting window (for the timer; a manual run
             without it always reports, so you can check progress early).
"""
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import softbook_ev_core as core

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
# The window agreed on 7/22: ~2 and ~3 weeks after the flag went live. The timer
# fires weekly, so this is exactly two reports; the second one closes the window.
FIRST_REPORT = os.getenv("EVWATCH_FIRST", "2026-08-05")
LAST_REPORT = os.getenv("EVWATCH_LAST", "2026-08-12")

# In-sample cohort the flag was built on (paper_bet, tier=High, h2h, ev>=0.05,
# through 2026-07-22) — quoted so the Telegram message is self-contained.
BASE_N, BASE_W, BASE_BE, BASE_ROI = 25, 4, 41.3, -60.8
MIN_N = 12                      # below this, direction is not readable at all


def send_telegram(text):
    """Self-contained sender (same pattern as softbook_tripwire — no import cycle)."""
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("TELEGRAM_CHAT_ID="):
                cho = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        return
    if not (tok and cho):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
    except Exception:
        pass


def cohort(db, since, until=None):
    """Graded ML picks at EV>=EV_WATCH_MIN placed on/after `since`.

    core.is_ev_watch is the single source for WHICH cell this is — the SQL only
    supplies the date bound, so a threshold change in core cannot silently
    desynchronise the alert from its own evaluation.
    """
    rows = db.execute(
        "SELECT market, ev, odds, stake, pnl, won, completed, substr(placed_at,1,10)"
        " FROM paper_bet WHERE tier='High' AND placed_at >= ?"
        + (" AND placed_at < ?" if until else ""),
        (since, until) if until else (since,)).fetchall()
    sel = [r for r in rows if core.is_ev_watch(r[0], r[1])]
    graded = [r for r in sel if r[6] and r[5] is not None]
    return graded, len(sel) - len(graded)


def stats(graded):
    n = len(graded)
    if not n:
        return None
    w = sum(r[5] for r in graded)
    st = sum(r[3] for r in graded)
    pnl = sum(r[4] for r in graded)
    return dict(n=n, w=w, l=n - w, wr=100 * w / n, st=st, pnl=pnl,
                roi=100 * pnl / st if st else 0.0,
                be=sum(1.0 / r[2] for r in graded) / n * 100,
                days=len({r[7] for r in graded}))


def verdict(s):
    """(headline, detail). Deliberately conservative: the default on thin data is
    'keep observing', never 'we proved it'."""
    if s is None or s["n"] < MIN_N:
        got = 0 if s is None else s["n"]
        return ("🟡 판정 보류 — 표본 부족",
                f"n={got}/{MIN_N}. 관찰 계속 (알림은 계속 🟡로 나감).")
    gap = s["wr"] - s["be"]
    if gap < -5:
        return ("🔴 병리 재현 — 하드 컷 검토",
                f"WR가 본전선보다 {abs(gap):.1f}%p 낮음. is_bet에서 이 셀 제외 검토.")
    if gap < 0:
        return ("🟠 약하게 재현 — 관찰 연장",
                f"WR가 본전선 아래지만 {abs(gap):.1f}%p차. 컷 근거로는 얇음.")
    return ("🟢 재현 안 됨 — 관찰 해제 검토",
            f"WR가 본전선을 {gap:.1f}%p 상회. in-sample 25건이 사고였을 가능성.")


def block(db, today):
    fwd, pending = cohort(db, core.EV_WATCH_SINCE)
    s = stats(fwd)
    head, detail = verdict(s)
    closing = today >= LAST_REPORT
    lines = [f"🔬 ML+EV≥5% 관찰 리포트 ({core.EV_WATCH_SINCE}~{today})",
             f"   기준선(in-sample, ~7/22): {BASE_W}-{BASE_N-BASE_W} "
             f"WR {100*BASE_W/BASE_N:.1f}% vs BE {BASE_BE}% · ROI {BASE_ROI:+.1f}%"]
    if s:
        lines += [f"• 관찰기간: n={s['n']} ({s['days']}일, 미정산 {pending})  "
                  f"{s['w']}-{s['l']}",
                  f"   WR {s['wr']:.1f}% vs BE {s['be']:.1f}%  ·  "
                  f"PnL ${s['pnl']:+.2f}  ROI {s['roi']:+.1f}%",
                  f"   보류로 아낀 금액: ${-s['pnl']:+.2f}" if s["pnl"] < 0 else
                  f"   보류로 놓친 금액: ${s['pnl']:+.2f}"]
    else:
        lines.append(f"• 관찰기간: 정산된 건 0 (미정산 {pending}) — 아직 판단 불가")
    lines += [f"→ {head}", f"   {detail}"]
    if closing:
        lines.append("⏰ 관찰 창(8/05~8/12) 종료 — 컷/해제 결정하고 이 타이머 끌 것:"
                     " sudo systemctl disable --now kalshi-softbook-evwatch.timer")
    return "\n".join(lines)


def main():
    today = os.getenv("EVWATCH_TODAY") or date.today().isoformat()
    if "--window" in sys.argv and not (FIRST_REPORT <= today <= LAST_REPORT):
        return                                   # timer fired outside the window
    db = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    text = block(db, today)
    print(text)
    if "--telegram" in sys.argv:
        send_telegram(text)


if __name__ == "__main__":
    main()
