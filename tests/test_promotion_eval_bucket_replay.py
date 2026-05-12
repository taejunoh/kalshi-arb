"""Bucket-replay section in promotion_eval — added 2026-05-11.

Verifies 4-archetype bucket boundaries and per-bucket replay computation
(n, win-rate, ROI) for synthetic ATP entries.

Field-name note: _load_entries produces entries with `yes_ask`, `eu_prob`,
and `result` ('yes'/'no') — NOT `kalshi_yes_ask`/`eu_yes_prob`/`settled_yes`.
_replay_archetype_buckets reads these field names directly.
Synthetic test entries use the same schema to match real _load_entries output.
"""
import pytest

from scripts.promotion_eval import (
    ARCHETYPE_BUCKETS,
    _bucket_label_for_archetype,
    _replay_archetype_buckets,
)


def test_archetype_buckets_are_four():
    assert len(ARCHETYPE_BUCKETS) == 4


def test_archetype_bucket_boundaries():
    expected = [
        (0.05, 0.25, "longshot"),
        (0.25, 0.39, "underdog"),
        (0.39, 0.75, "mid-fav"),
        (0.75, 1.00, "chalk"),
    ]
    for (exp_lo, exp_hi, exp_label), (lo, hi, lbl) in zip(expected, ARCHETYPE_BUCKETS):
        assert lo == pytest.approx(exp_lo)
        assert hi == pytest.approx(exp_hi)
        assert lbl == exp_label


def test_bucket_label_for_archetype():
    assert _bucket_label_for_archetype(0.10) == "longshot"
    assert _bucket_label_for_archetype(0.30) == "underdog"
    assert _bucket_label_for_archetype(0.50) == "mid-fav"
    assert _bucket_label_for_archetype(0.80) == "chalk"
    assert _bucket_label_for_archetype(0.03) is None
    assert _bucket_label_for_archetype(1.0) is None


def test_replay_archetype_buckets_computes_per_bucket_stats():
    """Synthetic entries: 3 longshot wins, 1 longshot loss; 2 underdog losses.

    Uses `result: 'yes'/'no'` to match the actual schema from _load_entries.
    The eu_prob >= ask + 0.05 gate is satisfied for all entries here.
    """
    entries = [
        {"yes_ask": 0.10, "eu_prob": 0.20, "result": "yes"},
        {"yes_ask": 0.12, "eu_prob": 0.25, "result": "yes"},
        {"yes_ask": 0.15, "eu_prob": 0.22, "result": "yes"},
        {"yes_ask": 0.20, "eu_prob": 0.30, "result": "no"},
        {"yes_ask": 0.30, "eu_prob": 0.40, "result": "no"},
        {"yes_ask": 0.35, "eu_prob": 0.45, "result": "no"},
    ]
    out = _replay_archetype_buckets(entries, n_boot=100)
    longshot = out["longshot"]
    assert longshot["n"] == 4
    assert longshot["wins"] == 3
    # ROI: ((1-0.10)/0.10) + ((1-0.12)/0.12) + ((1-0.15)/0.15) + (-1) all / 4
    # = (9 + 7.333 + 5.667 + -1) / 4 = 5.25
    assert longshot["roi"] == pytest.approx(5.25, abs=0.01)
    underdog = out["underdog"]
    assert underdog["n"] == 2
    assert underdog["wins"] == 0
    assert underdog["roi"] == pytest.approx(-1.0, abs=0.01)
    assert out["mid-fav"]["n"] == 0
    assert out["chalk"]["n"] == 0


def test_atp_wta_ufc_in_target_sports():
    from scripts.promotion_eval import TARGET_SPORTS
    assert "ATP" in TARGET_SPORTS
    assert "WTA" in TARGET_SPORTS
    assert "UFC" in TARGET_SPORTS
