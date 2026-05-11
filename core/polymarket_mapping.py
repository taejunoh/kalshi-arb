"""Polymarket slug <-> Kalshi ticker_prefix manual mapping (JSON-backed).

JSON schema (list of objects):
  {
    "polymarket_slug": "...",       # Polymarket URL slug
    "kalshi_ticker_prefix": "...",  # Kalshi ticker prefix (one prefix can
                                    # cover multiple ticker children)
    "outcome_match": "direct" | "reversed",  # Are Yes/No semantics aligned?
    "notes": "..."                  # Free-text for human reviewer
  }

One slug may have multiple entries (e.g., "Trump=YES" maps to both
"KXPRES-2028-T" direct and "KXPRES-2028-DEM" reversed).

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import json
import logging
import os
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("polymarket_mapping")


class PolymarketKalshiMapping:
    """JSON-backed lookup. Hot-reload via reload() — caller may invoke
    periodically (e.g., once per scan_loop iteration) so mapping edits
    take effect without bot restart.
    """

    def __init__(self, path: str):
        self._path = path
        self._by_slug: dict[str, list[dict]] = defaultdict(list)
        self.reload()

    def reload(self) -> None:
        """Re-read the JSON file. On failure (missing file, malformed JSON)
        the in-memory mapping is reset to empty."""
        new_by_slug: dict[str, list[dict]] = defaultdict(list)
        if not os.path.exists(self._path):
            logger.warning(f"Polymarket mapping file not found: {self._path}")
            self._by_slug = new_by_slug
            return
        try:
            with open(self._path) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Polymarket mapping load failed: {e}")
            self._by_slug = new_by_slug
            return
        if not isinstance(raw, list):
            logger.warning("Polymarket mapping JSON must be a list")
            self._by_slug = new_by_slug
            return
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("polymarket_slug")
            prefix = entry.get("kalshi_ticker_prefix")
            match = entry.get("outcome_match", "direct")
            if not slug or not prefix:
                continue
            if match not in ("direct", "reversed"):
                logger.warning(f"Invalid outcome_match for {slug}: {match}")
                continue
            new_by_slug[slug].append({
                "kalshi_ticker_prefix": prefix,
                "outcome_match": match,
                "notes": entry.get("notes", ""),
            })
        self._by_slug = new_by_slug

    def all_slugs(self) -> list[str]:
        return list(self._by_slug.keys())

    def entries_for_slug(self, slug: str) -> list[dict]:
        """Returns list of {kalshi_ticker_prefix, outcome_match, notes} dicts."""
        return list(self._by_slug.get(slug, []))
