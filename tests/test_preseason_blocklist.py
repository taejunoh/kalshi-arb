"""Every exhibition-class sport key the Odds API offers is blocked.

NFL preseason got into the pick feed before its key was in SPORT_BLOCKLIST and
debuted in the public track record 0-5 (excluded going forward 2026-08-14, the
rows kept). The lesson is that the blocklist is only as good as its coverage of
the API's key space, so this pins the complete exhibition set as of 2026-08-14
(verified live against /sports?all=true). If the API adds a new preseason key,
this test will not catch it — but nothing here can; the check that found the
gap ("list every key matching preseason/spring/summer/all_star/exhibition") is
the thing to re-run when a new league's exhibition season approaches.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_ev_core as core  # noqa: E402

EXHIBITION_KEYS = {
    "americanfootball_nfl_preseason",
    "baseball_mlb_preseason",           # spring training
    "basketball_nba_all_stars",
    "basketball_nba_preseason",
    "basketball_nba_summer_league",
    "icehockey_nhl_preseason",
}


def test_every_known_exhibition_key_is_blocked():
    assert EXHIBITION_KEYS <= core.SPORT_BLOCKLIST


def test_regular_seasons_are_not_swept_up():
    for k in ("baseball_mlb", "icehockey_nhl", "basketball_nba",
              "americanfootball_nfl", "basketball_wnba"):
        assert k not in core.SPORT_BLOCKLIST
