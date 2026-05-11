import json
import os
import tempfile
import pytest

from core.polymarket_mapping import PolymarketKalshiMapping


def _write_mapping(tmp_dir: str, mappings: list[dict]) -> str:
    path = os.path.join(tmp_dir, "mapping.json")
    with open(path, "w") as f:
        json.dump(mappings, f)
    return path


def test_load_and_lookup_direct():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {
                "polymarket_slug": "will-trump-win-2028",
                "kalshi_ticker_prefix": "KXPRES-2028-T",
                "outcome_match": "direct",
                "notes": "test",
            },
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("will-trump-win-2028")
        assert len(entries) == 1
        assert entries[0]["kalshi_ticker_prefix"] == "KXPRES-2028-T"
        assert entries[0]["outcome_match"] == "direct"


def test_load_and_lookup_reversed():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {
                "polymarket_slug": "will-trump-win-2028",
                "kalshi_ticker_prefix": "KXPRES-2028-DEM",
                "outcome_match": "reversed",
                "notes": "",
            },
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("will-trump-win-2028")
        assert entries[0]["outcome_match"] == "reversed"


def test_one_slug_multiple_prefixes():
    """A slug can map to multiple Kalshi prefixes (direct + reversed)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "x", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
            {"polymarket_slug": "x", "kalshi_ticker_prefix": "KX2",
             "outcome_match": "reversed", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        entries = m.entries_for_slug("x")
        assert len(entries) == 2
        prefixes = sorted(e["kalshi_ticker_prefix"] for e in entries)
        assert prefixes == ["KX1", "KX2"]


def test_all_slugs():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
            {"polymarket_slug": "b", "kalshi_ticker_prefix": "KX2",
             "outcome_match": "direct", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        assert sorted(m.all_slugs()) == ["a", "b"]


def test_reload_picks_up_new_mappings():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_mapping(tmp, [
            {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
             "outcome_match": "direct", "notes": ""},
        ])
        m = PolymarketKalshiMapping(path)
        assert m.all_slugs() == ["a"]
        # Add a new mapping
        with open(path, "w") as f:
            json.dump([
                {"polymarket_slug": "a", "kalshi_ticker_prefix": "KX1",
                 "outcome_match": "direct", "notes": ""},
                {"polymarket_slug": "b", "kalshi_ticker_prefix": "KX2",
                 "outcome_match": "direct", "notes": ""},
            ], f)
        m.reload()
        assert sorted(m.all_slugs()) == ["a", "b"]


def test_missing_file_returns_empty():
    m = PolymarketKalshiMapping("/nonexistent/path.json")
    assert m.all_slugs() == []
    assert m.entries_for_slug("anything") == []
