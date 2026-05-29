"""Phase 0/1 watchlist daily check — 2026-05-21.

Three sub-cohorts identified during 2026-05-21 evening crypto observer
audit that were INTERESTING but NOT promotable (single-day concentration,
small n, or post-config-change too-fresh). Each needs multi-day
confirmation before action.

Run any time to see current state:
    python3 scripts/phase01_watchlist.py

Or on EC2:
    ssh ec2 "/home/ubuntu/kalshi-arb/venv/bin/python /home/ubuntu/kalshi-arb/scripts/phase01_watchlist.py"

Items:

  1. lognormal_fair_dev × KXETHD × predicted=yes × |dev|≥0.20
     Promote if 7d net stays > 0 AND each of last 3 days is > 0.

  2. bid_imbalance × KXBTCD × ya ∈ [0.50, 0.70]
     Promote when n ≥ 50 AND WR ≥ 75% AND net > 0.

  3. cme_basis_fade_v1 paper post-J-config (commit 1b534b5, ts >= 2026-05-21 16:11 UTC)
     Confirm J cap is biting: post-J fires should all be no_ask < 0.85
     AND per-event fire count <= 8. Net should turn positive vs pre-J.

Read-only on the DB.
"""
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = os.getenv("BOT_DB_PATH") or (
    "/home/ubuntu/kalshi-arb/data/bot.db"
    if Path("/home/ubuntu/kalshi-arb/data/bot.db").exists()
    else str(Path(__file__).resolve().parent.parent / "data" / "bot.db")
)
J_CONFIG_DEPLOY_TS = "2026-05-21T16:11:31"


def _connect() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        sys.exit(f"DB not found: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _print_header(title: str) -> None:
    print()
    print("─" * 72)
    print(title)
    print("─" * 72)


def _print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = rows[0].keys()
    widths = [max(len(c), max(len(str(r[c])) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


def cohort_a_lognormal_fair_dev_kxethd(con: sqlite3.Connection) -> None:
    _print_header(
        "[1] lognormal_fair_dev × KXETHD × predicted=yes × |dev|≥0.20 — day-by-day"
    )
    rows = con.execute(
        """
        WITH s AS (
          SELECT substr(ts, 1, 10) AS day,
            CAST(json_extract(features_json, '$.ya') AS REAL) AS ya,
            CAST(json_extract(features_json, '$.deviation') AS REAL) AS dev,
            actual_outcome
          FROM crypto_observer_signals
          WHERE observer = 'lognormal_fair_dev'
            AND ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')
            AND series = 'KXETHD'
            AND predicted_outcome = 'yes'
            AND actual_outcome IN ('yes', 'no')
            AND ABS(CAST(json_extract(features_json, '$.deviation') AS REAL)) >= 0.20
        )
        SELECT day,
          COUNT(*) AS n,
          ROUND(100.0 * SUM(CASE WHEN actual_outcome = 'yes' THEN 1 ELSE 0 END)
                / COUNT(*), 1) AS wr,
          ROUND(AVG(ya), 3) AS avg_ya,
          ROUND(SUM(CASE WHEN actual_outcome = 'yes' THEN (1 - ya - 0.07)
                         ELSE (-ya - 0.07) END), 2) AS net_ct,
          ROUND(AVG(CASE WHEN actual_outcome = 'yes' THEN (1 - ya - 0.07)
                         ELSE (-ya - 0.07) END), 3) AS avg_pnl_ct
        FROM s GROUP BY day ORDER BY day;
        """
    ).fetchall()
    _print_rows(rows)
    print(
        "Decision rule: promote when 7d net > 0 AND each of last 3 days > 0.\n"
        "Watch days < 0 closely — re-check whether |dev|≥0.20 still correct cut."
    )


def cohort_b_bid_imbalance_kxbtcd(con: sqlite3.Connection) -> None:
    _print_header(
        "[2] bid_imbalance × KXBTCD × ya ∈ [0.50, 0.70] — cumulative + last 3d"
    )
    overall = con.execute(
        """
        WITH s AS (
          SELECT substr(ts, 1, 10) AS day,
            CAST(json_extract(features_json, '$.ya') AS REAL) AS ya,
            actual_outcome
          FROM crypto_observer_signals
          WHERE observer = 'bid_imbalance'
            AND ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')
            AND series = 'KXBTCD'
            AND predicted_outcome = 'yes'
            AND actual_outcome IN ('yes', 'no')
            AND CAST(json_extract(features_json, '$.ya') AS REAL) BETWEEN 0.50 AND 0.70
        )
        SELECT 'cumulative_14d' AS bucket, COUNT(*) AS n,
          ROUND(100.0 * SUM(CASE WHEN actual_outcome = 'yes' THEN 1 ELSE 0 END)
                / COUNT(*), 1) AS wr,
          ROUND(AVG(ya), 3) AS avg_ya,
          ROUND(SUM(CASE WHEN actual_outcome = 'yes' THEN (1 - ya - 0.07)
                         ELSE (-ya - 0.07) END), 2) AS net_ct,
          ROUND(AVG(CASE WHEN actual_outcome = 'yes' THEN (1 - ya - 0.07)
                         ELSE (-ya - 0.07) END), 3) AS avg_pnl_ct
        FROM s;
        """
    ).fetchall()
    _print_rows(overall)
    print()
    print("Last 3 days:")
    daily = con.execute(
        """
        WITH s AS (
          SELECT substr(ts, 1, 10) AS day,
            CAST(json_extract(features_json, '$.ya') AS REAL) AS ya,
            actual_outcome
          FROM crypto_observer_signals
          WHERE observer = 'bid_imbalance'
            AND ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-3 days')
            AND series = 'KXBTCD'
            AND predicted_outcome = 'yes'
            AND actual_outcome IN ('yes', 'no')
            AND CAST(json_extract(features_json, '$.ya') AS REAL) BETWEEN 0.50 AND 0.70
        )
        SELECT day, COUNT(*) AS n,
          ROUND(100.0 * SUM(CASE WHEN actual_outcome = 'yes' THEN 1 ELSE 0 END)
                / COUNT(*), 1) AS wr,
          ROUND(SUM(CASE WHEN actual_outcome = 'yes' THEN (1 - ya - 0.07)
                         ELSE (-ya - 0.07) END), 2) AS net_ct
        FROM s GROUP BY day ORDER BY day;
        """
    ).fetchall()
    _print_rows(daily)
    print("Promote when n ≥ 50 AND WR ≥ 75% AND net_ct > 0.")


def cohort_c_cme_basis_fade_post_j(con: sqlite3.Connection) -> None:
    _print_header(
        f"[3] cme_basis_fade_v1 paper post-J-config (>= {J_CONFIG_DEPLOY_TS}) by day"
    )
    daily = con.execute(
        """
        SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS n,
          ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)
                / COUNT(*), 1) AS wr,
          ROUND(SUM(pnl), 2) AS net,
          SUM(CASE WHEN price > 0.85 THEN 1 ELSE 0 END) AS over_cap_n
        FROM trades
        WHERE strategy = 'cme_basis_fade_v1'
          AND status = 'paper_settled'
          AND timestamp >= ?
        GROUP BY day ORDER BY day;
        """,
        (J_CONFIG_DEPLOY_TS,),
    ).fetchall()
    _print_rows(daily)
    print()
    print(
        "J-cap health: `over_cap_n` should be 0 — gate is strict `>`, so "
        "fires at no_ask=0.85 exactly are allowed by design."
    )
    print("Per-event cap (8) — top 5 events:")
    events = con.execute(
        """
        WITH s AS (
          SELECT substr(market_id, 7, 9) AS event_key
          FROM trades
          WHERE strategy = 'cme_basis_fade_v1' AND status = 'paper_settled'
            AND timestamp >= ?
        )
        SELECT event_key, COUNT(*) AS fires FROM s
        GROUP BY event_key ORDER BY fires DESC LIMIT 5;
        """,
        (J_CONFIG_DEPLOY_TS,),
    ).fetchall()
    _print_rows(events)


def main() -> None:
    con = _connect()
    try:
        print(f"DB: {DB_PATH}")
        cohort_a_lognormal_fair_dev_kxethd(con)
        cohort_b_bid_imbalance_kxbtcd(con)
        cohort_c_cme_basis_fade_post_j(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
