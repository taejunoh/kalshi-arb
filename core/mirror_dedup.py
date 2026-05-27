"""Mirror-pair dedup utility — standardize n calculations across backtests.

Many Kalshi sports markets have mirror tickers — the same game gets two
listings, one per side:
  KXNPBGAME-26MAY270500TOHCHU-TOH  (Hanshin home line)
  KXNPBGAME-26MAY270500TOHCHU-CHU  (mirror)
  KXATPMATCH-26MAY24FARSHA-SHA     (Player SHA)
  KXATPMATCH-26MAY24FARSHA-FAR     (mirror)

A strategy that fires on a Pinnacle move signal will typically fire on
BOTH sides of the mirror (because the move is symmetric: +3pp UP for
team A == +3pp DN for team B). Backtests that don't dedup count each
match twice, inflating n by ~2x and EV variance by ~sqrt(2).

The [[pinnacle_strategies_mirror_dedup_2026-05-26]] entry documents the
correction applied to tennis_pinnacle_fade + npb_sharp_follow live
strategies via PAIR_FIRE_DEDUP=True. This module is the BACKTEST-side
companion — strip mirror-pair inflation when computing reported metrics.

For 3-way markets (soccer HOME/AWAY/DRAW), the third ticker is NOT a
mirror — outcomes don't determine each other. `dedup_by_match` keeps
3-way matches separate by default; pass `aggressive=True` if you want
to collapse 3+ tickers per match.
"""
from __future__ import annotations
from collections import defaultdict
import re
from typing import Optional

# Pattern: any Kalshi series prefix + match identifier, then "-XXX" entity
# code (player initials, team code) at end. The match_key strips the
# trailing -XXX.
# Example matches:
#   KXNPBGAME-26MAY270500TOHCHU-CHU -> match_key = KXNPBGAME-26MAY270500TOHCHU
#   KXATPMATCH-26MAY24FARSHA-SHA    -> match_key = KXATPMATCH-26MAY24FARSHA
_MATCH_KEY_PATTERN = re.compile(r"^(KX[A-Z]+(?:GAME|MATCH|FIGHT)-[A-Z0-9]+)-[A-Z0-9]{2,5}$")


def match_key(ticker: str) -> Optional[str]:
    """Return the shared match identifier for a Kalshi sports ticker.

    Mirror-paired tickers (same game, opposite side) share a match_key.
    Returns None if ticker doesn't match the expected sports pattern
    (e.g., crypto, econ, or unknown series format).
    """
    if not ticker:
        return None
    m = _MATCH_KEY_PATTERN.match(ticker)
    return m.group(1) if m else None


def group_by_match(rows: list, ticker_key: str = "ticker") -> dict:
    """Group rows by their match_key. Rows with no match_key are skipped.

    Returns dict {match_key: [rows]}.
    """
    grouped: dict = defaultdict(list)
    for row in rows:
        # Support dict-like and tuple-like rows
        if hasattr(row, "get"):
            ticker = row.get(ticker_key)
        elif isinstance(row, (tuple, list)):
            ticker = row[0]
        else:
            ticker = getattr(row, ticker_key, None)
        mk = match_key(ticker)
        if mk:
            grouped[mk].append(row)
    return dict(grouped)


def dedup_by_match(rows: list, ticker_key: str = "ticker",
                   prefer: str = "first",
                   aggressive: bool = False) -> list:
    """Reduce mirror-paired rows to one row per unique match.

    Args:
        rows: list of dicts/tuples/objects, each must have a ticker field.
        ticker_key: key to look up the ticker on each row (default 'ticker').
        prefer: which side to keep when 2 tickers share a match_key.
                'first' = keep input order. 'random' = nondeterministic.
        aggressive: if True, collapse ANY group >= 2 tickers per match.
                    Default False = only collapse exactly 2-ticker groups
                    (preserves 3-way soccer markets that have HOME/AWAY/DRAW).

    Returns: deduplicated list.
    """
    grouped = group_by_match(rows, ticker_key)
    seen_keys = set()
    deduped = []
    for row in rows:
        if hasattr(row, "get"):
            ticker = row.get(ticker_key)
        elif isinstance(row, (tuple, list)):
            ticker = row[0]
        else:
            ticker = getattr(row, ticker_key, None)
        mk = match_key(ticker)
        if not mk:
            # Unknown ticker pattern — keep as-is (don't dedup).
            deduped.append(row)
            continue
        group = grouped.get(mk, [])
        # In aggressive mode, dedup all groups with 2+ tickers.
        # In default mode, only dedup pure 2-ticker mirror groups.
        if (aggressive and len(group) >= 2) or (not aggressive and len(group) == 2):
            if mk in seen_keys:
                continue
            seen_keys.add(mk)
        deduped.append(row)
    return deduped


def mirror_stats(rows: list, ticker_key: str = "ticker") -> dict:
    """Diagnostic — count solo / pair / 3-plus matches in a row list.

    Useful for verifying dedup is needed before applying it. Returns:
        {
          "total_rows": int,
          "unique_matches": int,
          "solo_matches": int,    # 1 ticker per match (e.g. one side never fired)
          "paired_matches": int,  # 2 tickers (typical mirror)
          "multi_matches": int,   # 3+ tickers (3-way soccer)
          "inflation_ratio": float  # total_rows / unique_matches
        }
    """
    grouped = group_by_match(rows, ticker_key)
    counts = {1: 0, 2: 0, 3: 0}
    for group in grouped.values():
        n = len(group)
        if n == 1: counts[1] += 1
        elif n == 2: counts[2] += 1
        else: counts[3] += 1
    total = sum(len(g) for g in grouped.values())
    unique = len(grouped)
    return {
        "total_rows": total,
        "unique_matches": unique,
        "solo_matches": counts[1],
        "paired_matches": counts[2],
        "multi_matches": counts[3],
        "inflation_ratio": total / unique if unique else 0.0,
    }
