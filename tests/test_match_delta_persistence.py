"""match_delta_s persistence: migration, both write paths, NULL semantics.

`record_odds_observation`'s INSERT is a hand-maintained column/placeholder
list, so adding a column there is exactly the kind of edit that silently
shifts every value one slot to the left. These tests execute the real SQL
against a temp DB rather than asserting on source text.
"""
import asyncio
import sqlite3

import pytest

from core.database import Database


def _init(tmp_path, name="bot.db") -> Database:
    db = Database(db_path=str(tmp_path / name))
    asyncio.run(db.initialize())
    return db


def _cols(db: Database, table: str) -> set:
    conn = sqlite3.connect(db.db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migration_adds_column_to_both_tables(tmp_path):
    db = _init(tmp_path)
    assert "match_delta_s" in _cols(db, "trades")
    assert "match_delta_s" in _cols(db, "odds_observations")


def test_migration_is_idempotent(tmp_path):
    """initialize() runs on every boot; ALTER TABLE would throw on a second
    pass if _add_column_if_missing were bypassed."""
    db = _init(tmp_path)
    asyncio.run(db.initialize())
    asyncio.run(db.initialize())
    assert "match_delta_s" in _cols(db, "trades")


def test_migration_column_is_nullable_with_no_default(tmp_path):
    """Guarantees the O(1) metadata-only ALTER on the 8.9GB production DB:
    SQLite rewrites every row only when a non-NULL default is supplied."""
    db = _init(tmp_path)
    conn = sqlite3.connect(db.db_path)
    try:
        for table in ("trades", "odds_observations"):
            row = [r for r in conn.execute(f"PRAGMA table_info({table})")
                   if r[1] == "match_delta_s"][0]
            _, _, col_type, notnull, default, _ = row
            assert col_type == "REAL"
            assert notnull == 0
            assert default is None
    finally:
        conn.close()


def test_record_match_delta_updates_the_trade(tmp_path):
    db = _init(tmp_path)
    trade_id = asyncio.run(db.record_trade(
        strategy="sports_underdog_v31",
        market_id="KXMLBGAME-26JUL041605DETTEX-TEX",
        direction="yes", price=0.53, size=2.0, fee=0.07,
        status="paper_filled", minutes_to_start=-84.0,
    ))
    asyncio.run(db.record_match_delta(trade_id, -158280.0))

    conn = sqlite3.connect(db.db_path)
    try:
        row = conn.execute(
            "SELECT match_delta_s, minutes_to_start, price, status "
            "FROM trades WHERE id = ?", (trade_id,)).fetchone()
    finally:
        conn.close()
    assert row[0] == -158280.0
    # Neighbouring columns untouched by the post-hoc UPDATE.
    assert row[1] == -84.0 and row[2] == 0.53 and row[3] == "paper_filled"


def test_new_trade_defaults_to_null(tmp_path):
    """A trade whose delta could not be computed stays NULL, so weekly
    aggregates can separate 'unknown' from 'perfectly matched'."""
    db = _init(tmp_path)
    trade_id = asyncio.run(db.record_trade(
        strategy="s", market_id="KXMLBGAME-26JUL041605DETTEX-TEX",
        direction="yes", price=0.5, size=1.0, fee=0.0, status="paper_filled",
    ))
    conn = sqlite3.connect(db.db_path)
    try:
        val = conn.execute("SELECT match_delta_s FROM trades WHERE id = ?",
                           (trade_id,)).fetchone()[0]
    finally:
        conn.close()
    assert val is None


def test_record_match_delta_none_is_a_noop(tmp_path):
    db = _init(tmp_path)
    trade_id = asyncio.run(db.record_trade(
        strategy="s", market_id="KXMLBGAME-26JUL041605DETTEX-TEX",
        direction="yes", price=0.5, size=1.0, fee=0.0, status="paper_filled",
    ))
    asyncio.run(db.record_match_delta(trade_id, None))
    conn = sqlite3.connect(db.db_path)
    try:
        assert conn.execute("SELECT match_delta_s FROM trades WHERE id = ?",
                            (trade_id,)).fetchone()[0] is None
    finally:
        conn.close()


@pytest.mark.parametrize("delta", [None, -158280.0])
def test_observation_insert_column_alignment(tmp_path, delta):
    """The INSERT lists 33 columns and 33 placeholders by hand. Assert on
    identifiable values in the columns that bracket the new one, so a shifted
    binding cannot pass."""
    db = _init(tmp_path)
    asyncio.run(db.record_odds_observation(
        kalshi_ticker="KXMLBGAME-26JUL041605DETTEX-TEX",
        series="KXMLBGAME", sport="MLB", phase="live", minutes_to_start=-84.0,
        home_team="Texas Rangers", away_team="Detroit Tigers",
        yes_sub_title="TEX", is_yes_home=True,
        kalshi_yes_ask=0.53, kalshi_no_ask=0.48,
        us_yes_prob=0.55, eu_yes_prob=0.56, pinnacle_yes_prob=0.925,
        pinnacle_age_s=261,
        tournament_slug="mlb-regular", starter_home="A Pitcher",
        starter_away="B Pitcher",
        match_delta_s=delta,
    ))
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM odds_observations").fetchone()
    finally:
        conn.close()
    assert row["match_delta_s"] == delta
    assert row["tournament_slug"] == "mlb-regular"
    assert row["starter_home"] == "A Pitcher"
    assert row["starter_away"] == "B Pitcher"
    assert row["phase"] == "live"
    assert row["minutes_to_start"] == -84.0
    assert row["pinnacle_yes_prob"] == 0.925
    assert row["pinnacle_age_s"] == 261
    assert row["kalshi_yes_ask"] == 0.53


def test_observation_match_delta_defaults_to_null(tmp_path):
    """Every other caller of record_odds_observation omits the new kwarg."""
    db = _init(tmp_path)
    asyncio.run(db.record_odds_observation(
        kalshi_ticker="KXNBAGAME-26APR21BOSBUF-BOS",
        series="KXNBAGAME", sport="NBA", phase="pre", minutes_to_start=120.0,
        home_team="Buffalo", away_team="Boston", yes_sub_title="BOS",
        is_yes_home=False, kalshi_yes_ask=0.4, kalshi_no_ask=0.62,
        us_yes_prob=None, eu_yes_prob=None, pinnacle_yes_prob=None,
        pinnacle_age_s=None,
    ))
    conn = sqlite3.connect(db.db_path)
    try:
        assert conn.execute(
            "SELECT match_delta_s FROM odds_observations").fetchone()[0] is None
    finally:
        conn.close()
