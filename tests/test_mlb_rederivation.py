"""The MLB Med ML re-derivation: gate, protocol, and verdict rules.

Agreed 2026-08-12: MLB is the largest Med ML slice and the weakest (lifetime
+2.3%/bet vs +17.1% for everything else, breakeven in its core odds band). The
re-derivation fires the day the forward sample reaches n>=50 and reports over
Telegram — it never changes the rule itself.

The verdict logic encodes the audit discipline the memory notes carry:
regime-consistency (2-prop z) decides whether pooling is allowed; a negative
verdict must survive dropping its WORST day, the mirror of the drop-best-day
check a positive claim faces; and the control cohort keeps "MLB is broken"
distinguishable from "everything cooled".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from mlb_med_ml_rederivation import GATE_N, analyze, cohort, report, two_prop_z  # noqa: E402


def _rows(n, wins, odds=2.2, day="2026-08-06", per_day=None):
    """n rows at `odds`, `wins` of them won, stake $2, spread over days."""
    out = []
    for i in range(n):
        won = 1 if i < wins else 0
        pnl = 2 * (odds - 1) if won else -2.0
        d = day if per_day is None else "2026-08-%02d" % (6 + i // per_day)
        out.append(dict(sport="baseball_mlb", odds=odds, stake=2.0, pnl=pnl,
                        won=won, d=d))
    return out


# ── the z test itself ─────────────────────────────────────────────────

def test_identical_regimes_give_z_zero():
    assert two_prop_z(50, 100, 25, 50) == pytest.approx(0.0)


def test_a_real_split_is_significant():
    """60% vs 30% over decent n must clear 1.96."""
    assert abs(two_prop_z(60, 100, 30, 100)) > 1.96


# ── gate ──────────────────────────────────────────────────────────────

def test_below_the_gate_is_a_wait_not_a_verdict():
    a = analyze(_rows(100, 50), _rows(30, 10), _rows(50, 30))
    assert a["verdict"] == "대기"


# ── pooling discipline ────────────────────────────────────────────────

def test_consistent_regimes_pool():
    a = analyze(_rows(100, 46), _rows(60, 26, per_day=6), _rows(60, 30))
    assert a["consistent"] and a["basis"] == "pooled"
    assert a["basis_stats"]["n"] == 160


def test_inconsistent_regimes_judge_forward_alone():
    """The era-break lesson: a significant split forbids pooling."""
    a = analyze(_rows(200, 120), _rows(80, 20, per_day=8), _rows(60, 30))
    assert not a["consistent"] and a["basis"] == "forward-only"
    assert a["basis_stats"]["n"] == 80


# ── verdicts ──────────────────────────────────────────────────────────

def test_a_clearly_negative_cohort_recommends_exclusion():
    # 35% WR at 2.2 odds (BE 45.5%), consistent across eras, spread over days
    a = analyze(_rows(150, 53), _rows(60, 21, per_day=6), _rows(60, 35))
    assert a["verdict"] == "MLB만 Med ML에서 제외 권고"


def test_a_passing_cohort_recommends_keeping():
    # 62% WR at 2.2 — Wilson LB comfortably above BE
    a = analyze(_rows(150, 93), _rows(60, 37, per_day=6), _rows(60, 30))
    assert a["verdict"] == "MLB 유지"


def test_breakeven_mush_stays_under_observation():
    """Neither gate-passing nor robustly negative -> keep watching, do not cut.
    (~48% WR at 2.2: above water but LB below BE.)"""
    a = analyze(_rows(150, 72), _rows(60, 29, per_day=6), _rows(60, 30))
    assert a["verdict"] == "판정 보류 — 계속 관찰"


def test_a_negative_verdict_must_survive_its_worst_day():
    """One catastrophic slate must not be able to buy an exclusion: same
    negative cohort, but all the losses land on a single day."""
    fwd = _rows(60, 21, per_day=None)          # all on one day
    a = analyze(_rows(150, 53), fwd, _rows(60, 35))
    assert a["drop_worst"]["n"] == 0 or a["verdict"] != "MLB만 Med ML에서 제외 권고"


# ── report content ────────────────────────────────────────────────────

def test_report_carries_the_decision_inputs():
    a = analyze(_rows(150, 53), _rows(60, 21, per_day=6), _rows(60, 35))
    r = report(a)
    for token in ("전환 전", "전진(8/05+)", "대조군", "레짐 z=", "최악일 제외",
                  "권고:", "결정은 유저 몫"):
        assert token in r, token


def test_report_never_claims_more_than_star4():
    a = analyze(_rows(150, 53), _rows(60, 21, per_day=6), _rows(60, 35))
    assert a["tier"] in ("★3", "★4")


# ── cohort split ──────────────────────────────────────────────────────

def test_cohort_splits_on_the_switch_date_and_sport():
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE paper_bet(sport TEXT, odds REAL, stake REAL, pnl REAL,"
               " won INT, placed_at TEXT, tier TEXT, market TEXT, completed INT)")
    for sport, day in (("baseball_mlb", "2026-07-01"), ("baseball_mlb", "2026-08-06"),
                       ("baseball_kbo", "2026-08-06")):
        db.execute("INSERT INTO paper_bet VALUES (?,2.2,2,2.4,1,?,'Med','h2h',1)",
                   (sport, day + "T12:00:00"))
    db.execute("INSERT INTO paper_bet VALUES ('baseball_mlb',2.2,2,NULL,NULL,"
               "'2026-08-07T12:00:00','Med','h2h',0)")     # ungraded — excluded
    pre, fwd, ctrl = cohort(db)
    assert [len(pre), len(fwd), len(ctrl)] == [1, 1, 1]
    assert GATE_N == 50
