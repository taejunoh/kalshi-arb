#!/usr/bin/env python3
"""Stake-tiered promotion tracker for soft-book +EV candidate edges (2026-06-15).

Background: the 6/15 full re-measurement showed NO cell in the high-trigger ledger
clears the user's bar (long-run ROI - slippage - no-fill drag >= +1% on the CI
LOWER bound). The best *candidate* is late-crosser MLB batter total-bases
(in-sample +12.5%/n103) but ~90% of that came from one slate (6/14) and its CI
lower bound is negative. So we pre-register it OUT-OF-SAMPLE (bets placed on/after
2026-06-15, excluding the 6/14 slate) and let this tracker report, every daily
digest, where it stands against STAKE-TIERED gates:

  🟢 OBSERVE   ($2 floor, always while collecting)
  🟡 SMALL-ALLOC ready  (point-estimate gates: enough data + ROI-after-costs>+1%
                         + not one-day fluke + not one-game fluke)
  🔴 SIZE-UP ready      (the strict gate: day-clustered CI lower bound > +1%)
  ☠️ KILL               (OOS point ROI < 0 once n>=KILL_N)

Why tiered, not one hard gate: betting variance is huge (per-bet return sd ~1.1),
so requiring a 95% CI lower bound > +1% needs thousands of bets even for a real
+5% edge — correct for sizing UP real money, far too strict for "try a little".
We drop the redundant Wilson-WR gate (it tests the same "reliably positive" as the
CI gate); we KEEP single-day AND single-game concentration gates because they
catch a *different* failure mode (the 6/14 one-slate artifact). Thresholds are
module constants — tune freely.

  python scripts/softbook_tripwire.py              # print the report
  python scripts/softbook_tripwire.py --telegram   # also push it
Imported by softbook_ev_paper_pnl.py to append a line to the 13:00 UTC digest.
"""
import math
import os
import random
import sqlite3
import sys
import urllib.parse
import urllib.request

import softbook_ev_core as core

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")

# Pre-registration cutoff: only bets placed on/after this date count (out-of-sample;
# excludes the in-sample 6/14 slate that drove ~90% of the candidate's profit).
OOS_START = os.getenv("SB_TRIPWIRE_OOS", "2026-06-15")

HAIRCUT_PCT = float(os.getenv("SB_TRIPWIRE_HAIRCUT", "3.0"))  # slippage + no-fill drag, %pt off ROI
N_MIN = int(os.getenv("SB_TRIPWIRE_NMIN", "60"))             # min graded bets to judge
DAYS_MIN = int(os.getenv("SB_TRIPWIRE_DAYSMIN", "8"))        # min distinct commence-days
ROI_BAR = 1.0                                               # user's long-run bar (%)
DAY_SHARE_MAX = 0.40                                        # no single day > 40% of net profit
GAME_SHARE_MAX = 0.50                                       # no single game(event) > 50% of net
CI_BAR = 1.0                                               # size-up: CI lower bound must clear this
KILL_N = int(os.getenv("SB_TRIPWIRE_KILLN", "40"))          # n at which a negative OOS ROI = kill
BOOT_ITERS = 2000
BOOT_SEED = 12345                                          # fixed -> same data gives same verdict

# Candidate cohorts. [0] is PRIMARY (full gate breakdown); the rest are context.
COHORTS = [
    {"label": "LC MLB total-bases",
     "filter": lambda r: (not r["first_entry"]) and r["sport"] == "baseball_mlb"
                         and r["market"] == "batter_total_bases"},
    {"label": "LC MLB props(전체)",
     "filter": lambda r: (not r["first_entry"]) and r["sport"] == "baseball_mlb"
                         and r["market"] in core.ALL_PROPS},
    {"label": "First-entry 전체",
     "filter": lambda r: r["first_entry"]},
]


def send_telegram(text):
    """Self-contained sender (avoids a circular import with paper_pnl)."""
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("TELEGRAM_CHAT_ID="):
                cho = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return
    if not (tok and cho):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
    except Exception:
        pass


def _wilson_lb(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    return (p + z * z / (2 * n) - z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / (1 + z * z / n)


def load_classified(db):
    """All GRADED high-trigger bets, each tagged first_entry (matches the dashboard:
    a row is first-entry iff its (event,market,player) is in the paper_bet tier=High
    set — the +ROI-core ledger)."""
    fe = {(e, m, pl or "") for e, m, pl in db.execute(
        "SELECT DISTINCT event_id, market, COALESCE(player, '') FROM paper_bet WHERE tier='High'")}
    rows = []
    for (eid, sport, market, side, point, player, odds, stake, comp, won, pnl,
         placed, ct) in db.execute(
            "SELECT event_id, sport, market, side, point, COALESCE(player, ''), odds, stake, "
            "completed, won, pnl, placed_at, commence_time FROM paper_bet_high"):
        if not comp or won is None:                     # graded only (excludes void/push/pending)
            continue
        rows.append(dict(event_id=eid, sport=sport, market=market, side=side, point=point,
                         player=player, odds=odds, stake=stake, won=won, pnl=pnl,
                         placed_at=placed or "", commence_time=ct or "",
                         first_entry=((eid, market, player) in fe)))
    return rows


def _bootstrap_day_ci(byday):
    """Day-clustered bootstrap: resample whole commence-days with replacement (honors
    within-day correlation that naive iid bootstrap would ignore). Returns the 2.5th
    percentile of resampled ROI%. NaN if < 2 distinct days."""
    days = list(byday.values())                          # each = [net, staked]
    k = len(days)
    if k < 2:
        return float("nan")
    rng = random.Random(BOOT_SEED)
    rois = []
    for _ in range(BOOT_ITERS):
        s_net = s_st = 0.0
        for _ in range(k):
            d = days[rng.randrange(k)]
            s_net += d[0]
            s_st += d[1]
        rois.append(s_net / s_st * 100 if s_st else 0.0)
    rois.sort()
    return rois[int(0.025 * BOOT_ITERS)]


def cohort_stats(rows, filt, oos_start=None):
    oos = oos_start or OOS_START
    bets = [r for r in rows if filt(r) and r["placed_at"][:10] >= oos]
    n = len(bets)
    out = dict(n=n, days=0, wins=0, roi=0.0, roi_after=0.0, net=0.0, staked=0.0,
               ci_lo=float("nan"), ci_lo_after=float("nan"), day_share=1.0,
               game_share=1.0, wilson_lb=0.0, be_wr=0.0)
    if n == 0:
        return out
    staked = sum(b["stake"] for b in bets)
    net = sum(b["pnl"] for b in bets)
    wins = sum(b["won"] for b in bets)
    roi = net / staked * 100 if staked else 0.0
    byday, bygame = {}, {}
    for b in bets:
        d = (b["commence_time"][:10] or b["placed_at"][:10])
        a = byday.setdefault(d, [0.0, 0.0])
        a[0] += b["pnl"]
        a[1] += b["stake"]
        bygame[b["event_id"]] = bygame.get(b["event_id"], 0.0) + b["pnl"]
    ci_lo = _bootstrap_day_ci(byday)
    out.update(
        n=n, days=len(byday), wins=wins, roi=roi, roi_after=roi - HAIRCUT_PCT,
        net=net, staked=staked, ci_lo=ci_lo, ci_lo_after=ci_lo - HAIRCUT_PCT,
        day_share=(max(v[0] for v in byday.values()) / net) if net > 0 else 1.0,
        game_share=(max(bygame.values()) / net) if net > 0 else 1.0,
        wilson_lb=_wilson_lb(wins, n) * 100,
        be_wr=sum(1.0 / b["odds"] for b in bets) / n * 100)
    return out


def verdict(s):
    """(gates dict, tier string) from a cohort_stats result."""
    n, days = s["n"], s["days"]
    ci_ok = (s["ci_lo_after"] > CI_BAR) if s["ci_lo_after"] == s["ci_lo_after"] else False
    gates = {
        f"수집(≥{N_MIN}·{DAYS_MIN}일)": n >= N_MIN and days >= DAYS_MIN,
        "보정ROI>+1%": s["roi_after"] > ROI_BAR,
        "단일일<40%": s["day_share"] < DAY_SHARE_MAX,
        "단일경기<50%": s["game_share"] < GAME_SHARE_MAX,
        "CI하한>+1%": ci_ok,
    }
    point_gates = [v for k, v in gates.items() if k != "CI하한>+1%"]
    if n >= KILL_N and s["roi"] < 0:
        tier = "☠️ KILL — 모니터 제외 (OOS 점추정 음수)"
    elif all(gates.values()):
        tier = "🔴 증액 가능 — CI 하한까지 통과"
    elif all(point_gates):
        tier = "🟡 소액 승격 검토 — 점추정 게이트 통과 (CI는 아직)"
    elif n < N_MIN:
        tier = "🟢 관망 — 데이터 더 필요"
    else:
        tier = "🟢 관망 — 게이트 미충족"
    return gates, tier


def telegram_block(db, oos_start=None):
    oos = oos_start or OOS_START
    rows = load_classified(db)
    prim = COHORTS[0]
    lines = [f"🔬 트립와이어 ({oos}+ OOS · 슬리피지 -{HAIRCUT_PCT:.0f}%p 가정)"]
    s = cohort_stats(rows, prim["filter"], oos)
    if s["n"] == 0:
        lines.append(f"• {prim['label']}: 아직 0건 — 오늘부터 수집 시작")
    else:
        gates, tier = verdict(s)
        ci = f"{s['ci_lo_after']:+.1f}%" if s["ci_lo_after"] == s["ci_lo_after"] else "n/a"
        lines.append(
            f"• {prim['label']}: n={s['n']}/{N_MIN} · {s['days']}/{DAYS_MIN}일 · "
            f"ROI {s['roi']:+.1f}%(보정 {s['roi_after']:+.1f}%, CI하한 {ci})")
        lines.append("   " + "  ".join(
            f"{'✅' if v else '❌'}{k}" for k, v in gates.items()))
        lines.append("   → " + tier)
    ctx = []
    for c in COHORTS[1:]:
        cs = cohort_stats(rows, c["filter"], oos)
        ctx.append(f"{c['label']} n={cs['n']}"
                   + (f" ROI{cs['roi']:+.0f}%" if cs["n"] else ""))
    if ctx:
        lines.append("   참고 — " + " | ".join(ctx))
    return "\n".join(lines)


def main():
    db = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    block = telegram_block(db)
    print(block)
    if "--telegram" in sys.argv:
        send_telegram(block)


if __name__ == "__main__":
    main()
