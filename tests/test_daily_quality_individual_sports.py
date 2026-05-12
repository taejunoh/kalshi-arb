"""daily_data_quality covers ATP/WTA/UFC — added 2026-05-11.

Verifies the production report path (_format_stats_block) handles
individual sports correctly: rows counted, Pinnacle coverage shown,
no phantom TIE section. The TIE handling is data-driven (gated on
tie_legs > 0), so ATP rows with is_yes_draw=0 land in the 2-way branch.
"""
import sqlite3
import pytest

from scripts.daily_data_quality import _per_sport_stats, _format_stats_block


@pytest.fixture
def db_with_atp_rows(tmp_path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            kalshi_ticker TEXT,
            series TEXT,
            sport TEXT,
            phase TEXT,
            minutes_to_start REAL,
            home_team TEXT, away_team TEXT, yes_sub_title TEXT,
            is_yes_home INTEGER, is_yes_draw INTEGER,
            kalshi_yes_ask REAL, kalshi_no_ask REAL,
            us_yes_prob REAL, eu_yes_prob REAL, pinnacle_yes_prob REAL,
            pinnacle_age_s INTEGER, is_playoff INTEGER,
            n_books_eu INTEGER, n_books_us INTEGER, has_pinnacle INTEGER,
            kalshi_yes_bid REAL, kalshi_no_bid REAL,
            kalshi_yes_bid_size REAL, kalshi_yes_ask_size REAL,
            kalshi_no_bid_size REAL, kalshi_no_ask_size REAL,
            kalshi_volume_24h REAL, kalshi_open_interest REAL
        )"""
    )
    # 2 ATP rows in last hour
    conn.execute(
        """INSERT INTO odds_observations (timestamp, sport, series, kalshi_ticker,
                    is_yes_home, is_yes_draw, n_books_eu, has_pinnacle,
                    kalshi_open_interest, kalshi_volume_24h)
           VALUES (datetime('now', '-30 minutes'), 'ATP', 'KXATPMATCH',
                   'KXATPMATCH-26MAY12ALCSIN-ALC', 1, 0, 12, 1, 5000, 200),
                  (datetime('now', '-30 minutes'), 'ATP', 'KXATPMATCH',
                   'KXATPMATCH-26MAY12ALCSIN-SIN', 0, 0, 12, 1, 5000, 200)"""
    )
    conn.commit()
    conn.close()
    return str(db_path)


def test_atp_appears_in_per_sport_stats(db_with_atp_rows):
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    assert "ATP" in stats
    assert stats["ATP"]["n"] == 2
    # is_yes_draw=0 for both rows → tie_legs must be 0 (2-way sport)
    assert stats["ATP"]["tie_legs"] == 0


def test_atp_in_format_stats_block_uses_2way_leg_check(db_with_atp_rows):
    """The production formatter must place ATP in the 2-way leg-balance
    branch (no TIE counted) since is_yes_draw=0 for tennis rows."""
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    text = _format_stats_block("Test", stats)
    # ATP row present in table
    assert "ATP" in text
    # Leg check format for 2-way sports is "OK" or "!h/a" (two slashes),
    # NOT "!h/a/t" (three slashes — 3-way format used only for soccer)
    # With 1 home + 1 away → balanced → leg="OK"
    atp_line = next((ln for ln in text.split("\n") if ln.startswith("ATP")), "")
    assert atp_line, f"Expected ATP row in stats block: {text}"
    assert "OK" in atp_line, (
        f"ATP (1 home + 1 away) should show leg_check=OK (2-way), got: {atp_line}"
    )


def test_atp_format_does_not_include_tie_word(db_with_atp_rows):
    """No phantom TIE label for individual sports."""
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    text = _format_stats_block("Test", stats)
    assert "TIE" not in text
    assert "tie" not in text.lower(), (
        f"ATP block must not reference 'tie' (3-way concept): {text}"
    )
