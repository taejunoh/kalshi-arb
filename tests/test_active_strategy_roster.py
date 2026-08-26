"""Pins which strategies main.py actually starts (2026-08-26 mothball).

The recurring failure mode in this repo is not a bad strategy — it is a
strategy nobody meant to be running. `sports_underdog_v31` and
`sports_longshot_v31` sat in LIVE_STRATEGIES for 62 days without firing and
were set to auto-rearm on the October NBA opener against a 25-trade April
sample; 22 paper strategies kept firing after being demoted, polluting every
research read with -EV noise.

So the roster is now a pinned list rather than whatever happens to be
uncommented. Adding a strategy back is fine — it just has to be a deliberate
edit here too, with the audit number that justifies it.
"""
import pathlib
import re

MAIN = pathlib.Path(__file__).resolve().parent.parent / "main.py"

# Everything that may run. Anything else must stay commented out in main.py.
EXPECTED_SCAN_LOOP = {
    # LIVE (real money) — LIVE_STRATEGIES on EC2 must match this set.
    "sports_tennis_live_fade_v1",     # only strategy in the low-fee zone (~0.7%)
    "sports_tennis_underdog_v1",      # band decision deferred to post-US-Open
    "sports_mlb_pitcher_edge_v1",     # confirmed CLV edge (z=+2.80), MLB only
    "sports_mlb_pitcher_edge_v2_1",   # confirmed CLV edge (z=+2.04), MLB only
    # PAPER, deliberately kept.
    "sports_mlb_pitcher_edge_v2",     # control group for the 0.45 ask floor
    "sports_underdog_v31",            # collecting in-gate n=25 -> 45 for October
    "sports_longshot_v31",            # same
}

# Tuple-registered strategies still in the shared loop. All are inert in
# production, so they are left alone rather than churned:
#   market_maker    MM_ENABLED=false,             0 trades ever
#   spread_scanner  SPREAD_SCANNER_ENABLED=false, last traded 2026-03-27
#   econ_trader     ECON_ENABLED=false,           last traded 2026-04-29
#   ladder_shutdown last traded 2026-06-17
#   ladder_approval 0 trades ever
# If any of those env flags is ever flipped on, that is a deliberate decision
# and this set should be revisited at the same time.
EXPECTED_TUPLES = {
    "market_maker", "spread_scanner", "econ_trader",
    "ladder_shutdown", "ladder_approval",
}

_SCAN = re.compile(r"^\s+self\.(\w+)\.scan_loop\(", re.M)
_TUPLE = re.compile(r'^\s+\("(\w+)", self\.\w+\),', re.M)


def _uncommented(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_only_the_pinned_strategies_are_started():
    active = set(_SCAN.findall(_uncommented(MAIN.read_text())))
    assert active == EXPECTED_SCAN_LOOP, (
        f"roster drift.\n  unexpectedly active: {sorted(active - EXPECTED_SCAN_LOOP)}"
        f"\n  expected but missing: {sorted(EXPECTED_SCAN_LOOP - active)}"
    )


def test_only_the_pinned_tuples_are_registered():
    active = set(_TUPLE.findall(_uncommented(MAIN.read_text())))
    assert active == EXPECTED_TUPLES, (
        f"tuple roster drift.\n  unexpectedly active: {sorted(active - EXPECTED_TUPLES)}"
        f"\n  expected but missing: {sorted(EXPECTED_TUPLES - active)}"
    )


def test_mothballed_strategies_are_commented_not_deleted():
    """Mothball means reversible. The instances must survive in __init__ so a
    strategy can be brought back by uncommenting one line."""
    text = MAIN.read_text()
    for name in ("sports_midshot_v31", "sports_tennis_chalk_v1",
                 "sports_consensus_aligned_live_v1", "sports_npb_sharp_follow_v1"):
        assert f"self.{name} = " in text, f"{name} instance was deleted, not mothballed"
        assert f"# 2026-08-26 MOTHBALL" in text
