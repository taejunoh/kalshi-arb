#!/usr/bin/env python3
"""PRE-REGISTERED test: is Med ML's edge grand-slam-only?

Registered 2026-08-31, BEFORE the US Open produced a single settled pick — the
rotation that starts the feed landed the same day (PR #100). The thresholds
below are frozen as of that date. That ordering is the whole point: on
2026-08-24 the Med ML re-measure found the tier's entire statistical
significance came from Wimbledon (n=74, ROI +44.4%, CI [+16.1, +72.8]) and that
NOTHING else cleared zero — pooled ex-Wimbledon n=686, +6.73%, CI [-2.15,
+15.62]. So "grand slams carry it" is the standing hypothesis and the US Open is
its first out-of-sample test.

The failure mode this file exists to prevent: reading the US Open result and
THEN deciding what counts as a pass. That is how the 08-05 High->Med promotion
went wrong (the rule was fitted to the data it was then scored on), and this
session found three more filters that died the moment they were tested honestly
(per-sport tier rules, an EV>=6% cut, a lead-time cut). Thresholds chosen in
advance are the only defence.

    PRIMARY   realised ROI on settled US Open Med ML first-entry picks
      >= +25%  -> grand-slam hypothesis SUPPORTED
      <  +10%  -> REJECTED; Wimbledon was noise, and ex-Wimbledon Med ML does
                  not clear zero, so demotion goes on the table
      between  -> UNDECIDED; hold, re-test at the Australian Open
      n < 40   -> INSUFFICIENT regardless of ROI (Wimbledon was n=74)

Why those numbers: per-bet SD is ~120%, so at n~74 the standard error on ROI is
~14pp. A true +44% edge clears +25% with high probability; a true 0% edge
reaches it rarely. The thresholds are one SE either side of the midpoint — this
test can separate "Wimbledon-sized edge" from "nothing", and deliberately
refuses to adjudicate anything finer.

SECONDARY (mechanism, reported but NOT decisive on its own): closing EV
= close_fair * soft_price - 1. Registered now so it cannot be promoted to
primary after the fact if it happens to read better. If the two disagree, the
PRIMARY verdict stands and the disagreement itself gets written up — closing EV
is the tighter measure but it is not what the Wimbledon claim was made in.
See memory softbook_clv_confound_closing_ev_2026-08-24 for why the older
fair-drift CLV is NOT used here: it shares open_fair with entry EV and
manufactures a monotone relationship out of nothing.

Usage (EC2, cwd=scripts):  ../venv/bin/python usopen_med_ml_prereg.py
"""
import sqlite3, os, statistics, random, math
from collections import defaultdict

import softbook_ev_core as core
from softbook_ev_measure import first_entry_map

# --- FROZEN 2026-08-31. Changing any of these after data exists is moving the
# goalposts; test_usopen_prereg_frozen.py fails if they drift, so the diff has
# to say so out loud. ---
REGISTERED_ON = "2026-08-31"
SPORTS = ("tennis_atp_us_open", "tennis_wta_us_open")
MIN_N = 40
PASS_ROI = 0.25
FAIL_ROI = 0.10

EV_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")


def _ci(xs, seed=7, nb=3000):
    rnd = random.Random(seed)
    b = sorted(statistics.mean([xs[rnd.randrange(len(xs))] for _ in xs]) for _ in range(nb))
    return b[int(.025 * nb)], b[int(.975 * nb)]


def bet_keys(ev):
    """Join keys for first-entry US Open picks at their market's bet tier."""
    rows = ev.execute("""SELECT event_id, market, side, point, player, recorded_at,
        pinnacle_fair_prob, best_soft_price, tier FROM softbook_ev
        WHERE sport IN (?, ?)""", SPORTS).fetchall()
    if not rows:
        return {}, {}
    g = defaultdict(list)
    for eid, m, s, p, pl, ts, f, px, t in rows:
        g[(eid, m, s, p, pl)].append((ts, f, px, t))
    fe = first_entry_map(rows)
    close = {}
    for e, m, s, p, pl, cf in ev.execute(
            "SELECT event_id, market, side, point, player, close_fair FROM closing_line"):
        close[(e, m, s, p or 0, pl or "")] = cf
    keys, cev = {}, {}
    for k, tr in g.items():
        tr.sort()
        if k[1] != "h2h":
            continue
        if not core.is_bet(k[2] in fe.get((k[0], k[1], k[4] or ""), frozenset()),
                           k[1], tr[0][3]):
            continue
        ck = (k[0], k[1], k[2], k[3] or 0, k[4] or "")
        keys[ck] = tr[0][3]
        cf_ = close.get(ck)
        if cf_ and tr[0][2]:
            cev[ck] = cf_ * tr[0][2] - 1.0        # TRUE close only — no fallback
    return keys, cev


def main():
    ev = sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True)
    keys, cev = bet_keys(ev)
    pp = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    rs = []
    for e, m, s, p, pl, pnl, stake in pp.execute(
            """SELECT event_id, market, side, point, player, pnl, stake
               FROM paper_bet WHERE market='h2h' AND sport IN (?, ?)
                 AND completed=1 AND won IS NOT NULL AND stake > 0""", SPORTS):
        ck = (e, m, s, p or 0, pl or "")
        if ck in keys:
            rs.append((ck, pnl, stake))

    print(f"PRE-REGISTERED {REGISTERED_ON} — US Open Med ML grand-slam test")
    print(f"  기준: n>={MIN_N} 필요 | ROI>={PASS_ROI:.0%} 지지 | "
          f"ROI<{FAIL_ROI:.0%} 기각 | 사이는 보류")
    print(f"  후보 픽(미정산 포함): {len(keys)}    정산 완료: {len(rs)}")

    if len(rs) < MIN_N:
        print(f"\n  판정: INSUFFICIENT — 정산 {len(rs)} < {MIN_N}. 대회 종료 후 재실행.")
        return

    r = [pnl / stake for _, pnl, stake in rs]
    roi = sum(p for _, p, _ in rs) / sum(s for _, _, s in rs)
    lo, hi = _ci(r)
    print(f"\n  PRIMARY  실현 ROI = {roi*100:+.1f}%  95%CI [{lo*100:+.1f}, {hi*100:+.1f}]"
          f"  (n={len(rs)})")
    verdict = ("SUPPORTED — 그랜드슬램 가설 지지" if roi >= PASS_ROI else
               "REJECTED — 윔블던은 노이즈였다. 강등 검토" if roi < FAIL_ROI else
               "UNDECIDED — 보류, 호주오픈에서 재시험")
    print(f"  판정: {verdict}")

    sec = [cev[ck] for ck, _, _ in rs if ck in cev]
    if len(sec) >= 20:
        slo, shi = _ci(sec)
        print(f"\n  SECONDARY 종가EV = {statistics.mean(sec)*100:+.2f}%  "
              f"95%CI [{slo*100:+.2f}, {shi*100:+.2f}]  (n={len(sec)}, TRUE 종가만)")
        print(f"  {'독립 지지' if slo > 0 else '독립 지지 없음'} — 참고용, 판정은 PRIMARY 가 한다.")
    else:
        print(f"\n  SECONDARY 종가EV: n={len(sec)} 표본 부족")


if __name__ == "__main__":
    main()
