"""Test fixture — synthetic backtest with intentional look-ahead bias.

Used by scripts/lint_lookahead.py to verify detection rules. Real
backtests should never have these patterns. Filename intentionally
matches `*backtest*` so default lint glob picks it up.
"""
import sqlite3


def biased_backtest_aggregate():
    conn = sqlite3.connect("dummy.db")
    # BAD: Uses season-aggregate stat as feature.
    season_era = 3.45  # noqa
    # BAD: SQL aggregate without time filter — looks at all-time data
    avg = conn.execute("""
        SELECT AVG(era) FROM mlb_pitcher_observations
    """).fetchone()
    # BAD: career stat
    career_wr = conn.execute("""
        SELECT career_wr FROM mlb_pitcher_observations WHERE pitcher_id = ?
    """, (1,)).fetchone()
    # BAD: JOIN ticker_settlements without timestamp guard
    rows = conn.execute("""
        SELECT o.kalshi_ticker, s.result
        FROM odds_observations o
        JOIN ticker_settlements s ON s.kalshi_ticker = o.kalshi_ticker
        WHERE o.pinnacle_yes_prob IS NOT NULL
    """).fetchall()
    return avg, career_wr, rows
