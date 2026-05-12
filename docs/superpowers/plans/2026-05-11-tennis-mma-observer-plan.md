# Tennis + MMA Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire observer-only data collection for `KXATPMATCH`, `KXWTAMATCH`, `KXUFCFIGHT` Kalshi series into the existing sports observer pipeline so that 14 days of clean per-match consensus data accumulate, then extend `promotion_eval.py` to output a 4-archetype bucket replay (longshot / underdog / mid-fav / chalk) per sport on the weekly Monday cron.

**Architecture:** Mirrors the v37 soccer pattern (commit `e936555`). Three new entries to the existing sport-token / sport-map / observation-only registries, a single new helper method for individual-sport name matching (since the team-name-city-prefix matcher won't work for player names), and additive sections to `promotion_eval` and `daily_data_quality`. No schema migrations. No strategy files (deferred to follow-up spec at T+14).

**Tech Stack:** Python 3.11, sqlite3, asyncio, pytest. Odds API endpoints (existing client `core.kalshi_client`). Existing tables `odds_observations`, `ticker_settlements`.

**Spec reference:** [docs/superpowers/specs/2026-05-11-tennis-mma-observer-design.md](../specs/2026-05-11-tennis-mma-observer-design.md)

---

## Task 0: Verify Odds API endpoint identifiers (read-only research)

Confirms the exact Odds API endpoint slugs before any code change. The spec assumed `tennis_atp_italian_open` and `mma_mixed_martial_arts` but the live Odds API endpoint list may have different identifiers (e.g., `tennis_atp_rome` for the same tournament, or seasonal slugs that rotate).

**Files:**
- Create: `scripts/research/verify_odds_api_tennis_mma_endpoints.py` (research-only, do not check in)

- [ ] **Step 1: Write the probe script**

Save to `scripts/research/verify_odds_api_tennis_mma_endpoints.py`:

```python
"""One-shot probe: list Odds API endpoint slugs containing tennis or mma."""
import os
import sys
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

API_KEY = os.environ.get("ODDS_API_KEY")
if not API_KEY:
    raise SystemExit("Set ODDS_API_KEY env var (use prod key from .env)")

r = httpx.get(
    "https://api.the-odds-api.com/v4/sports",
    params={"apiKey": API_KEY, "all": "true"},
    timeout=20,
)
r.raise_for_status()
sports = r.json()
print(f"Total endpoints: {len(sports)}")
for s in sports:
    key = s.get("key", "")
    if "tennis" in key.lower() or "mma" in key.lower():
        print(f"  {key}  active={s.get('active')}  title={s.get('title')}  group={s.get('group')}")
```

- [ ] **Step 2: Run the script on EC2 (prod env has the API key)**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && ODDS_API_KEY=$(grep '^ODDS_API_KEY=' .env | cut -d= -f2) \
   /home/ubuntu/kalshi-arb/venv/bin/python scripts/research/verify_odds_api_tennis_mma_endpoints.py"
```

Record the actual `key` strings for:
- Currently active tennis tournament (look for `active=True`, contains `tennis_atp` or `tennis_wta`)
- MMA (look for `mma_mixed_martial_arts` or similar)

- [ ] **Step 3: Update Task 3 with the verified endpoint slugs**

Edit the plan inline — replace the placeholder slugs in Task 3 with the verified ones. (This is the only place in the plan where the slugs appear.)

- [ ] **Step 4: Discard the research script**

```bash
rm scripts/research/verify_odds_api_tennis_mma_endpoints.py
```

No commit — research script was never staged.

---

## Task 1: Add Kalshi series + tokens to sports_trader.py

Three constants need entries: `SPORTS_SERIES`, `SPORT_GAME_TOKENS`, `SPORT_SHORT_BY_TOKEN`. The substring-collision rule is the only ordering concern — verify `ATPMATCH`, `WTAMATCH`, `UFCFIGHT` don't conflict with existing tokens.

**Files:**
- Modify: `strategies/sports_trader.py:131-166`
- Test: `tests/test_sport_tokens_tennis_mma.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sport_tokens_tennis_mma.py`:

```python
"""Sport tokens for Tennis (ATP/WTA) + MMA (UFC) — added 2026-05-11."""
from strategies.sports_trader import (
    SPORTS_SERIES,
    SPORT_GAME_TOKENS,
    SPORT_SHORT_BY_TOKEN,
)


def test_kxatpmatch_in_sports_series():
    assert "KXATPMATCH" in SPORTS_SERIES


def test_kxwtamatch_in_sports_series():
    assert "KXWTAMATCH" in SPORTS_SERIES


def test_kxufcfight_in_sports_series():
    assert "KXUFCFIGHT" in SPORTS_SERIES


def test_atpmatch_in_sport_game_tokens():
    assert "ATPMATCH" in SPORT_GAME_TOKENS


def test_wtamatch_in_sport_game_tokens():
    assert "WTAMATCH" in SPORT_GAME_TOKENS


def test_ufcfight_in_sport_game_tokens():
    assert "UFCFIGHT" in SPORT_GAME_TOKENS


def test_atpmatch_short_is_atp():
    assert SPORT_SHORT_BY_TOKEN["ATPMATCH"] == "ATP"


def test_wtamatch_short_is_wta():
    assert SPORT_SHORT_BY_TOKEN["WTAMATCH"] == "WTA"


def test_ufcfight_short_is_ufc():
    assert SPORT_SHORT_BY_TOKEN["UFCFIGHT"] == "UFC"


def test_no_substring_collision_in_sport_game_tokens():
    """For first-match-wins extraction, no token may be a prefix substring
    of a later token. ATPMATCH/WTAMATCH/UFCFIGHT must not collide."""
    for i, ti in enumerate(SPORT_GAME_TOKENS):
        for tj in SPORT_GAME_TOKENS[i+1:]:
            # If ti is a substring of tj, tj must come first (already broken)
            assert ti not in tj, (
                f"Token order bug: '{ti}' at position {i} is substring of '{tj}' "
                f"at later position — first-match extraction would misclassify"
            )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py -v
```

Expected: 9 FAIL (KXATPMATCH/WTAMATCH/UFCFIGHT not yet in tuples), 1 PASS (collision check passes for existing tokens, will pass after add too).

- [ ] **Step 3: Add the three series to SPORTS_SERIES**

Edit `strategies/sports_trader.py:131-135`:

```python
SPORTS_SERIES = [
    "KXNBAGAME", "KXWNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXNPBGAME", "KXKBOGAME",
    "KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME", "KXUCLGAME", "KXBUNDESLIGAGAME",
    "KXATPMATCH", "KXWTAMATCH", "KXUFCFIGHT",  # NEW 2026-05-11 — individual-sport h2h match markets
]
```

- [ ] **Step 4: Add tokens to SPORT_GAME_TOKENS**

Edit `strategies/sports_trader.py:149-154`:

```python
SPORT_GAME_TOKENS = (
    "WNBAGAME",  # MUST come before NBAGAME — substring collision
    "NBAGAME", "MLBGAME", "NHLGAME",
    "NPBGAME", "KBOGAME",
    "MLSGAME", "EPLGAME", "LALIGAGAME", "UCLGAME", "BUNDESLIGAGAME",
    "ATPMATCH", "WTAMATCH", "UFCFIGHT",  # NEW 2026-05-11 — individual-sport h2h
)
```

- [ ] **Step 5: Add shorts to SPORT_SHORT_BY_TOKEN**

Edit `strategies/sports_trader.py:160-166`:

```python
SPORT_SHORT_BY_TOKEN = {
    "WNBAGAME": "WNBA",
    "NBAGAME": "NBA", "MLBGAME": "MLB", "NHLGAME": "NHL",
    "NPBGAME": "NPB", "KBOGAME": "KBO",
    "MLSGAME": "MLS", "EPLGAME": "EPL", "LALIGAGAME": "LAL",
    "UCLGAME": "UCL", "BUNDESLIGAGAME": "BUN",
    "ATPMATCH": "ATP", "WTAMATCH": "WTA", "UFCFIGHT": "UFC",  # NEW 2026-05-11
}
```

- [ ] **Step 6: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py -v
```

Expected: 10 PASS

- [ ] **Step 7: Run adjacent existing tests to verify no regression**

```bash
venv/bin/python -m pytest tests/test_realized_fill_logging.py tests/test_dedup_rollback.py tests/test_sports_chalk_shadow.py -q
```

Expected: All previously-passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add tests/test_sport_tokens_tennis_mma.py strategies/sports_trader.py
git commit -m "feat(observer): add ATP/WTA/UFC sport tokens for individual-sport observer

Three new Kalshi series — KXATPMATCH, KXWTAMATCH, KXUFCFIGHT — added to
SPORTS_SERIES, SPORT_GAME_TOKENS, SPORT_SHORT_BY_TOKEN. Substring-collision
test added to prevent future ordering bugs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Register Tennis + MMA as observation-only

Adds ATP / WTA / UFC to `OBSERVATION_ONLY_SPORTS` so strategies skip these tickers in `scan_and_execute`. The observer ([sports_scan_observer.py](../../../strategies/sports_scan_observer.py)) does NOT honor this set — it logs everything — which is the desired behavior.

**Files:**
- Modify: `strategies/sports_trader.py:381-387`
- Test: append to `tests/test_sport_tokens_tennis_mma.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sport_tokens_tennis_mma.py`:

```python
from strategies.sports_trader import SportsTraderStrategy


def test_atp_is_observation_only():
    assert "ATP" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_wta_is_observation_only():
    assert "WTA" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_ufc_is_observation_only():
    assert "UFC" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py::test_atp_is_observation_only tests/test_sport_tokens_tennis_mma.py::test_wta_is_observation_only tests/test_sport_tokens_tennis_mma.py::test_ufc_is_observation_only -v
```

Expected: 3 FAIL

- [ ] **Step 3: Add to OBSERVATION_ONLY_SPORTS**

Edit `strategies/sports_trader.py:381-387`:

```python
    OBSERVATION_ONLY_SPORTS: frozenset = frozenset({
        "WNBA",  # 2026-05-09 wired (KXWNBAGAME). Observe through ~5/16 opener
                 # + first 1-2 weeks before any promotion. Pinnacle line quality
                 # on women's basketball historically thinner than men's.
        "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        # 2026-05-11: Individual-sport observers. ATP/WTA tennis + UFC MMA.
        # 14-day data window → promotion_eval bucket-replay decides archetype
        # (longshot 5-25¢ / underdog 25-39¢ / mid-fav 39-75¢ / chalk 75+).
        # No strategy file yet — that's the follow-up spec.
        "ATP", "WTA", "UFC",
    })
```

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py -v
```

Expected: 13 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_sport_tokens_tennis_mma.py strategies/sports_trader.py
git commit -m "feat(observer): mark ATP/WTA/UFC as observation-only sports

Strategies skip these via OBSERVATION_ONLY_SPORTS gate; observer logs
everything for 14-day data accumulation. Strategy archetype decision
follows after promotion_eval bucket-replay report.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add SPORT_MAP entries to observer

The observer's `SPORT_MAP` resolves Kalshi series → Odds API endpoint slug. Tennis has tournament-specific slugs (one slug per active tournament), MMA has a single slug.

**Use the slugs verified in Task 0.** The placeholders below assume `tennis_atp_italian_open`, `tennis_wta_italian_open`, `mma_mixed_martial_arts` — adjust per Task 0's actual finding.

**Files:**
- Modify: `strategies/sports_scan_observer.py:35-60`
- Test: `tests/test_observer_sport_map_tennis_mma.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observer_sport_map_tennis_mma.py`:

```python
"""Observer SPORT_MAP entries for Tennis + MMA — added 2026-05-11."""
from strategies.sports_scan_observer import SPORT_MAP, SPORT_SHORT


def test_kxatpmatch_maps_to_tennis_atp_endpoint():
    # The exact tournament slug rotates with the tour calendar; this test
    # asserts the slug pattern is correct rather than a specific tournament.
    slug = SPORT_MAP.get("KXATPMATCH")
    assert slug is not None, "KXATPMATCH must be mapped to an Odds API slug"
    assert slug.startswith("tennis_atp_"), f"Expected tennis_atp_* slug, got {slug!r}"


def test_kxwtamatch_maps_to_tennis_wta_endpoint():
    slug = SPORT_MAP.get("KXWTAMATCH")
    assert slug is not None
    assert slug.startswith("tennis_wta_"), f"Expected tennis_wta_* slug, got {slug!r}"


def test_kxufcfight_maps_to_mma_endpoint():
    slug = SPORT_MAP.get("KXUFCFIGHT")
    assert slug is not None
    assert "mma" in slug, f"Expected mma slug, got {slug!r}"


def test_kxatpmatch_short_in_observer():
    assert SPORT_SHORT.get("KXATPMATCH") == "ATP"


def test_kxwtamatch_short_in_observer():
    assert SPORT_SHORT.get("KXWTAMATCH") == "WTA"


def test_kxufcfight_short_in_observer():
    assert SPORT_SHORT.get("KXUFCFIGHT") == "UFC"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_observer_sport_map_tennis_mma.py -v
```

Expected: 6 FAIL (entries missing).

- [ ] **Step 3: Extend SPORT_MAP and SPORT_SHORT**

Edit `strategies/sports_scan_observer.py:35-60`:

```python
SPORT_MAP = {
    "KXNBAGAME": "basketball_nba",
    "KXWNBAGAME": "basketball_wnba",
    "KXMLBGAME": "baseball_mlb",
    "KXNHLGAME": "icehockey_nhl",
    "KXNPBGAME": "baseball_npb",
    "KXKBOGAME": "baseball_kbo",
    "KXMLSGAME": "soccer_usa_mls",
    "KXEPLGAME": "soccer_epl",
    "KXLALIGAGAME": "soccer_spain_la_liga",
    "KXUCLGAME": "soccer_uefa_champs_league",
    "KXBUNDESLIGAGAME": "soccer_germany_bundesliga",
    # 2026-05-11: Individual-sport observers. Tennis slug rotates per
    # currently-active tournament (manually updated; rotation cron in Task 9
    # alerts when active slug returns 0 events). Verified slugs from Task 0:
    "KXATPMATCH": "tennis_atp_italian_open",  # replace with Task 0 verified slug
    "KXWTAMATCH": "tennis_wta_italian_open",  # replace with Task 0 verified slug
    "KXUFCFIGHT": "mma_mixed_martial_arts",   # replace with Task 0 verified slug
}
SPORT_SHORT = {
    "KXNBAGAME": "NBA",
    "KXWNBAGAME": "WNBA",
    "KXMLBGAME": "MLB",
    "KXNHLGAME": "NHL",
    "KXNPBGAME": "NPB",
    "KXKBOGAME": "KBO",
    "KXMLSGAME": "MLS",
    "KXEPLGAME": "EPL",
    "KXLALIGAGAME": "LAL",
    "KXUCLGAME": "UCL",
    "KXBUNDESLIGAGAME": "BUN",
    "KXATPMATCH": "ATP",
    "KXWTAMATCH": "WTA",
    "KXUFCFIGHT": "UFC",
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_observer_sport_map_tennis_mma.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_observer_sport_map_tennis_mma.py strategies/sports_scan_observer.py
git commit -m "feat(observer): wire ATP/WTA/UFC into observer SPORT_MAP

Tennis slug rotates per active tournament — manual maintenance covered
by rotation alert cron in follow-up task. MMA uses single year-round slug.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add individual-sport name matcher

The existing `_match_kalshi_to_odds` uses city-prefix matching from team-sport title formats ("Phoenix at Charlotte"). Tennis/MMA titles use "Will {FullName} win the {Last1} vs {Last2}..." — different structure. Need a dedicated matcher that uses `yes_sub_title` (full player/fighter name) against the Odds API `home_team` / `away_team` fields directly.

**Files:**
- Modify: `strategies/sports_trader.py` (new method on `SportsTraderStrategy`, near `_match_kalshi_to_odds` at line 902)
- Test: `tests/test_individual_sport_matcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_individual_sport_matcher.py`:

```python
"""Individual-sport (Tennis/MMA) name matching — added 2026-05-11.

Tennis/MMA Kalshi markets have a structure incompatible with the
team-sport _match_kalshi_to_odds (city-prefix). Use yes_sub_title against
Odds API home_team/away_team directly.
"""
from strategies.sports_trader import _match_individual_sport_to_odds


def test_exact_full_name_match_atp():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("carlos alcaraz", odds_games)
    assert g is not None
    assert g["home_team"] == "Carlos Alcaraz"


def test_exact_full_name_match_wta():
    odds_games = [{
        "home_team": "Coco Gauff",
        "away_team": "Mirra Andreeva",
        "commence_time": "2026-05-12T13:00:00Z",
    }]
    g = _match_individual_sport_to_odds("mirra andreeva", odds_games)
    assert g is not None
    assert g["away_team"] == "Mirra Andreeva"


def test_token_overlap_with_extra_pinnacle_token():
    """Pinnacle sometimes carries a longer form. Kalshi's 'Carlos Alcaraz'
    must still match Pinnacle's 'Carlos Alcaraz Garfia'."""
    odds_games = [{
        "home_team": "Carlos Alcaraz Garfia",
        "away_team": "Daniil Medvedev",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("carlos alcaraz", odds_games)
    assert g is not None


def test_token_overlap_with_extra_kalshi_token():
    """Inverse: Kalshi carries a longer form like 'Thiago Agustin Tirante'.
    Pinnacle 'Tirante' should still match. (Defensive — Pinnacle usually has
    the longer name, but handle both directions.)"""
    odds_games = [{
        "home_team": "Tirante",
        "away_team": "Daniil Medvedev",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("thiago agustin tirante", odds_games)
    assert g is not None


def test_no_match_returns_none():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("rafael nadal", odds_games)
    assert g is None


def test_empty_odds_list_returns_none():
    g = _match_individual_sport_to_odds("carlos alcaraz", [])
    assert g is None


def test_empty_yes_sub_title_returns_none():
    odds_games = [{
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-05-12T15:00:00Z",
    }]
    g = _match_individual_sport_to_odds("", odds_games)
    assert g is None


def test_ufc_fighter_match():
    odds_games = [{
        "home_team": "Jeremiah Wells",
        "away_team": "Nicolas Dalby",
        "commence_time": "2026-05-16T22:00:00Z",
    }]
    g = _match_individual_sport_to_odds("jeremiah wells", odds_games)
    assert g is not None
    assert g["home_team"] == "Jeremiah Wells"


def test_disambiguates_same_player_two_events_by_ticker_date():
    """When a player has matches on different dates (e.g., R32 Mon + R16 Wed),
    the kalshi_ticker date narrows to the right game."""
    odds_games = [
        {
            "home_team": "Carlos Alcaraz",
            "away_team": "Jannik Sinner",
            "commence_time": "2026-05-12T15:00:00Z",
        },
        {
            "home_team": "Carlos Alcaraz",
            "away_team": "Daniil Medvedev",
            "commence_time": "2026-05-14T15:00:00Z",
        },
    ]
    g = _match_individual_sport_to_odds(
        "carlos alcaraz", odds_games,
        kalshi_ticker="KXATPMATCH-26MAY14ALCMED-ALC",
    )
    assert g is not None
    assert g["away_team"] == "Daniil Medvedev"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_individual_sport_matcher.py -v
```

Expected: All 9 FAIL — `_match_individual_sport_to_odds` not yet defined.

- [ ] **Step 3: Implement the matcher**

Add to `strategies/sports_trader.py`, immediately after the `_parse_kalshi_ticker_datetime` helper (search for that function near line ~860 — add the new function right after it as a module-level helper):

```python
def _match_individual_sport_to_odds(
    yes_sub_title: str,
    odds_games: list[dict],
    kalshi_ticker: Optional[str] = None,
) -> Optional[dict]:
    """Match a Kalshi individual-sport market to Odds API game data.

    Kalshi yes_sub_title is the full player/fighter name (e.g.,
    'Carlos Alcaraz', 'Jeremiah Wells'). Match by:
      1. Exact (case-insensitive) match against home_team or away_team
      2. Token-overlap fallback: every space-token of the SHORTER name must
         appear in the LONGER name (handles 'Carlos Alcaraz' vs
         'Carlos Alcaraz Garfia' both directions)

    When the same player has multiple matches on different dates, the
    kalshi_ticker date disambiguates — same logic as _match_kalshi_to_odds.
    """
    if not yes_sub_title or not odds_games:
        return None
    yes_lc = yes_sub_title.strip().lower()
    if not yes_lc:
        return None
    yes_tokens = set(yes_lc.split())

    def _name_matches(odds_name: str) -> bool:
        if not odds_name:
            return False
        on_lc = odds_name.strip().lower()
        if on_lc == yes_lc:
            return True
        on_tokens = set(on_lc.split())
        if not on_tokens:
            return False
        # Shorter-token set must be a subset of longer-token set
        if len(yes_tokens) <= len(on_tokens):
            return yes_tokens.issubset(on_tokens)
        return on_tokens.issubset(yes_tokens)

    candidates = [
        g for g in odds_games
        if _name_matches(g.get("home_team") or "")
        or _name_matches(g.get("away_team") or "")
    ]
    if not candidates:
        return None
    if len(candidates) == 1 or not kalshi_ticker:
        return candidates[0]
    ticker_dt = _parse_kalshi_ticker_datetime(kalshi_ticker)
    if not ticker_dt:
        return candidates[0]

    def _delta_seconds(g: dict) -> float:
        ct = g.get("commence_time")
        if not ct:
            return float("inf")
        try:
            game_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return float("inf")
        return abs((game_dt - ticker_dt).total_seconds())

    return min(candidates, key=_delta_seconds)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_individual_sport_matcher.py -v
```

Expected: 9 PASS

- [ ] **Step 5: Run adjacent tests for regression**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py tests/test_observer_sport_map_tennis_mma.py -q
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_individual_sport_matcher.py strategies/sports_trader.py
git commit -m "feat(observer): add _match_individual_sport_to_odds for Tennis/MMA

Module-level helper matches Kalshi yes_sub_title (full player/fighter
name) against Odds API home_team/away_team. Direct match fast path;
token-overlap fallback handles 'Carlos Alcaraz' vs 'Carlos Alcaraz Garfia'
in both directions. Ticker-date disambiguation reuses the existing
_parse_kalshi_ticker_datetime helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Branch observer flow to use individual-sport matcher for new series

The observer loop calls `self._match_kalshi_to_odds(...)` for every market. For ATP/WTA/UFC tickers, switch to the new matcher and pass `yes_sub_title` instead of `title`.

**Files:**
- Modify: `strategies/sports_scan_observer.py:108-145`
- Test: `tests/test_observer_routes_individual_sport_matcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observer_routes_individual_sport_matcher.py`:

```python
"""Verify observer routes ATP/WTA/UFC markets through the individual-sport
matcher rather than the team-sport _match_kalshi_to_odds (which would never
match because tennis/MMA titles lack city names)."""
from unittest.mock import MagicMock, patch
import asyncio
import pytest

# Smoke-level test: feed a fixture into the helper used inside the observer
# loop and verify the right matcher fires for the right series.


def test_atp_series_routes_to_individual_matcher():
    """The observer's `_get_matcher_for_series` returns the individual-sport
    matcher for KXATPMATCH (and team matcher for everything else)."""
    from strategies.sports_scan_observer import _matcher_for_series
    from strategies.sports_trader import _match_individual_sport_to_odds

    assert _matcher_for_series("KXATPMATCH") is _match_individual_sport_to_odds
    assert _matcher_for_series("KXWTAMATCH") is _match_individual_sport_to_odds
    assert _matcher_for_series("KXUFCFIGHT") is _match_individual_sport_to_odds


def test_team_sport_series_routes_to_team_matcher_sentinel():
    """For team-sport series the helper returns None — observer flow uses
    the instance method self._match_kalshi_to_odds."""
    from strategies.sports_scan_observer import _matcher_for_series

    assert _matcher_for_series("KXNBAGAME") is None
    assert _matcher_for_series("KXMLBGAME") is None
    assert _matcher_for_series("KXEPLGAME") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_observer_routes_individual_sport_matcher.py -v
```

Expected: 2 FAIL — `_matcher_for_series` doesn't exist.

- [ ] **Step 3: Add the dispatch helper to the observer module**

Edit `strategies/sports_scan_observer.py`. Add after the SPORT_MAP/SPORT_SHORT constants (around line 60), and import the individual-sport matcher at the top:

In the imports block at top:
```python
from strategies.sports_trader import (
    SPORTS_SERIES,
    ODDS_TO_KALSHI_SHORT,
    SportsTraderStrategy,
    _commence_time_to_minutes,
    _match_individual_sport_to_odds,
)
```

After SPORT_SHORT (around line 60):
```python
INDIVIDUAL_SPORT_SERIES = frozenset({"KXATPMATCH", "KXWTAMATCH", "KXUFCFIGHT"})


def _matcher_for_series(series: str):
    """Return the matcher function for `series`, or None for team-sport
    series (which use the instance method self._match_kalshi_to_odds).
    Team-sport matcher cannot be returned because it's a method that
    needs `self`; this dispatch sentinel keeps the call site simple."""
    if series in INDIVIDUAL_SPORT_SERIES:
        return _match_individual_sport_to_odds
    return None
```

- [ ] **Step 4: Wire the dispatch into the scan loop**

In `strategies/sports_scan_observer.py`, replace the `_match_kalshi_to_odds` call inside the `for market in markets` loop (currently at line ~135):

```python
                # OLD:
                # matched_game = self._match_kalshi_to_odds(
                #     title, odds_games, kalshi_ticker=ticker
                # )

                # NEW:
                ind_matcher = _matcher_for_series(series)
                if ind_matcher is not None:
                    yes_sub_for_match = market.get("yes_sub_title") or ""
                    matched_game = ind_matcher(
                        yes_sub_for_match, odds_games, kalshi_ticker=ticker,
                    )
                else:
                    matched_game = self._match_kalshi_to_odds(
                        title, odds_games, kalshi_ticker=ticker,
                    )
```

For individual sports we ALSO need to handle the `is_yes_home` / `is_yes_draw` block below (around line 152-180), which currently assumes city-prefix team match. For individual sports the matching has already been done by yes_sub_title — set `is_yes_home` directly:

After the matched_game is found (around line 145, after the `if not matched_game: continue`):

```python
                # For individual sports, is_yes_home is determined by
                # which side of the matched game the yes_sub_title is on.
                if series in INDIVIDUAL_SPORT_SERIES:
                    yst_lc = (market.get("yes_sub_title") or "").lower()
                    home_lc = (matched_game.get("home_team") or "").lower()
                    yes_tok = set(yst_lc.split())
                    home_tok = set(home_lc.split())
                    # Token-overlap (same rule as the matcher uses)
                    if yst_lc == home_lc or (yes_tok and home_tok and (
                        yes_tok.issubset(home_tok) or home_tok.issubset(yes_tok)
                    )):
                        is_yes_home = True
                    else:
                        is_yes_home = False
                    is_yes_draw = False
                    # Skip the team-sport home/away/draw detection block
                    yes_sub_title_for_record = market.get("yes_sub_title") or ""
                else:
                    yes_sub_title_for_record = (market.get("yes_sub_title") or "").lower()
                    # ... existing team-sport detection block runs as-is
```

You'll need to restructure the existing block so it only runs for non-individual sport series. The cleanest approach: wrap the existing team-sport detection (lines ~150-180) in an `else:` branch. The `yes_sub_title` variable feeding into `_yes_side` and `record_odds_observation` may differ — for individual sports, preserve the **original casing** of the full name (Pinnacle uses original casing in its `home_team`/`away_team`, but downstream consumers compare lowercased — keep lowercased for the record).

Concretely, the block from ~148 to ~180 becomes:

```python
                yes_sub_title = (market.get("yes_sub_title") or "").lower()
                is_yes_home: Optional[bool] = None
                is_yes_draw: Optional[bool] = None

                if series in INDIVIDUAL_SPORT_SERIES:
                    # Already matched by yes_sub_title; set is_yes_home by
                    # which side of the matched game corresponds.
                    home_lc = (matched_game.get("home_team") or "").lower()
                    yes_tok = set(yes_sub_title.split())
                    home_tok = set(home_lc.split())
                    if yes_sub_title == home_lc or (yes_tok and home_tok and (
                        yes_tok.issubset(home_tok) or home_tok.issubset(yes_tok)
                    )):
                        is_yes_home = True
                    else:
                        is_yes_home = False
                    is_yes_draw = False
                else:
                    # Existing team-sport home/away/draw detection
                    if yes_sub_title == "tie":
                        is_yes_home = False
                        is_yes_draw = True
                    elif home_team and away_team:
                        # ... existing block continues unchanged
```

Move the existing `home_team`/`away_team` variable assignments BEFORE the if/else so both branches can use them:

```python
                home_team = matched_game.get("home_team")
                away_team = matched_game.get("away_team")
                yes_sub_title = (market.get("yes_sub_title") or "").lower()

                is_yes_home: Optional[bool] = None
                is_yes_draw: Optional[bool] = None

                if series in INDIVIDUAL_SPORT_SERIES:
                    # ... individual-sport branch
                else:
                    # ... team-sport branch (existing)
```

- [ ] **Step 5: Run test to verify dispatch helper passes**

```bash
venv/bin/python -m pytest tests/test_observer_routes_individual_sport_matcher.py -v
```

Expected: 2 PASS

- [ ] **Step 6: Run all observer-adjacent tests**

```bash
venv/bin/python -m pytest tests/test_sport_tokens_tennis_mma.py tests/test_observer_sport_map_tennis_mma.py tests/test_observer_routes_individual_sport_matcher.py tests/test_individual_sport_matcher.py tests/test_realized_fill_logging.py -q
```

Expected: All pass. If any team-sport test breaks, the restructuring in Step 4 introduced a regression — re-check that the team-sport branch is reached when series is in `SPORT_MAP` but not in `INDIVIDUAL_SPORT_SERIES`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_observer_routes_individual_sport_matcher.py strategies/sports_scan_observer.py
git commit -m "feat(observer): dispatch individual-sport series to name-based matcher

ATP/WTA/UFC observer rows now match via yes_sub_title (full player/fighter
name) against Odds API home_team/away_team. Team-sport (NBA/MLB/NHL/soccer)
flow unchanged. is_yes_home assignment for individual sports uses the same
token-overlap rule as the matcher.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Extend promotion_eval with bucket-replay sections

`promotion_eval.py` currently iterates `TARGET_SPORTS = ["MLS", "EPL", "LAL", "UCL"]`. Extend to include `ATP`, `WTA`, `UFC` and add a per-sport 4-archetype bucket replay block. The buckets partition by `kalshi_yes_ask` price; each bucket replays "buy YES if eu_yes_prob >= ask + 0.05" → settlement → win-rate + ROI.

**Files:**
- Modify: `scripts/promotion_eval.py` (TARGET_SPORTS at line 78, new bucket helpers, new report section)
- Test: `tests/test_promotion_eval_bucket_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_promotion_eval_bucket_replay.py`:

```python
"""Bucket-replay section in promotion_eval — added 2026-05-11.

Verifies 4-archetype bucket boundaries and per-bucket replay computation
(n, win-rate, ROI) for synthetic ATP entries.
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
    # (lo, hi, label) — verify exactly the 4 spec-defined buckets
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
    # Below 0.05 → None (no archetype)
    assert _bucket_label_for_archetype(0.03) is None
    # >= 1.00 → None (degenerate)
    assert _bucket_label_for_archetype(1.0) is None


def test_replay_archetype_buckets_computes_per_bucket_stats():
    """Synthetic entries: 3 longshot wins, 1 longshot loss; 2 underdog losses."""
    entries = [
        # longshot: ask=0.10, settled YES → win
        {"kalshi_yes_ask": 0.10, "eu_yes_prob": 0.20, "settled_yes": 1},
        {"kalshi_yes_ask": 0.12, "eu_yes_prob": 0.25, "settled_yes": 1},
        {"kalshi_yes_ask": 0.15, "eu_yes_prob": 0.22, "settled_yes": 1},
        {"kalshi_yes_ask": 0.20, "eu_yes_prob": 0.30, "settled_yes": 0},
        # underdog: 0.30 ask, both lose
        {"kalshi_yes_ask": 0.30, "eu_yes_prob": 0.40, "settled_yes": 0},
        {"kalshi_yes_ask": 0.35, "eu_yes_prob": 0.45, "settled_yes": 0},
    ]
    out = _replay_archetype_buckets(entries, n_boot=100)
    longshot = out["longshot"]
    assert longshot["n"] == 4
    assert longshot["wins"] == 3
    # ROI: win pays (1-ask)/ask, lose pays -1. Average across n=4:
    # (((1-0.10)/0.10) + ((1-0.12)/0.12) + ((1-0.15)/0.15) + (-1)) / 4
    # = (9 + 7.333 + 5.667 + -1) / 4 = 5.25
    assert longshot["roi"] == pytest.approx(5.25, abs=0.01)
    underdog = out["underdog"]
    assert underdog["n"] == 2
    assert underdog["wins"] == 0
    assert underdog["roi"] == pytest.approx(-1.0, abs=0.01)
    # mid-fav and chalk had 0 samples → still present, with n=0
    assert out["mid-fav"]["n"] == 0
    assert out["chalk"]["n"] == 0


def test_atp_wta_ufc_in_target_sports():
    from scripts.promotion_eval import TARGET_SPORTS
    assert "ATP" in TARGET_SPORTS
    assert "WTA" in TARGET_SPORTS
    assert "UFC" in TARGET_SPORTS
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_promotion_eval_bucket_replay.py -v
```

Expected: All FAIL — symbols not yet defined.

- [ ] **Step 3: Add constants and helper functions**

Edit `scripts/promotion_eval.py`. Add after the `EDGE_BUCKETS` constant at line 80:

```python
# 2026-05-11: 4-archetype price buckets for individual-sport replay (Tennis/MMA).
# Boundaries chosen to align with existing strategy families:
#   - longshot: 5-25¢   (sports_longshot_v31 band, 3x+ payoff)
#   - underdog: 25-39¢  (sports_underdog_v31/v32 band)
#   - mid-fav:  39-75¢  (no existing strategy — Kalshi-underprices-fav hypothesis)
#   - chalk:    75-100¢ (chalk-shadow territory — retired strategy, kept for diagnostics)
ARCHETYPE_BUCKETS = [
    (0.05, 0.25, "longshot"),
    (0.25, 0.39, "underdog"),
    (0.39, 0.75, "mid-fav"),
    (0.75, 1.00, "chalk"),
]
ARCHETYPE_EDGE_GATE = 0.05   # Replay only includes signals where eu_yes_prob >= ask + 0.05


def _bucket_label_for_archetype(price: float) -> str | None:
    """Return archetype label for a Kalshi YES ask price; None if outside
    all buckets (< 0.05 or >= 1.00)."""
    if price is None:
        return None
    for lo, hi, label in ARCHETYPE_BUCKETS:
        if lo <= price < hi:
            return label
    return None


def _replay_archetype_buckets(entries: list[dict], n_boot: int = 1000) -> dict:
    """Replay synthetic 'buy YES if eu_yes_prob >= ask + ARCHETYPE_EDGE_GATE'
    across 4 archetype buckets. Each entry must have:
      kalshi_yes_ask (float), eu_yes_prob (float), settled_yes (0/1)

    Returns dict[label] = {n, wins, roi, ci_lo, ci_hi}. ROI is contracts-equivalent
    (win pays (1-ask)/ask, lose pays -1).
    """
    out = {}
    for lo, hi, label in ARCHETYPE_BUCKETS:
        bucket_entries = [
            e for e in entries
            if e.get("kalshi_yes_ask") is not None
            and lo <= e["kalshi_yes_ask"] < hi
            and e.get("eu_yes_prob") is not None
            and e["eu_yes_prob"] >= e["kalshi_yes_ask"] + ARCHETYPE_EDGE_GATE
            and e.get("settled_yes") is not None
        ]
        n = len(bucket_entries)
        if n == 0:
            out[label] = {"n": 0, "wins": 0, "roi": None, "ci_lo": None, "ci_hi": None}
            continue
        wins = sum(1 for e in bucket_entries if e["settled_yes"] == 1)
        rois = [
            ((1.0 - e["kalshi_yes_ask"]) / e["kalshi_yes_ask"]) if e["settled_yes"] == 1 else -1.0
            for e in bucket_entries
        ]
        mean_roi = sum(rois) / n
        ci_lo, ci_hi = _bootstrap_ci(rois, n_iter=n_boot)
        out[label] = {
            "n": n, "wins": wins, "roi": mean_roi,
            "ci_lo": ci_lo, "ci_hi": ci_hi,
        }
    return out
```

- [ ] **Step 4: Extend TARGET_SPORTS**

Edit `scripts/promotion_eval.py:78`:

```python
TARGET_SPORTS = ["MLS", "EPL", "LAL", "UCL", "ATP", "WTA", "UFC"]
```

- [ ] **Step 5: Run unit tests to verify bucket helpers pass**

```bash
venv/bin/python -m pytest tests/test_promotion_eval_bucket_replay.py -v
```

Expected: 5 PASS

- [ ] **Step 6: Add report section emitting bucket-replay block**

Edit `scripts/promotion_eval.py`. Find `_build_report` (line 619) — add a new section after `_section_strategy_matrix`:

```python
def _section_archetype_buckets(results: dict) -> list[str]:
    """Per-sport 4-archetype bucket replay block. Output appears after the
    strategy matrix and before legs/CLV."""
    INDIVIDUAL_SPORTS = ["ATP", "WTA", "UFC"]
    has_data = any(
        results.get(s, {}).get("archetype_buckets") for s in INDIVIDUAL_SPORTS
    )
    if not has_data:
        return []
    lines = ["", "<b>🎯 ARCHETYPE BUCKET REPLAY</b> (individual sports)"]
    lines.append("<i>YES-buy replay; entry gate: eu_yes_prob >= ask + 5¢</i>")
    for sport in INDIVIDUAL_SPORTS:
        r = results.get(sport)
        if not r or not r.get("archetype_buckets"):
            continue
        lines.append(f"")
        lines.append(f"  <b>{sport}</b>")
        for lo, hi, label in ARCHETYPE_BUCKETS:
            b = r["archetype_buckets"].get(label, {})
            n = b.get("n", 0)
            if n == 0:
                lines.append(f"    {label:9s} ({int(lo*100):2d}-{int(hi*100):2d}¢): n=0")
                continue
            wins = b["wins"]
            wr = 100.0 * wins / n if n > 0 else 0
            roi = b.get("roi")
            ci_lo = b.get("ci_lo")
            ci_hi = b.get("ci_hi")
            roi_str = f"{roi*100:+6.1f}%" if roi is not None else "—"
            ci_str = (
                f"[{ci_lo*100:+5.1f}%, {ci_hi*100:+5.1f}%]"
                if ci_lo is not None and ci_hi is not None
                else "[—, —]"
            )
            lines.append(
                f"    {label:9s} ({int(lo*100):2d}-{int(hi*100):2d}¢): "
                f"n={n:3d}  WR={wr:4.1f}%  ROI={roi_str}  CI95={ci_str}"
            )
    return lines
```

Wire into `_build_report` (line 619):

```python
def _build_report(results: dict, baselines: dict, window_days: int) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🎯 <b>Sport promotion evaluation — {now_iso}</b>", ""]
    lines += _section_summary(results, baselines)
    lines += _section_strategy_matrix(results)
    lines += _section_archetype_buckets(results)   # NEW
    lines += _section_legs(results)
    lines += _section_clv(results)
    lines += _section_quality(results)
    lines += _section_criteria(window_days)
    return "\n".join(lines)
```

- [ ] **Step 7: Wire bucket-replay into the per-sport pipeline**

Find `_evaluate` (line 597). After `replay = _replay_all_strategies(...)`, add bucket replay computation:

```python
def _evaluate(db_path: str, window_days: int, sport_filter: str | None,
              n_boot: int, ci_required: bool) -> tuple[dict, dict]:
    conn = sqlite3.connect(db_path, timeout=30.0)
    sports = [sport_filter] if sport_filter else TARGET_SPORTS
    results = {}
    INDIVIDUAL_SPORTS = ["ATP", "WTA", "UFC"]
    for sport in sports:
        entries = _load_entries(conn, sport, window_days)
        quality = _load_quality_stats(conn, sport, window_days)
        clv = _load_clv(conn, sport, window_days)
        replay = _replay_all_strategies(entries, n_boot)
        is_3way = quality.get("tie_legs", 0) > 0
        legs = _replay_legs(entries, n_boot) if is_3way else None
        # NEW: archetype bucket replay only for individual sports
        archetype_buckets = (
            _replay_archetype_buckets(entries, n_boot)
            if sport in INDIVIDUAL_SPORTS
            else None
        )
        decision = _decide(quality, replay, len(entries), ci_required)
        results[sport] = {
            "quality": quality, "clv": clv, "replay": replay,
            "legs": legs, "archetype_buckets": archetype_buckets,
            "n_games": len(entries), "decision": decision,
        }
    baselines = _load_live_baselines(conn, window_days)
    conn.close()
    return results, baselines
```

The bucket-replay function expects entries with `settled_yes` field. Check `_load_entries` (line 119) to confirm it joins settlement — it does via subquery on `ticker_settlements`. The field is likely called `settled_yes_outcome` or similar; inspect with:

```bash
grep -n "settled\|outcome" scripts/promotion_eval.py | head -10
```

Adapt the test's `settled_yes` field name to match the actual one in `_load_entries`. If the field is e.g. `outcome` with values `'yes'/'no'`, update the test fixture and `_replay_archetype_buckets` accordingly to read that field.

- [ ] **Step 8: Run full promotion_eval test suite**

```bash
venv/bin/python -m pytest tests/test_promotion_eval_bucket_replay.py -v
```

Expected: 5 PASS

- [ ] **Step 9: Smoke test the actual report (dry-run, current data)**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && /home/ubuntu/kalshi-arb/venv/bin/python scripts/promotion_eval.py --sport ATP --dry-run --window 1"
```

Expected: Report prints to stdout, shows ATP with `n=0` everywhere (no data yet — observer hasn't run). Or `(no data)` message if early-exit. No crash.

- [ ] **Step 10: Commit**

```bash
git add tests/test_promotion_eval_bucket_replay.py scripts/promotion_eval.py
git commit -m "feat(promotion_eval): 4-archetype bucket replay for individual sports

Extends TARGET_SPORTS to include ATP/WTA/UFC and adds per-sport
longshot/underdog/mid-fav/chalk bucket replay (n, WR, ROI, 95% CI).
Output feeds the T+14 archetype-selection decision for the follow-up
Tennis/MMA strategy spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Extend daily_data_quality with new-sport sections

The existing report iterates per-sport from `odds_observations` rows. Tennis/MMA rows will appear automatically once Task 5 deploys. Need to:
1. Add ATP/WTA/UFC to the section ordering
2. Add a sport-specific "matched %" line (matched markets / total markets observed today) — useful for surfacing player-name normalization gaps

**Files:**
- Modify: `scripts/daily_data_quality.py`
- Test: `tests/test_daily_quality_individual_sports.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_daily_quality_individual_sports.py`:

```python
"""daily_data_quality covers ATP/WTA/UFC — added 2026-05-11."""
import sqlite3
import pytest

from scripts.daily_data_quality import _per_sport_stats, _format_sport_block


@pytest.fixture
def db_with_atp_rows(tmp_path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            kalshi_ticker TEXT,
            series TEXT,
            sport TEXT,
            phase TEXT,
            minutes_to_start REAL,
            home_team TEXT, away_team TEXT, yes_sub_title TEXT,
            is_yes_home INTEGER, is_yes_draw INTEGER,
            kalshi_yes_ask REAL, kalshi_no_ask REAL,
            us_yes_prob REAL, eu_yes_prob REAL, pinnacle_yes_prob REAL,
            pinnacle_age_s INTEGER, is_playoff INTEGER,
            n_books_eu INTEGER, n_books_us INTEGER, has_pinnacle INTEGER,
            kalshi_yes_bid REAL, kalshi_no_bid REAL,
            kalshi_yes_bid_size REAL, kalshi_yes_ask_size REAL,
            kalshi_no_bid_size REAL, kalshi_no_ask_size REAL,
            kalshi_volume_24h REAL, kalshi_open_interest REAL
        )"""
    )
    # 2 ATP rows in last hour
    conn.execute(
        """INSERT INTO odds_observations (timestamp, sport, series, kalshi_ticker,
                    is_yes_home, is_yes_draw, n_books_eu, has_pinnacle,
                    kalshi_open_interest, kalshi_volume_24h)
           VALUES (datetime('now', '-30 minutes'), 'ATP', 'KXATPMATCH',
                   'KXATPMATCH-26MAY12ALCSIN-ALC', 1, 0, 12, 1, 5000, 200),
                  (datetime('now', '-30 minutes'), 'ATP', 'KXATPMATCH',
                   'KXATPMATCH-26MAY12ALCSIN-SIN', 0, 0, 12, 1, 5000, 200)"""
    )
    conn.commit()
    conn.close()
    return str(db_path)


def test_atp_appears_in_per_sport_stats(db_with_atp_rows):
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    assert "ATP" in stats
    assert stats["ATP"]["n"] == 2


def test_format_sport_block_for_atp_does_not_assume_3way(db_with_atp_rows):
    conn = sqlite3.connect(db_with_atp_rows)
    stats = _per_sport_stats(conn, hours=1)
    conn.close()
    block = _format_sport_block("ATP", stats["ATP"], baseline=None)
    # Tennis is 2-way — TIE leg balance section must NOT appear for ATP
    text = "\n".join(block)
    assert "TIE" not in text
    # Pinnacle coverage % MUST appear
    assert "Pinnacle" in text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_daily_quality_individual_sports.py -v
```

Expected: 2 FAIL — `_format_sport_block` likely doesn't exist as importable function, or existing implementation does include TIE block for all sports.

- [ ] **Step 3: Inspect the existing report builder**

```bash
grep -n "def \|format\|TIE" scripts/daily_data_quality.py | head -20
```

Find the function that emits per-sport text. If it's inlined inside `main()`, refactor it into a module-level `_format_sport_block(sport, stats, baseline)` returning a list of strings. Existing 3-way TIE leg detection should be gated on `sport in {"MLS","EPL","LAL","UCL"}` or `stats["tie_legs"] > 0`.

- [ ] **Step 4: Refactor + add individual-sport handling**

Locate the existing report-formatting block in `scripts/daily_data_quality.py` and refactor into:

```python
THREE_WAY_SPORTS = frozenset({"MLS", "EPL", "LAL", "UCL"})


def _format_sport_block(sport: str, stats: dict, baseline: dict | None) -> list[str]:
    """Format one sport's daily quality block. Returns list of lines."""
    lines = [f"<b>{sport}</b>"]
    lines.append(f"  rows (24h): {stats['n']}")
    # n_books p10/p50
    eu = stats.get("books_eu", [])
    if eu:
        lines.append(f"  n_books_eu p10/p50: {_percentile(eu, 0.10)} / {_percentile(eu, 0.50)}")
    pin_pct = (100.0 * stats["pin"] / stats["n"]) if stats["n"] else 0
    lines.append(f"  Pinnacle coverage: {pin_pct:.1f}%")
    # OI / volume
    oi = stats.get("oi", [])
    if oi:
        lines.append(f"  Kalshi OI median: ${_percentile(oi, 0.50):.0f}")
    vol = stats.get("vol24", [])
    if vol:
        lines.append(f"  Kalshi vol_24h median: ${_percentile(vol, 0.50):.0f}")
    # 3-way leg balance only for soccer
    if sport in THREE_WAY_SPORTS:
        h, a, t = stats.get("home_legs", 0), stats.get("away_legs", 0), stats.get("tie_legs", 0)
        if (h + a + t) > 0:
            lines.append(f"  leg balance H/A/T: {h}/{a}/{t}")
    # Baseline degradation
    if baseline is not None:
        baseline_n = baseline.get("n", 0)
        if baseline_n > 0:
            change = (stats["n"] - baseline_n) / baseline_n
            if change < -0.25:
                lines.append(f"  ⚠️  rows down {change*100:.0f}% vs 7d baseline ({baseline_n})")
    return lines
```

Then in `main()`, iterate including new sports. Find the existing loop and ensure `["NBA", "MLB", "NHL", "WNBA", "NPB", "KBO", "MLS", "EPL", "LAL", "UCL", "BUN", "ATP", "WTA", "UFC"]` is the order — or just iterate over `stats.keys()` sorted.

- [ ] **Step 5: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_daily_quality_individual_sports.py -v
```

Expected: 2 PASS

- [ ] **Step 6: Smoke test on prod DB (dry run)**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && /home/ubuntu/kalshi-arb/venv/bin/python scripts/daily_data_quality.py --dry-run"
```

Expected: Report prints; ATP/WTA/UFC sections appear with `n=0` (no rows yet — observer not yet rolled out). No crash.

- [ ] **Step 7: Commit**

```bash
git add tests/test_daily_quality_individual_sports.py scripts/daily_data_quality.py
git commit -m "feat(monitoring): daily_data_quality covers ATP/WTA/UFC

Individual sports get the standard per-sport quality block sans 3-way
leg balance (which only applies to soccer). Refactored inline formatter
into _format_sport_block for testability.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Settlement-backfill integration test

The existing `scripts/backfill_ticker_settlements.py` should handle Tennis/MMA tickers unchanged — they're binary YES/NO outcomes like NBA/MLB. Confirm with an integration test that feeds a real (resolved) ticker through the backfill.

**Files:**
- Test: `tests/test_settlement_backfill_individual_sports.py`

- [ ] **Step 1: Inspect backfill_ticker_settlements**

```bash
grep -n "def \|series\|sport" scripts/backfill_ticker_settlements.py | head -20
```

Look for: (a) is there sport-specific logic? (b) does it filter by series? If sport-agnostic (just iterates over unsettled tickers and calls Kalshi `/markets/{ticker}/settlement`), then no changes needed — only validation.

- [ ] **Step 2: Write the integration test**

Create `tests/test_settlement_backfill_individual_sports.py`:

```python
"""Settlement backfill works for individual-sport (Tennis/MMA) tickers — added 2026-05-11.

Smoke test: feed a synthetic resolved ticker through the backfill flow
and verify the row inserts into ticker_settlements correctly.
"""
import sqlite3
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def db_with_unsettled_atp(tmp_path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE odds_observations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            kalshi_ticker TEXT NOT NULL,
            series TEXT, sport TEXT, phase TEXT,
            minutes_to_start REAL,
            home_team TEXT, away_team TEXT, yes_sub_title TEXT,
            is_yes_home INTEGER, is_yes_draw INTEGER,
            kalshi_yes_ask REAL, kalshi_no_ask REAL,
            us_yes_prob REAL, eu_yes_prob REAL, pinnacle_yes_prob REAL,
            pinnacle_age_s INTEGER, is_playoff INTEGER,
            n_books_eu INTEGER, n_books_us INTEGER, has_pinnacle INTEGER,
            kalshi_yes_bid REAL, kalshi_no_bid REAL,
            kalshi_yes_bid_size REAL, kalshi_yes_ask_size REAL,
            kalshi_no_bid_size REAL, kalshi_no_ask_size REAL,
            kalshi_volume_24h REAL, kalshi_open_interest REAL
        );
        CREATE TABLE ticker_settlements (
            ticker TEXT PRIMARY KEY,
            settled_at TEXT,
            outcome TEXT,    -- 'yes' | 'no' | 'cancelled'
            close_yes_price REAL
        );
        INSERT INTO odds_observations
            (timestamp, kalshi_ticker, series, sport, phase, minutes_to_start)
        VALUES
            (datetime('now', '-2 days'), 'KXATPMATCH-26MAY10ALCSIN-ALC',
             'KXATPMATCH', 'ATP', 'pre', 120);
    """)
    conn.commit()
    conn.close()
    return str(db_path)


def test_backfill_inserts_settlement_for_atp_ticker(db_with_unsettled_atp):
    """Mock the Kalshi API response and verify the backfill function inserts
    a settlement row for an ATP ticker. (Demonstrates schema accepts the
    individual-sport ticker — no schema migration needed.)"""
    from scripts.backfill_ticker_settlements import backfill_one

    mock_client = MagicMock()
    mock_client.get_market_settlement = MagicMock(return_value={
        "ticker": "KXATPMATCH-26MAY10ALCSIN-ALC",
        "result": "yes",
        "settled_at": "2026-05-10T18:30:00Z",
        "close_yes_price": 1.0,
    })

    conn = sqlite3.connect(db_with_unsettled_atp)
    inserted = backfill_one(conn, mock_client, "KXATPMATCH-26MAY10ALCSIN-ALC")
    conn.commit()
    cur = conn.execute("SELECT ticker, outcome FROM ticker_settlements WHERE ticker = ?",
                       ("KXATPMATCH-26MAY10ALCSIN-ALC",))
    row = cur.fetchone()
    conn.close()
    assert inserted is True
    assert row is not None
    assert row[0] == "KXATPMATCH-26MAY10ALCSIN-ALC"
    assert row[1] == "yes"
```

If `backfill_ticker_settlements.py` doesn't expose a `backfill_one(conn, client, ticker)` function, refactor the existing loop to extract one. The refactor is minimal — split the inner-loop body into a function with the same signature.

- [ ] **Step 3: Run test**

```bash
venv/bin/python -m pytest tests/test_settlement_backfill_individual_sports.py -v
```

Expected: PASS (after any minor refactor of backfill_one).

- [ ] **Step 4: Commit**

```bash
git add tests/test_settlement_backfill_individual_sports.py scripts/backfill_ticker_settlements.py
git commit -m "test: confirm settlement backfill accepts Tennis/MMA tickers

Integration test verifies the existing backfill pipeline writes correctly
for KXATPMATCH-* tickers (and by extension KXWTAMATCH/KXUFCFIGHT — same
schema). No schema migration needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Tennis endpoint rotation cron

The Tennis `SPORT_MAP` entry rotates per active tournament. Daily cron checks whether the currently-mapped endpoint returns events; if 0 events for 2 consecutive days, Telegram-alert with suggested next endpoint.

**Files:**
- Create: `scripts/tennis_endpoint_rotation.py`
- Create: `systemd/kalshi-tennis-rotation.service`
- Create: `systemd/kalshi-tennis-rotation.timer`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tennis_endpoint_rotation.py`:

```python
"""Tennis endpoint rotation cron — added 2026-05-11."""
from unittest.mock import MagicMock, patch
import pytest

from scripts.tennis_endpoint_rotation import (
    fetch_active_endpoints,
    detect_stale_endpoints,
)


def test_detect_stale_endpoints_zero_events_two_days_triggers():
    """If a mapped endpoint has zero events for ≥ 2 consecutive days,
    detect_stale_endpoints lists it. Counter persists in a state file."""
    state = {"tennis_atp_italian_open": 2}  # 2 consecutive zero-event days
    sport_map = {"KXATPMATCH": "tennis_atp_italian_open"}
    current_counts = {"tennis_atp_italian_open": 0}  # zero again today
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" in stale
    # State for this endpoint is reset upon detection (avoids re-alert spam)
    assert new_state.get("tennis_atp_italian_open", 0) == 0


def test_detect_stale_endpoints_first_zero_day_does_not_trigger():
    state = {}
    sport_map = {"KXATPMATCH": "tennis_atp_italian_open"}
    current_counts = {"tennis_atp_italian_open": 0}
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" not in stale
    assert new_state["tennis_atp_italian_open"] == 1


def test_detect_stale_endpoints_nonzero_resets_counter():
    state = {"tennis_atp_italian_open": 1}
    sport_map = {"KXATPMATCH": "tennis_atp_italian_open"}
    current_counts = {"tennis_atp_italian_open": 15}
    new_state, stale = detect_stale_endpoints(state, current_counts, sport_map, threshold=2)
    assert "KXATPMATCH" not in stale
    assert new_state.get("tennis_atp_italian_open", 0) == 0


def test_fetch_active_endpoints_calls_odds_api():
    """fetch_active_endpoints hits /v4/sports?all=true and returns
    list of {key: ..., active: ..., title: ...}."""
    with patch("scripts.tennis_endpoint_rotation.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"key": "tennis_atp_french_open", "active": True, "title": "ATP French Open"},
                {"key": "tennis_atp_italian_open", "active": False, "title": "ATP Italian Open"},
            ],
        )
        result = fetch_active_endpoints("dummy_api_key")
        assert any(e["key"] == "tennis_atp_french_open" and e["active"] for e in result)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_tennis_endpoint_rotation.py -v
```

Expected: All FAIL — module doesn't exist.

- [ ] **Step 3: Implement the rotation script**

Create `scripts/tennis_endpoint_rotation.py`:

```python
"""Daily check: do any mapped tennis endpoints return 0 events for 2+
consecutive days? If so, Telegram-alert with suggestion of currently-active
tennis endpoints (so we know what to rotate to).

State is persisted in data/tennis_rotation_state.json (one int counter per
endpoint slug, incrementing on zero-event days and resetting on non-zero).

Usage:
    venv/bin/python scripts/tennis_endpoint_rotation.py            # send Telegram
    venv/bin/python scripts/tennis_endpoint_rotation.py --dry-run  # stdout only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from strategies.sports_scan_observer import SPORT_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tennis_endpoint_rotation")

STATE_PATH = Path("data/tennis_rotation_state.json")
TENNIS_SERIES = frozenset({"KXATPMATCH", "KXWTAMATCH"})


def fetch_active_endpoints(api_key: str) -> list[dict]:
    """List every Odds API sport endpoint (active and inactive)."""
    r = httpx.get(
        "https://api.the-odds-api.com/v4/sports",
        params={"apiKey": api_key, "all": "true"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _events_count_for_endpoint(api_key: str, slug: str) -> int:
    """Number of upcoming events on the slug (proxy for whether the
    tournament is currently running)."""
    try:
        r = httpx.get(
            f"https://api.the-odds-api.com/v4/sports/{slug}/events",
            params={"apiKey": api_key},
            timeout=20,
        )
        if r.status_code != 200:
            return 0
        return len(r.json() or [])
    except Exception as e:
        logger.warning(f"events_count {slug}: {e}")
        return 0


def detect_stale_endpoints(
    state: dict[str, int],
    current_counts: dict[str, int],
    sport_map: dict[str, str],
    threshold: int = 2,
) -> tuple[dict[str, int], list[str]]:
    """Update consecutive-zero counters and return list of Kalshi series
    whose endpoint has been zero for >= threshold days."""
    new_state = dict(state)
    stale_series: list[str] = []
    for series, slug in sport_map.items():
        if series not in TENNIS_SERIES:
            continue
        count_today = current_counts.get(slug, 0)
        if count_today == 0:
            new_state[slug] = new_state.get(slug, 0) + 1
            if new_state[slug] >= threshold:
                stale_series.append(series)
                # Reset on alert to avoid repeated daily nag for the same stale endpoint
                new_state[slug] = 0
        else:
            new_state[slug] = 0
    return new_state, stale_series


def _load_state() -> dict[str, int]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, int]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


async def _send_telegram(text: str):
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        print(text)
        return
    from monitoring.notifier import TelegramNotifier
    n = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await n.send(text, msg_type="tennis_rotation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    api_key = cfg.ODDS_API_KEY
    if not api_key:
        logger.error("ODDS_API_KEY missing")
        sys.exit(1)

    # Today's events per mapped tennis slug
    current_counts: dict[str, int] = {}
    for series, slug in SPORT_MAP.items():
        if series in TENNIS_SERIES:
            current_counts[slug] = _events_count_for_endpoint(api_key, slug)
            logger.info(f"{series} -> {slug}: {current_counts[slug]} events")

    state = _load_state()
    new_state, stale = detect_stale_endpoints(state, current_counts, SPORT_MAP)
    _save_state(new_state)

    if not stale:
        logger.info("No stale tennis endpoints.")
        return

    # Fetch active alternatives for the alert
    active = fetch_active_endpoints(api_key)
    active_tennis = [e for e in active
                     if e.get("key", "").startswith("tennis_") and e.get("active")]
    suggestions = "\n".join(
        f"  • {e['key']}  ({e.get('title','')})" for e in active_tennis[:10]
    ) or "  (none active right now)"

    lines = [
        "🎾 <b>Tennis endpoint rotation needed</b>",
        "",
        "Stale (0 events for 2+ days):",
    ]
    for series in stale:
        lines.append(f"  • {series} → {SPORT_MAP[series]}")
    lines += ["", "Currently active tennis endpoints:", suggestions]
    lines += ["", "<i>Action: edit strategies/sports_scan_observer.py SPORT_MAP, "
              "deploy, restart kalshi-bot.</i>"]
    text = "\n".join(lines)

    if args.dry_run:
        print(text)
    else:
        asyncio.run(_send_telegram(text))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/python -m pytest tests/test_tennis_endpoint_rotation.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Create systemd unit files**

Create `systemd/kalshi-tennis-rotation.service`:

```ini
[Unit]
Description=Kalshi-arb: tennis endpoint rotation check
After=kalshi-bot.service

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/kalshi-arb
ExecStart=/home/ubuntu/kalshi-arb/venv/bin/python scripts/tennis_endpoint_rotation.py
StandardOutput=journal
StandardError=journal
```

Create `systemd/kalshi-tennis-rotation.timer`:

```ini
[Unit]
Description=Kalshi-arb: tennis endpoint rotation daily check

[Timer]
OnCalendar=daily
OnCalendar=*-*-* 11:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_tennis_endpoint_rotation.py scripts/tennis_endpoint_rotation.py \
        systemd/kalshi-tennis-rotation.service systemd/kalshi-tennis-rotation.timer
git commit -m "feat(monitoring): tennis endpoint rotation alert cron

Daily check: if any mapped tennis endpoint returns 0 events for 2+ days,
Telegram-alert with currently-active alternatives. State persisted in
data/tennis_rotation_state.json (counter per slug). Systemd timer fires
daily at 11:00 UTC.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Deploy + smoke test on EC2

Push all commits, pull on EC2, restart `kalshi-bot`, install new systemd unit, verify observer is logging Tennis/MMA rows.

**Files:** No code change — operational only.

- [ ] **Step 1: Push to remote**

```bash
git push origin master
```

- [ ] **Step 2: Pull on EC2 and restart**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot && sudo systemctl is-active kalshi-bot"
```

Expected: `active`

- [ ] **Step 3: Install + enable rotation timer**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo cp /home/ubuntu/kalshi-arb/systemd/kalshi-tennis-rotation.service /etc/systemd/system/ && \
   sudo cp /home/ubuntu/kalshi-arb/systemd/kalshi-tennis-rotation.timer /etc/systemd/system/ && \
   sudo systemctl daemon-reload && \
   sudo systemctl enable --now kalshi-tennis-rotation.timer && \
   sudo systemctl list-timers kalshi-tennis-rotation.timer"
```

Expected: Timer line shows next scheduled run.

- [ ] **Step 4: Verify observer logs Tennis/MMA scans**

Wait ~2 minutes for the next sports scan cycle, then:

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '5 minutes ago' --no-pager | grep -E 'KXATPMATCH|KXWTAMATCH|KXUFCFIGHT' | head -10"
```

Expected: Lines containing the new series, indicating scan attempts. If 0 lines after 5 minutes, check for errors:

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo journalctl -u kalshi-bot --since '5 minutes ago' --no-pager | grep -iE 'error|exception|traceback' | head -10"
```

- [ ] **Step 5: Verify odds_observations rows accumulate**

After 10 minutes:

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "/home/ubuntu/kalshi-arb/venv/bin/python -c \"
import sqlite3
c = sqlite3.connect('/home/ubuntu/kalshi-arb/data/bot.db', timeout=30)
for s in ['ATP','WTA','UFC']:
    n = c.execute('SELECT COUNT(*) FROM odds_observations WHERE sport=? AND datetime(timestamp) >= datetime(\\\"now\\\", \\\"-1 hour\\\")', (s,)).fetchone()[0]
    print(f'{s}: {n} rows in last hour')
\""
```

Expected: ATP, WTA, UFC each have ≥ 0 rows. If 0 across all three for 30 minutes, the SPORT_MAP slug is wrong (Task 0 verification failed) or the Odds API returned no matches (e.g., no events today for tennis Italian Open if it just ended).

- [ ] **Step 6: Smoke test promotion_eval against current data**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "cd /home/ubuntu/kalshi-arb && /home/ubuntu/kalshi-arb/venv/bin/python scripts/promotion_eval.py --sport ATP --window 1 --dry-run"
```

Expected: Report prints; ATP archetype bucket section appears with `n=0` for all buckets (no settled markets yet). No crash.

- [ ] **Step 7: Manually trigger one rotation cron run**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo systemctl start kalshi-tennis-rotation.service && sudo journalctl -u kalshi-tennis-rotation.service --since '2 minutes ago' --no-pager | tail -15"
```

Expected: Service runs once, exits clean (rc=0), logs `No stale tennis endpoints` (assumes Italian Open or French Open is active).

- [ ] **Step 8: Update memory note for T+14 review**

Create `/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/tennis_mma_observer_2026-05-11.md`:

```markdown
---
name: tennis-mma-observer-2026-05-11
description: Tennis (KXATPMATCH/KXWTAMATCH) + MMA (KXUFCFIGHT) observer deployed; T+14 archetype decision pending
metadata:
  type: project
---
Deployed 2026-05-11 (plan: docs/superpowers/plans/2026-05-11-tennis-mma-observer-plan.md).

**Status:** observer-only — no strategy file yet.

**Wiring:**
- SPORTS_SERIES + SPORT_GAME_TOKENS + SPORT_SHORT_BY_TOKEN: ATP/WTA/UFC entries
- OBSERVATION_ONLY_SPORTS: {"ATP","WTA","UFC"} added
- SPORT_MAP: KXATPMATCH→tennis_atp_*, KXWTAMATCH→tennis_wta_*, KXUFCFIGHT→mma_*
- New matcher: _match_individual_sport_to_odds (yes_sub_title direct match + token overlap)
- promotion_eval: 4-archetype bucket replay (longshot 5-25¢ / underdog 25-39¢ / mid-fav 39-75¢ / chalk 75+) per sport
- Tennis rotation cron: alerts when current endpoint has 0 events for 2 days

**T+14 review (2026-05-25):**
- Run promotion_eval (or wait for Monday cron) → archetype bucket replay output
- Per spec success criteria:
  - n_games ≥ 25 per sport
  - match rate ≥ 70%
  - Pinnacle coverage ≥ 70%
  - Top bucket ROI ≥ +3% with CI lower bound ≥ -5%
- If passes → write follow-up spec for strategy archetype
- If fails → diagnose / extend window / abandon

**Why:** Build alpha discovery infrastructure before parameter setting. v34 ALLOWED_CELLS pattern of pre-committing parameters failed (MLB-live -23.8% from premature inclusion). This time, parameters come from data.

Related: [[v37_soccer_strategy]] (pattern model), [[sport_promotion_thresholds]] (eval criteria), [[npb_kbo_soccer_observers]] (observer precedent)
```

Add a single-line index entry to `/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/MEMORY.md`:

```markdown
- [Tennis + MMA observer 2026-05-11](tennis_mma_observer_2026-05-11.md) — KXATPMATCH/KXWTAMATCH/KXUFCFIGHT observer deployed; T+14 (5/25) promotion_eval bucket replay decides archetype
```

- [ ] **Step 9: Final verification**

```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.117.216.224 \
  "sudo systemctl is-active kalshi-bot kalshi-tennis-rotation.timer"
```

Expected: Both `active`.

- [ ] **Step 10: Commit memory updates**

The memory dir is outside the repo. The plan's last task is operational only — no repo commit. If the user wants the memory dir checked in, that's a separate workflow.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Sport tokens + shorts (Task 1)
- ✅ OBSERVATION_ONLY_SPORTS registration (Task 2)
- ✅ SPORT_MAP entries (Task 3)
- ✅ Player-name matching (Task 4, Task 5 dispatch)
- ✅ promotion_eval 4-bucket (Task 6)
- ✅ daily_data_quality extension (Task 7)
- ✅ Settlement integration test (Task 8)
- ✅ Endpoint-rotation cron (Task 9)
- ✅ Deploy + smoke (Task 10)

**Placeholder scan:**
- Task 3 SPORT_MAP slugs are placeholders pending Task 0 verification — flagged explicitly with "replace with Task 0 verified slug" comment. Acceptable.
- Task 8 references `backfill_one` — the test description includes "If `backfill_ticker_settlements.py` doesn't expose a `backfill_one` function, refactor the existing loop to extract one." This is a directed instruction, not a vague placeholder.

**Type consistency:**
- `_match_individual_sport_to_odds(yes_sub_title, odds_games, kalshi_ticker=None)` — same signature used in tests and observer dispatch.
- `_matcher_for_series(series) → Optional[Callable]` — consistent return type (function or None).
- `ARCHETYPE_BUCKETS` tuple format `(lo, hi, label)` — consistent across helper and report section.
