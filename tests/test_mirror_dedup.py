"""Tests for mirror_dedup utility."""
from __future__ import annotations

import pytest

from core.mirror_dedup import (
    dedup_by_match,
    group_by_match,
    match_key,
    mirror_stats,
)


class TestMatchKey:
    def test_npb_pair(self):
        assert match_key("KXNPBGAME-26MAY270500TOHCHU-CHU") == "KXNPBGAME-26MAY270500TOHCHU"
        assert match_key("KXNPBGAME-26MAY270500TOHCHU-TOH") == "KXNPBGAME-26MAY270500TOHCHU"

    def test_tennis_pair(self):
        assert match_key("KXATPMATCH-26MAY24FARSHA-SHA") == "KXATPMATCH-26MAY24FARSHA"
        assert match_key("KXATPMATCH-26MAY24FARSHA-FAR") == "KXATPMATCH-26MAY24FARSHA"

    def test_wta_pair(self):
        assert match_key("KXWTAMATCH-26MAY24PRIOLI-OLI") == "KXWTAMATCH-26MAY24PRIOLI"

    def test_mlb_pair(self):
        assert match_key("KXMLBGAME-26MAY27NYYBOS-NYY") == "KXMLBGAME-26MAY27NYYBOS"
        assert match_key("KXMLBGAME-26MAY27NYYBOS-BOS") == "KXMLBGAME-26MAY27NYYBOS"

    def test_ufc(self):
        assert match_key("KXUFCFIGHT-26MAY27JONESBJ-BJ") == "KXUFCFIGHT-26MAY27JONESBJ"

    def test_unknown_returns_none(self):
        # Non-sports tickers
        assert match_key("KXBTCDAILY-26MAY28-90000") is None
        assert match_key("KXFEDDECISION-26JUN") is None
        assert match_key("") is None
        assert match_key(None) is None


class TestGroupByMatch:
    def test_dict_rows(self):
        rows = [
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-TOH", "pnl": 1.0},
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-CHU", "pnl": -1.0},
            {"ticker": "KXATPMATCH-26MAY24FARSHA-FAR", "pnl": 0.5},
        ]
        grouped = group_by_match(rows)
        assert len(grouped) == 2
        assert len(grouped["KXNPBGAME-26MAY270500TOHCHU"]) == 2
        assert len(grouped["KXATPMATCH-26MAY24FARSHA"]) == 1

    def test_tuple_rows(self):
        # Tuple form: ticker is first element
        rows = [
            ("KXATPMATCH-26MAY24FARSHA-FAR", 0.5),
            ("KXATPMATCH-26MAY24FARSHA-SHA", -0.5),
        ]
        grouped = group_by_match(rows)
        assert len(grouped) == 1
        assert len(grouped["KXATPMATCH-26MAY24FARSHA"]) == 2

    def test_custom_ticker_key(self):
        rows = [
            {"market_id": "KXMLBGAME-26MAY27NYYBOS-NYY", "pnl": 1.0},
        ]
        grouped = group_by_match(rows, ticker_key="market_id")
        assert "KXMLBGAME-26MAY27NYYBOS" in grouped


class TestDedupByMatch:
    def test_pair_deduped_to_one(self):
        rows = [
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-TOH", "pnl": 1.0},
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-CHU", "pnl": -1.0},
        ]
        deduped = dedup_by_match(rows)
        assert len(deduped) == 1
        # Keeps first by default
        assert deduped[0]["ticker"].endswith("-TOH")

    def test_solo_unchanged(self):
        rows = [
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-TOH", "pnl": 1.0},
        ]
        deduped = dedup_by_match(rows)
        assert len(deduped) == 1

    def test_three_way_preserved_by_default(self):
        # Soccer 3-way HOME/AWAY/DRAW — default preserves all 3
        rows = [
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-LAFC", "pnl": 1.0},
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-SEA", "pnl": -0.5},
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-DRAW", "pnl": 0.0},
        ]
        deduped = dedup_by_match(rows)
        assert len(deduped) == 3

    def test_three_way_collapsed_with_aggressive(self):
        rows = [
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-LAFC", "pnl": 1.0},
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-SEA", "pnl": -0.5},
            {"ticker": "KXMLSGAME-26MAY27LAFCSEA-DRAW", "pnl": 0.0},
        ]
        deduped = dedup_by_match(rows, aggressive=True)
        assert len(deduped) == 1

    def test_unknown_ticker_passes_through(self):
        rows = [
            {"ticker": "KXBTCDAILY-26MAY28-90000", "pnl": 1.0},
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-TOH", "pnl": 0.5},
            {"ticker": "KXNPBGAME-26MAY270500TOHCHU-CHU", "pnl": 0.5},
        ]
        deduped = dedup_by_match(rows)
        # 1 unknown + 1 deduped sports pair = 2
        assert len(deduped) == 2

    def test_mixed_pairs_and_solos(self):
        rows = [
            {"ticker": "KXNPBGAME-A-X", "pnl": 1.0},  # malformed — passes through
            {"ticker": "KXNPBGAME-26MAY27FOOBAR-FOO", "pnl": 0.5},
            {"ticker": "KXNPBGAME-26MAY27FOOBAR-BAR", "pnl": -0.5},
            {"ticker": "KXNPBGAME-26MAY27SOLO-SOL", "pnl": 0.3},
        ]
        deduped = dedup_by_match(rows)
        # 1 malformed (X is single char — no match) + 1 paired + 1 solo = 3
        # Actually the X is 1 char, doesn't match [A-Z0-9]{2,5}, so it's
        # passed through as unknown.
        assert len(deduped) == 3


class TestMirrorStats:
    def test_inflation_ratio(self):
        rows = [
            {"ticker": "KXNPBGAME-26MAY27SOLO-AAA"},
            {"ticker": "KXNPBGAME-26MAY27PAIR1-AAA"},
            {"ticker": "KXNPBGAME-26MAY27PAIR1-BBB"},
            {"ticker": "KXMLSGAME-26MAY27TRIPLE-AAA"},
            {"ticker": "KXMLSGAME-26MAY27TRIPLE-BBB"},
            {"ticker": "KXMLSGAME-26MAY27TRIPLE-CCC"},
        ]
        s = mirror_stats(rows)
        # SOLO: 1 match (1 row), PAIR1: 1 match (2 rows), TRIPLE: 1 match (3 rows)
        assert s["unique_matches"] == 3
        assert s["solo_matches"] == 1
        assert s["paired_matches"] == 1
        assert s["multi_matches"] == 1
        assert s["total_rows"] == 6
        assert s["inflation_ratio"] == pytest.approx(2.0)

    def test_empty(self):
        s = mirror_stats([])
        assert s["unique_matches"] == 0
        assert s["inflation_ratio"] == 0.0
