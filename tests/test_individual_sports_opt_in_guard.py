"""INDIVIDUAL_SPORTS_OPT_IN guard — defense-in-depth (2026-05-18).

After the 2026-05-18 incident where sports_soccer_underdog_v1 and
sports_underdog_v37 fired live on 6 KXATPMATCH tickers (because their
SPORT_BLOCKLIST overrides did not include ATP/WTA/UFC), the base class
gained an opt-in flag. Subclasses must explicitly set True to receive
individual-sport tickers.

This test asserts:
1. Base default is False (team-sport strategies skip tennis/MMA).
2. Each tennis/UFC paper strategy opts in.
3. Live team-sport strategies do NOT opt in (regression guard for the
   exact incident — sports_soccer_underdog_v1 + sports_underdog_v37).
"""
import pytest

from strategies.sports_trader import (
    INDIVIDUAL_SPORT_SERIES,
    SportsTraderStrategy,
)


def test_base_class_does_not_opt_in():
    assert SportsTraderStrategy.INDIVIDUAL_SPORTS_OPT_IN is False


def test_individual_sport_series_contents():
    assert "KXATPMATCH" in INDIVIDUAL_SPORT_SERIES
    assert "KXWTAMATCH" in INDIVIDUAL_SPORT_SERIES
    assert "KXUFCFIGHT" in INDIVIDUAL_SPORT_SERIES


@pytest.mark.parametrize("module_path,class_name", [
    ("strategies.sports_tennis_pinnacle_arb_v1", "SportsTennisPinnacleArbV1Strategy"),
    ("strategies.sports_atp_gs_bo5_chalk_v1",    "SportsAtpGsBo5ChalkV1Strategy"),
    ("strategies.sports_tennis_bid_imbalance_v1","SportsTennisBidImbalanceV1Strategy"),
    ("strategies.sports_tennis_surface_skew_v1", "SportsTennisSurfaceSkewV1Strategy"),
    ("strategies.sports_ufc_chalk_fade_v1",      "SportsUfcChalkFadeV1Strategy"),
])
def test_tennis_and_ufc_strategies_opt_in(module_path, class_name):
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    assert cls.INDIVIDUAL_SPORTS_OPT_IN is True, (
        f"{class_name} must opt into individual-sport routing"
    )


@pytest.mark.parametrize("module_path,class_name", [
    # The two strategies that fired live on tennis in the 2026-05-18 incident
    ("strategies.sports_soccer_underdog_v1", "SportsSoccerUnderdogV1Strategy"),
    ("strategies.sports_underdog_v37",       "SportsUnderdogV37Strategy"),
    # Other live team-sport strategies — all must stay opted-out
    ("strategies.sports_underdog_v31",       "SportsUnderdogV31Strategy"),
    ("strategies.sports_underdog_v32",       "SportsUnderdogV32Strategy"),
    ("strategies.sports_longshot_v31",       "SportsLongshotV31Strategy"),
    ("strategies.sports_midshot_v31",        "SportsMidshotV31Strategy"),
    ("strategies.sports_v4_edge",            "SportsV4EdgeStrategy"),
    ("strategies.sports_underdog_v36",       "SportsUnderdogV36Strategy"),
    ("strategies.sports_soccer_draw_v1",     "SportsSoccerDrawV1Strategy"),
    ("strategies.sports_playoff_underdog_v1","SportsPlayoffUnderdogV1Strategy"),
])
def test_team_sport_strategies_do_not_opt_in(module_path, class_name):
    """Regression guard — these must inherit the False default."""
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    assert cls.INDIVIDUAL_SPORTS_OPT_IN is False, (
        f"{class_name} unexpectedly opted into individual-sport tickers — "
        "this is the 2026-05-18 regression we explicitly guard against."
    )
