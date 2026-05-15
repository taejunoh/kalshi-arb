import sqlite3
import tempfile
from pathlib import Path

from scripts.tennis_mma_strategy_replay import load_observations


def _make_db(rows):
    """Helper: create an in-memory sqlite DB with odds_observations schema + given rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT, kalshi_ticker TEXT, series TEXT, sport TEXT,
            phase TEXT, minutes_to_start REAL,
            home_team TEXT, away_team TEXT, yes_sub_title TEXT,
            is_yes_home INTEGER,
            kalshi_yes_ask REAL, kalshi_no_ask REAL,
            us_yes_prob REAL, eu_yes_prob REAL, pinnacle_yes_prob REAL,
            pinnacle_age_s INTEGER, is_playoff INTEGER, is_yes_draw INTEGER,
            n_books_eu INTEGER, n_books_us INTEGER, has_pinnacle INTEGER,
            kalshi_yes_bid REAL, kalshi_no_bid REAL,
            kalshi_yes_bid_size REAL, kalshi_yes_ask_size REAL,
            kalshi_no_bid_size REAL, kalshi_no_ask_size REAL,
            kalshi_volume_24h REAL, kalshi_open_interest REAL,
            tournament_slug TEXT, starter_home TEXT, starter_away TEXT
        )
    """)
    for r in rows:
        cols = ",".join(r.keys())
        placeholders = ",".join(["?"] * len(r))
        conn.execute(f"INSERT INTO odds_observations ({cols}) VALUES ({placeholders})",
                     tuple(r.values()))
    conn.commit()
    return conn


def test_load_observations_filters_sport_and_window():
    conn = _make_db([
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-1",
         "series": "KXATPMATCH", "sport": "ATP", "phase": "pre",
         "minutes_to_start": 120.0, "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.52, "pinnacle_yes_prob": 0.60, "pinnacle_age_s": 100,
         "has_pinnacle": 1, "n_books_eu": 20, "is_yes_home": 1,
         "tournament_slug": "tennis_atp_italian_open"},
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXNBAGAME-X",
         "series": "KXNBAGAME", "sport": "NBA", "phase": "pre",
         "minutes_to_start": 60.0, "kalshi_yes_ask": 0.50, "kalshi_no_ask": 0.50,
         "eu_yes_prob": 0.50, "pinnacle_yes_prob": 0.50, "pinnacle_age_s": 100,
         "has_pinnacle": 1, "n_books_eu": 15, "is_yes_home": 1,
         "tournament_slug": None},
    ])
    rows = load_observations(conn, sports=("ATP",), start="2026-05-11", end="2026-05-15")
    assert len(rows) == 1
    assert rows[0]["sport"] == "ATP"
