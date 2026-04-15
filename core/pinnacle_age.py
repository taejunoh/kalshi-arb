"""Tracks time since each (region, sport, pinnacle_hash) triple was first seen.

Used to measure Pinnacle feed freshness: when consensus is recomputed, we
record how long the same Pinnacle payload has been live. Older age = stale.
"""
import time
from typing import Optional


class PinnacleAgeTracker:
    def __init__(self):
        # key: (region, sport, hash) -> first_seen_monotonic_seconds
        self._first_seen: dict[tuple[str, str, str], float] = {}

    def observe(
        self,
        region: str,
        sport: str,
        pinnacle_hash: Optional[str],
        now: Optional[float] = None,
    ) -> Optional[int]:
        """Return integer seconds since this hash was first seen, or None
        if `pinnacle_hash` is None. Records `now` for unseen hashes."""
        if pinnacle_hash is None:
            return None
        t = now if now is not None else time.monotonic()
        key = (region, sport, pinnacle_hash)
        first = self._first_seen.get(key)
        if first is None:
            self._first_seen[key] = t
            return 0
        return int(t - first)
