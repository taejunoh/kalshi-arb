"""MLB pitcher collector — non-regular-season filtering (2026-07-12).

The MLB Stats API schedule (sportId=1) includes the All-Star Game during the
mid-July break: gameType 'A', team ids 159 (American League All-Stars) and
160 (National League All-Stars). Those ids aren't in TEAM_MAP, so before the
fix the collector logged them as "UNKNOWN TEAM IDS" and fired a critical
Telegram alert every 30 min for the whole break — the alert's prescription
("add to TEAM_MAP") is wrong: the All-Star Game is an exhibition with no
tradeable KXMLBGAME ticker and a meaningless ERA-diff, so it must be skipped,
not mapped.

The fix filters exhibition gameTypes (All-Star / spring / exhibition) BEFORE
the team-id lookup, so real games with genuinely unknown ids (rebrand /
relocation / expansion) still alert.
"""
import sqlite3

from data_collectors import mlb_pitchers


def _game(away_id, away_name, home_id, home_name, game_type,
          game_date="2026-07-20T23:05:00Z"):
    return {
        "gameType": game_type,
        "gameDate": game_date,
        "teams": {
            "away": {
                "team": {"id": away_id, "name": away_name},
                "probablePitcher": {"id": 10 * away_id, "fullName": f"{away_name} SP"},
            },
            "home": {
                "team": {"id": home_id, "name": home_name},
                "probablePitcher": {"id": 10 * home_id, "fullName": f"{home_name} SP"},
            },
        },
    }


def _install(monkeypatch, tmp_path, games):
    """Point the collector at a temp DB and feed it a fixed schedule; return
    a list that captures every Telegram-critical message it tries to send."""
    db = tmp_path / "bot.db"
    monkeypatch.setattr(mlb_pitchers, "DB", str(db))
    monkeypatch.setattr(mlb_pitchers, "fetch_schedule",
                        lambda date_str: {"dates": [{"games": games}]})
    monkeypatch.setattr(mlb_pitchers, "fetch_pitcher_era",
                        lambda pid, season: 3.50)
    sent = []
    monkeypatch.setattr(mlb_pitchers, "_send_telegram_critical", sent.append)
    return db, sent


def _rows(db):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT away_code, home_code FROM mlb_pitcher_observations"
        ).fetchall()
    finally:
        conn.close()


def test_all_star_game_skipped_without_alert(tmp_path, monkeypatch):
    """The All-Star Game (gameType 'A', ids 159/160) must be skipped silently:
    no unknown-team alert, no observation row."""
    games = [
        _game(159, "American League All-Stars", 160, "National League All-Stars", "A"),
        _game(147, "New York Yankees", 111, "Boston Red Sox", "R"),
    ]
    db, sent = _install(monkeypatch, tmp_path, games)

    mlb_pitchers.run()

    assert sent == [], f"exhibition game must not fire a critical alert, got: {sent}"
    rows = _rows(db)
    assert ("NYY", "BOS") in rows            # regular-season game recorded
    assert ("AL", "NL") not in rows          # All-Star not mapped/inserted
    assert len(rows) == 1                     # only the real game


def test_unknown_id_in_regular_season_still_alerts(tmp_path, monkeypatch):
    """The alert's real purpose survives: a genuinely unknown team id in a
    REGULAR-season game (rebrand/relocation/expansion) must still alert."""
    games = [
        _game(999, "Nashville Expansion", 111, "Boston Red Sox", "R"),
    ]
    db, sent = _install(monkeypatch, tmp_path, games)

    mlb_pitchers.run()

    assert len(sent) == 1
    assert "999" in sent[0] and "Nashville Expansion" in sent[0]
    assert _rows(db) == []  # unmapped game not inserted
