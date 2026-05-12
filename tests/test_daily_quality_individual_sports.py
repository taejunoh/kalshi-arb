"""daily_data_quality covers ATP/WTA/UFC — added 2026-05-11."""
import sqlite3
import pytest

from scripts.daily_data_quality import _per_sport_stats, _format_sport_block


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


def test_format_sport_block_for_atp_does_not_assume_3way(db_with_atp_rows):
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    block = _format_sport_block("ATP", stats["ATP"], baseline=None)
    text = "\n".join(block)
    # Tennis is 2-way — TIE leg balance section must NOT appear for ATP
    assert "TIE" not in text
    assert "tie" not in text.lower(), f"ATP block should not reference 'tie' (3-way concept): {text}"
    # Pinnacle coverage % MUST appear
    assert "Pinnacle" in text
