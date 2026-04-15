# Closing Pinnacle Capture — Future Work

**Status**: deferred 2026-04-15. Task 6 of V3/V31 hypothesis test plan marked complete without implementation.

## Why deferred

Two independent blockers:

1. **Missing ticker→game mapping at settlement**: Trades table doesn't
   persist market title or Odds API `game_id`. Settlement code
   (core/settlement.py) only has `ticker`. The existing
   `_match_kalshi_to_odds` helper in `strategies/sports_trader.py`
   needs the full market title, which isn't in the DB.

2. **Odds API drops resolved games**: `_fetch_odds` calls
   `https://api.the-odds-api.com/v4/sports/<sport>/odds` which only
   serves active markets. Since settlement runs after game resolution,
   Pinnacle prob would almost always be None.

## Correct future approach

When closing-time Pinnacle is wanted, do NOT re-query at settlement.
Instead, **carry Pinnacle forward from entry**:

1. At entry, snapshot `pinnacle_prob` is already captured (and Task 5
   now also captures us/eu consensus). Add `odds_game_id` to the entry
   snapshot.
2. Schedule a periodic job that, until game start, re-queries Odds API
   for each live trade's `odds_game_id` and updates a `pre_close` (or
   `last_live`) snapshot row with fresher Pinnacle data.
3. At settlement, the most recent pre-settlement Pinnacle snapshot IS
   the closing Pinnacle estimate. No re-query needed at settlement.

Estimated size: ~10 lines in `scan_and_execute` for odds_game_id
persistence + ~30 lines for the periodic pre-close job. Better done as
a standalone spec when prioritized.

## Spec reference

Original spec: `docs/superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md`
Plan: `docs/superpowers/plans/2026-04-15-v3-v31-hypothesis-test.md` (Task 6)
