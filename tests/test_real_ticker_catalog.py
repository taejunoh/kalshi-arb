"""Test mirror_dedup.match_key() against real Kalshi ticker fixture.

Catalog (tests/fixtures/real_ticker_catalog.txt) is sampled from production
DB. Refresh the fixture periodically:

  ssh kalshi-bot 'sqlite3 data/bot.db "SELECT kalshi_ticker FROM \\
    odds_observations WHERE timestamp >= datetime(\"now\",\"-30 days\") \\
    GROUP BY kalshi_ticker ORDER BY RANDOM() LIMIT 20"' \\
    > /tmp/sample.txt
"""
from __future__ import annotations

import os

import pytest

from core.mirror_dedup import match_key

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "real_ticker_catalog.txt"
)


def load_catalog():
    pairs = []
    with open(FIXTURE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 2)
            if len(parts) >= 2:
                pairs.append((parts[0].strip(), parts[1].strip()))
    return pairs


class TestRealTickerCatalog:
    def test_all_tickers_parse(self):
        """Every real ticker should produce a non-None match_key."""
        catalog = load_catalog()
        assert len(catalog) > 0, "fixture is empty"
        for ticker, _expected in catalog:
            assert match_key(ticker) is not None, \
                f"FAILED to parse: {ticker}"

    def test_expected_match_keys(self):
        """Each ticker matches its documented expected match_key."""
        catalog = load_catalog()
        mismatches = []
        for ticker, expected in catalog:
            actual = match_key(ticker)
            if actual != expected:
                mismatches.append((ticker, expected, actual))
        assert not mismatches, f"Mismatched: {mismatches}"

    def test_all_unique_series_covered(self):
        """Every series prefix in catalog should produce same match_key
        for tickers that share game-id prefix."""
        catalog = load_catalog()
        keys = [match_key(t) for t, _ in catalog]
        # No duplicates expected since fixture is one ticker per game.
        assert len(set(keys)) == len(keys), "duplicate match_keys in fixture"

    def test_3_way_market_DRAW_preserved(self):
        """The KXMLSGAME ...-DRAW outcome should share match_key with
        ...-LAFC (so dedup_by_match would group them correctly)."""
        draw_key = match_key("KXMLSGAME-26MAY27LAFCSEA-DRAW")
        lafc_key = match_key("KXMLSGAME-26MAY27LAFCSEA-LAFC")
        sea_key = match_key("KXMLSGAME-26MAY27LAFCSEA-SEA")
        assert draw_key == lafc_key == sea_key, \
            "3-way soccer match_keys must be identical for proper aggressive dedup"
