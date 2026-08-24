"""Market expansion 2026-06-12: pitcher_outs/ER/HA + team_totals + NBA player
props. Guards the _canon ordering trap (team_totals contains 'totals') and the
no-alternate-key rule (an unknown market key 422s the whole event call)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import softbook_ev_core as core
import softbook_ev_stats as stats


def test_canon_team_totals_precedes_totals_substring():
    assert core._canon("team_totals") == "team_total"
    assert core._canon("alternate_team_totals") == "team_total"
    assert core._canon("totals") == "total"
    assert core._canon("alternate_totals") == "total"


def test_canon_new_props_and_alternates():
    assert core._canon("pitcher_outs") == "pitcher_outs"
    assert core._canon("pitcher_earned_runs") == "pitcher_earned_runs"
    assert core._canon("pitcher_hits_allowed") == "pitcher_hits_allowed"
    assert core._canon("player_points") == "player_points"
    assert core._canon("player_points_alternate") == "player_points"
    # response-side folding tolerates alternates we don't request (future-proof);
    # the request-side guard lives in markets_for/PROP_ALTS
    assert core._canon("pitcher_outs_alternate") == "pitcher_outs"


def test_markets_for_requests_only_real_alternate_keys(monkeypatch):
    # Prop scanning is off by default since 2026-08-24 (credit cost, nothing
    # reads them). The alternate-key rules still have to be right for whenever
    # SOFTBOOK_SCAN_PROPS turns it back on — an unrecognized market key 422s
    # the entire event call, which is why this test exists.
    import importlib
    monkeypatch.setenv("SOFTBOOK_SCAN_PROPS", "1")
    importlib.reload(core)
    mlb = core.markets_for("baseball_mlb").split(",")
    assert "pitcher_outs" in mlb and "pitcher_outs_alternate" not in mlb
    assert "pitcher_earned_runs_alternate" not in mlb
    assert "batter_home_runs_alternate" in mlb
    assert "team_totals" in mlb and "alternate_team_totals" in mlb
    nba = core.markets_for("basketball_nba").split(",")
    assert "player_points" in nba and "player_points_alternate" in nba
    monkeypatch.delenv("SOFTBOOK_SCAN_PROPS", raising=False)
    importlib.reload(core)


def test_team_total_is_ou_and_label():
    assert core._is_ou("team_total")
    assert core.label("team_total", "Over", 4.5, "New York Yankees") == \
        "New York Yankees TT Over 4.5"


def _ev(pin_markets, soft_markets):
    return {"id": "ev1", "home_team": "Home HH", "away_team": "Away AA",
            "commence_time": "2099-01-01T00:00:00Z",
            "bookmakers": [
                {"key": "pinnacle", "markets": pin_markets},
                {"key": "fanduel", "markets": soft_markets}]}


def test_event_ev_bets_team_total_from_alternate_key():
    pin = [{"key": "team_totals", "outcomes": [
        {"name": "Over", "description": "Home HH", "point": 4.5, "price": 2.10},
        {"name": "Under", "description": "Home HH", "point": 4.5, "price": 1.78}]}]
    soft = [{"key": "alternate_team_totals", "outcomes": [
        {"name": "Over", "description": "Home HH", "point": 4.5, "price": 2.30},
        # same outcome repeated (main+alt overlap) — must count ONE book
        {"name": "Over", "description": "Home HH", "point": 4.5, "price": 2.25}]}]
    bets = core.event_ev_bets(_ev(pin, soft), "baseball_mlb")
    tt = [b for b in bets if b["market"] == "team_total"]
    assert len(tt) == 1
    b = tt[0]
    assert b["player"] == "Home HH" and b["point"] == 4.5
    assert b["n_books"] == 1 and b["odds"] == 2.30      # dedup kept best price
    assert b["label"] == "Home HH TT Over 4.5"


def test_grade_prop_settles_team_total_score():
    assert core.grade_prop("Over", 4.5, 5) == "win"
    assert core.grade_prop("Under", 4.5, 5) == "lose"
    assert core.grade_prop("Over", 5.0, 5) == "push"


def test_mlb_stat_map_covers_new_pitcher_props():
    assert stats.MLB_STAT["pitcher_outs"] == ("pitching", "outs")
    assert stats.MLB_STAT["pitcher_earned_runs"] == ("pitching", "earnedRuns")
    assert stats.MLB_STAT["pitcher_hits_allowed"] == ("pitching", "hits")


def test_nba_player_stat_dispatch(monkeypatch):
    monkeypatch.setattr(stats, "_nba_schedule",
                        lambda d: {("away aa", "home hh"): ("401", True)})
    monkeypatch.setattr(stats, "_nba_boxscore",
                        lambda eid: {"jalen brunson": {"PTS": "31", "REB": "4", "AST": "7"},
                                     "dnp guy": {}})
    got = stats.player_stat("basketball_nba", "Away AA", "Home HH",
                            "2026-06-11T00:00:00Z", "player_points", "Jalen Brunson")
    assert got == 31.0
    assert stats.player_stat("basketball_nba", "Away AA", "Home HH",
                             "2026-06-11T00:00:00Z", "player_rebounds", "DNP Guy") == stats.DNP
