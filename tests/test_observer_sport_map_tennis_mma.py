"""Observer SPORT_MAP entries for Tennis + MMA — added 2026-05-11,
extended 2026-05-18 to support tuple-valued tennis slugs (multi-tournament
fan-out)."""
from strategies.sports_scan_observer import SPORT_MAP, SPORT_SHORT


def test_kxatpmatch_maps_to_tennis_atp_endpoints():
    # Tennis values are tuples of concurrent tournament slugs. The tuple may
    # legitimately be EMPTY between seasons / mid-rotation — e.g. 2026-06-08,
    # when clay ended but the Odds API had not yet published any ATP grass
    # endpoint (no queens/halle/'s-hertogenbosch slug existed). When non-empty,
    # every slug must follow the tennis_atp_* pattern. The unmapped-active
    # rotation alert (tennis_endpoint_rotation.py) covers the empty window.
    slugs = SPORT_MAP.get("KXATPMATCH")
    assert slugs is not None, "KXATPMATCH must be present in SPORT_MAP"
    assert isinstance(slugs, tuple)
    for slug in slugs:
        assert slug.startswith("tennis_atp_"), (
            f"Expected tennis_atp_* slug, got {slug!r}"
        )


def test_kxwtamatch_maps_to_tennis_wta_endpoints():
    # Like KXATPMATCH, the WTA tuple may legitimately be EMPTY between
    # tournaments — e.g. 2026-07-13, the post-Wimbledon gap when Wimbledon
    # ended and the Odds API had not yet published the mid-July swing (no
    # active WTA endpoint of any kind). When non-empty, every slug must follow
    # the tennis_wta_* pattern. The unmapped-active rotation alert covers the
    # empty window. (Was `len >= 1` until 7/13 — that invariant hard-failed on
    # the legitimate between-tournaments state.)
    slugs = SPORT_MAP.get("KXWTAMATCH")
    assert slugs is not None
    assert isinstance(slugs, tuple)
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
