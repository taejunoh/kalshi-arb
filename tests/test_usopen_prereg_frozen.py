"""The US Open pre-registration thresholds are frozen.

This file duplicates the constants on purpose. It is not testing arithmetic —
it is making a silent edit impossible: moving a threshold now requires changing
two files in one diff, which forces the change to be stated rather than slipped
in. Same reasoning as the RULE_CHANGES <-> BET_TIER_ERAS tie in the keepmypick
export: a rule quietly rewritten after the data lands is the exact failure the
registration exists to prevent.

If a threshold genuinely must move, say why in the commit message and note
whether any US Open result was already visible when the decision was made.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import usopen_med_ml_prereg as prereg


def test_thresholds_frozen():
    assert prereg.REGISTERED_ON == "2026-08-31"
    assert prereg.MIN_N == 40
    assert prereg.PASS_ROI == 0.25
    assert prereg.FAIL_ROI == 0.10


def test_sports_are_the_us_open_only():
    assert prereg.SPORTS == ("tennis_atp_us_open", "tennis_wta_us_open")


def test_pass_band_is_above_fail_band():
    # a gap between them is what makes UNDECIDED a real outcome rather than a
    # coin flip on the boundary
    assert prereg.FAIL_ROI < prereg.PASS_ROI
