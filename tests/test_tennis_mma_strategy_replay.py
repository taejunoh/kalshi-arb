import sqlite3

from scripts.tennis_mma_strategy_replay import (
    load_observations,
    resolve_settled_outcomes,
    simulate_tennis_pinnacle_arb_v1,
    wilson_ci,
)
from scripts.tennis_mma_strategy_replay import (
    simulate_tennis_bid_imbalance_v1,
    compute_imbalance_decile_baseline,
)


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


def test_load_observations_boundary_timestamps():
    """Half-open window [start, end): start is INCLUDED, end is EXCLUDED."""
    conn = _make_db([
        # Exactly at start — should be INCLUDED.
        {"timestamp": "2026-05-11T00:00:00+00:00", "kalshi_ticker": "KXATPMATCH-START",
         "series": "KXATPMATCH", "sport": "ATP", "phase": "pre",
         "minutes_to_start": 120.0, "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "is_yes_home": 1},
        # Strictly inside — should be INCLUDED.
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-MID",
         "series": "KXATPMATCH", "sport": "ATP", "phase": "pre",
         "minutes_to_start": 120.0, "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "is_yes_home": 1},
        # Exactly at end — should be EXCLUDED (half-open).
        {"timestamp": "2026-05-15T00:00:00+00:00", "kalshi_ticker": "KXATPMATCH-END",
         "series": "KXATPMATCH", "sport": "ATP", "phase": "pre",
         "minutes_to_start": 120.0, "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "is_yes_home": 1},
    ])
    rows = load_observations(
        conn, sports=("ATP",),
        start="2026-05-11T00:00:00+00:00",
        end="2026-05-15T00:00:00+00:00",
    )
    tickers = {r["kalshi_ticker"] for r in rows}
    assert tickers == {"KXATPMATCH-START", "KXATPMATCH-MID"}


def test_wilson_ci_known_values():
    # Known: 5/10 wins, z=1.96 → CI ~[0.237, 0.763]
    lo, hi = wilson_ci(5, 10)
    assert abs(lo - 0.237) < 0.01
    assert abs(hi - 0.763) < 0.01


def test_wilson_ci_zero_n():
    lo, hi = wilson_ci(0, 0)
    assert lo == 0.0 and hi == 0.0


def test_resolve_settled_outcomes_returns_dict():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE ticker_settlements (
            kalshi_ticker TEXT PRIMARY KEY,
            result TEXT NOT NULL,
            close_yes_ask REAL,
            close_no_ask REAL,
            settled_at TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO ticker_settlements (kalshi_ticker, result, fetched_at) "
        "VALUES ('KXATPMATCH-1', 'yes', '2026-05-15T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO ticker_settlements (kalshi_ticker, result, fetched_at) "
        "VALUES ('KXATPMATCH-2', 'no', '2026-05-15T00:00:00+00:00')"
    )
    conn.commit()
    out = resolve_settled_outcomes(
        conn, ["KXATPMATCH-1", "KXATPMATCH-2", "KXATPMATCH-3"]
    )
    assert out == {"KXATPMATCH-1": 1.0, "KXATPMATCH-2": 0.0}


def test_simulate_tennis_pinnacle_arb_fires_on_gap():
    rows = [
        # Should fire — 0.10 gap, fresh pin, eu within 3pp of pin
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-1",
         "sport": "ATP", "minutes_to_start": 120.0,
         "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.63, "pinnacle_yes_prob": 0.65,
         "pinnacle_age_s": 100, "has_pinnacle": 1, "n_books_eu": 20},
        # Should NOT fire — gap only 2pp
        {"timestamp": "2026-05-12T10:00:00+00:00", "kalshi_ticker": "KXATPMATCH-2",
         "sport": "ATP", "minutes_to_start": 120.0,
         "kalshi_yes_ask": 0.55, "kalshi_no_ask": 0.45,
         "eu_yes_prob": 0.56, "pinnacle_yes_prob": 0.57,
         "pinnacle_age_s": 100, "has_pinnacle": 1, "n_books_eu": 20},
    ]
    settled = {"KXATPMATCH-1": 1.0, "KXATPMATCH-2": 0.0}
    fires = simulate_tennis_pinnacle_arb_v1(rows, settled)
    assert len(fires) == 1
    assert fires[0]["ticker"] == "KXATPMATCH-1"
    assert fires[0]["side"] == "yes"
    assert fires[0]["settle"] == 1.0


def test_imbalance_decile_baseline_returns_per_band_wr():
    """For each price band, return baseline WR with NO imbalance filter."""
    rows = [
        # YES band 0.50-0.85: 3 wins / 5 settled
        {"timestamp": "t", "kalshi_ticker": f"X{i}", "sport": "ATP",
         "minutes_to_start": 300.0, "kalshi_yes_ask": 0.60, "kalshi_no_ask": 0.40,
         "kalshi_yes_bid_size": 50.0, "kalshi_no_bid_size": 50.0,
         "pinnacle_yes_prob": 0.60, "pinnacle_age_s": 100, "has_pinnacle": 1,
         "eu_yes_prob": 0.60}
        for i in range(5)
    ]
    settled = {f"X{i}": (1.0 if i < 3 else 0.0) for i in range(5)}
    baselines = compute_imbalance_decile_baseline(rows, settled)
    assert "yes_band" in baselines
    assert abs(baselines["yes_band"] - 0.60) < 0.001  # 3/5 = 0.60
