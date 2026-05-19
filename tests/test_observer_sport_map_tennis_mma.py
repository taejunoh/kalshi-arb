"""Observer SPORT_MAP entries for Tennis + MMA — added 2026-05-11,
extended 2026-05-18 to support tuple-valued tennis slugs (multi-tournament
fan-out)."""
from strategies.sports_scan_observer import SPORT_MAP, SPORT_SHORT


def test_kxatpmatch_maps_to_tennis_atp_endpoints():
    # Tennis values are tuples of concurrent tournament slugs. Each slug must
    # follow the tennis_atp_* pattern.
    slugs = SPORT_MAP.get("KXATPMATCH")
    assert slugs is not None, "KXATPMATCH must be mapped to Odds API slugs"
    assert isinstance(slugs, tuple) and len(slugs) >= 1
    for slug in slugs:
        assert slug.startswith("tennis_atp_"), (
            f"Expected tennis_atp_* slug, got {slug!r}"
        )


def test_kxwtamatch_maps_to_tennis_wta_endpoints():
    slugs = SPORT_MAP.get("KXWTAMATCH")
    assert slugs is not None
    assert isinstance(slugs, tuple) and len(slugs) >= 1
    for slug in slugs:
        assert slug.startswith("tennis_wta_"), (
            f"Expected tennis_wta_* slug, got {slug!r}"
        )


def test_kxufcfight_maps_to_mma_endpoint():
    # UFC stays single-endpoint (year-round mma_mixed_martial_arts).
    slug = SPORT_MAP.get("KXUFCFIGHT")
    assert slug is not None
    assert isinstance(slug, str) and "mma" in slug, (
        f"Expected mma slug, got {slug!r}"
    )


def test_kxatpmatch_short_in_observer():
    assert SPORT_SHORT.get("KXATPMATCH") == "ATP"


def test_kxwtamatch_short_in_observer():
    assert SPORT_SHORT.get("KXWTAMATCH") == "WTA"


def test_kxufcfight_short_in_observer():
    assert SPORT_SHORT.get("KXUFCFIGHT") == "UFC"
