"""Observer SPORT_MAP entries for Tennis + MMA — added 2026-05-11."""
from strategies.sports_scan_observer import SPORT_MAP, SPORT_SHORT


def test_kxatpmatch_maps_to_tennis_atp_endpoint():
    # The exact tournament slug rotates with the tour calendar; this test
    # asserts the slug pattern is correct rather than a specific tournament.
    slug = SPORT_MAP.get("KXATPMATCH")
    assert slug is not None, "KXATPMATCH must be mapped to an Odds API slug"
    assert slug.startswith("tennis_atp_"), f"Expected tennis_atp_* slug, got {slug!r}"


def test_kxwtamatch_maps_to_tennis_wta_endpoint():
    slug = SPORT_MAP.get("KXWTAMATCH")
    assert slug is not None
    assert slug.startswith("tennis_wta_"), f"Expected tennis_wta_* slug, got {slug!r}"


def test_kxufcfight_maps_to_mma_endpoint():
    slug = SPORT_MAP.get("KXUFCFIGHT")
    assert slug is not None
    assert "mma" in slug, f"Expected mma slug, got {slug!r}"


def test_kxatpmatch_short_in_observer():
    assert SPORT_SHORT.get("KXATPMATCH") == "ATP"


def test_kxwtamatch_short_in_observer():
    assert SPORT_SHORT.get("KXWTAMATCH") == "WTA"


def test_kxufcfight_short_in_observer():
    assert SPORT_SHORT.get("KXUFCFIGHT") == "UFC"
