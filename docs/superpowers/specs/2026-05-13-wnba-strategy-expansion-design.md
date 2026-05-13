# WNBA Strategy Expansion — Design

**Status**: Approved 2026-05-13
**Author**: collaborative (user + Claude)
**Target deploy**: 2026-05-13 (this session)
**First eval**: 2026-05-23 (n≥25 promotion-eval per [sport_promotion_thresholds.md](../../memory/sport_promotion_thresholds.md))

## Context

WNBA wired observer-only on 2026-05-09 (commit 6698c5e). 4-day observer accumulation:
- 20,771 obs rows / 22 tickers / 11 unique games / 8 fully settled
- n_books_eu 11.5-12.8 (≥8 promotion gate, ✓)
- top-of-book depth p50 $567 (≥$20 gate, ✓)
- Per-strategy paper-replay (n=10-11):

| Strategy | games | WR | ROI | CI 95% |
|----------|------:|---:|----:|--------|
| longshot_v31 | 1 | 100% | +335% | — |
| midshot_v31 | 4 | 50% | +84% | [-100%, +268%] |
| underdog_v31 | 7 | 43% | +41% | [-64%, +173%] |
| v36_floor35 (replay) | 3 | 100% | +73% | [+72%, +73%] |
| v37_band_20_29 (replay) | 2 | 50% | +118% | [-100%, +335%] |

Sample size below n=25 promotion gate, but user prioritizes fast data accumulation over conservative paper-twin launch (see Decisions).

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Launch mode | Full live, immediate | Fast n accumulation. Accepts noise risk for 1-2 weeks until eval. |
| v31 trio enablement | modify-in-place | longshot/midshot/underdog_v31 are sport-agnostic by design; natural fit. |
| v36 enablement | sibling class `v36_wnba` | v36's `PLAYOFF_ONLY=True` blocks WNBA regular season; sibling avoids gate-stripping the NBA-playoff parent. |
| v37 enablement | sibling class `v37_wnba` | v37_soccer's "soccer-only" intent preserved; thin sibling inherits 20-29c TEAM band logic. |
| Sizing | Inherit existing per strategy | longshot 12 / midshot 16 / underdog 20 / v36_wnba 8 / v37_wnba 8. |
| Pair-fire dedup | WNBA-only, applied to all 5 strategies | Live v40 pattern. Avoids 2× leverage on same outcome via paired tickers. NBA/MLB/NHL pair-firing unchanged (P2 cleanup deferred per [pair_fire_dedup_queued_2026-05-11.md](../../memory/pair_fire_dedup_queued_2026-05-11.md)). |

## Architecture

### v31 trio — modify-in-place

For each of `sports_longshot_v31.py`, `sports_midshot_v31.py`, `sports_underdog_v31.py`:

1. Override OBSERVATION_ONLY_SPORTS to unblock WNBA:
   ```python
   OBSERVATION_ONLY_SPORTS = (
       SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
   )
   ```
2. Add WNBA-only pair-fire dedup at top of `evaluate()`:
   ```python
   if "WNBAGAME" in ticker:
       game_key = ticker.rsplit("-", 1)[0]
       for traded in self._traded_tickers:
           if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
               return None
   ```

### v36_wnba — new sibling

File: `strategies/sports_underdog_v36_wnba.py`

```python
class SportsUnderdogV36WNBAStrategy(SportsUnderdogV31Strategy):
    STRATEGY_NAME = "sports_underdog_v36_wnba"
    TRADED_TICKERS_FILE = "data/sports_underdog_v36_wnba_tickers.json"
    REGION_OVERRIDE = "eu"  # observer used eu

    ALLOWED_CELLS = frozenset({("WNBA", "pre")})
    PLAYOFF_ONLY = False
    SPORT_BLOCKLIST = frozenset()
    PLAYOFF_BLOCKLIST = frozenset()

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )

    # v36 floor35 band — 35-55c YES with fair_prob > 0.55
    # (matches replay basis: n=3 WNBA games, ROI +73%)
    MIN_ENTRY_PRICE = 0.35
    MAX_ENTRY_PRICE = 0.55
    MIN_DEPTH_USDC = 20.0
    PRE_GAME_ONLY = True

    def _get_trade_size(self, signal):
        return min(8.0, self._order_manager.bankroll * 0.02)

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **_):
        # Pair-fire dedup (WNBA-only by ALLOWED_CELLS, so unconditional here)
        game_key = ticker.rsplit("-", 1)[0]
        for traded in self._traded_tickers:
            if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                return None
        # v36 floor35 signal: side_prob > 0.55, price in 30-55c, payoff >= 1.5x
        # ... (inherit core logic from v36_us pattern)
```

### v37_wnba — new sibling

File: `strategies/sports_underdog_v37_wnba.py`

```python
class SportsUnderdogV37WNBAStrategy(SportsUnderdogV37SoccerStrategy):
    STRATEGY_NAME = "sports_underdog_v37_wnba"
    TRADED_TICKERS_FILE = "data/sports_underdog_v37_wnba_tickers.json"

    # Inherit all gates from v37_soccer EXCEPT block list
    SPORT_BLOCKLIST = frozenset({"NBA", "MLB", "NHL", "NPB", "KBO"})  # WNBA NOT in here

    # Inherit 0.20-0.29 TEAM band, payoff>=2.0, PRE_GAME_ONLY, MAX_MTS=1440,
    # MIN_DEPTH=15, size 8 from v37_soccer.

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob, ticker="", **kwargs):
        # Pair-fire dedup (only WNBA fires given SPORT_BLOCKLIST, so unconditional)
        game_key = ticker.rsplit("-", 1)[0]
        for traded in self._traded_tickers:
            if traded != ticker and traded.rsplit("-", 1)[0] == game_key:
                return None
        # Delegate to v37_soccer evaluate (TEAM-only 20-29c, payoff>=2.0)
        return super().evaluate(kalshi_yes, kalshi_no, fair_prob, ticker=ticker, **kwargs)
```

### Cascade re-block for v32 and v33 paper variants

v32 and v33 paper variants inherit from `SportsUnderdogV31Strategy`. v31 underdog's OBSERVATION_ONLY override cascades to them — undesired since they're out of scope.

For each (v32, v33 paper variants):
```python
# Restore parent default (re-block WNBA)
OBSERVATION_ONLY_SPORTS = frozenset(SportsTraderStrategy.OBSERVATION_ONLY_SPORTS)
```

v34/v35 trio: naturally blocked via ALLOWED_CELLS (NBA/MLB/NHL cells only, no WNBA cell). No action.

### Registration

1. `core/order_manager.py` — `PAPER_TWIN_STRATEGIES`: add `sports_underdog_v36_wnba`, `sports_underdog_v37_wnba`.
2. `main.py` — import + instantiate + add to scan_loop iteration (~12L).
3. `monitoring/dashboard/api.py` — add to categorize() + display list (~6L).
4. **`.env` (prod)** — `LIVE_STRATEGIES` add 2 new tokens: `sports_underdog_v36_wnba`, `sports_underdog_v37_wnba`. (v31 trio already in env.)

## File Change Summary

| File | Type | Est. LOC |
|------|------|---------:|
| `strategies/sports_longshot_v31.py` | edit | +6 |
| `strategies/sports_midshot_v31.py` | edit | +6 |
| `strategies/sports_underdog_v31.py` | edit | +6 |
| `strategies/sports_underdog_v32.py` | edit | +2 |
| `strategies/sports_underdog_v33_*.py` (paper) | edit | +2 each |
| `strategies/sports_underdog_v36_wnba.py` | NEW | ~70 |
| `strategies/sports_underdog_v37_wnba.py` | NEW | ~30 |
| `core/order_manager.py` | edit | +2 |
| `main.py` | edit | ~12 |
| `monitoring/dashboard/api.py` | edit | ~6 |
| `.env` (prod) | edit | 2 tokens |

Total: ~10 files modified, 2 files new, ~150 LOC.

## Validation Plan

### 1. Unit tests (pytest)

New file `tests/test_wnba_expansion.py`:
- v31 trio fires on a WNBA pre-game ticker (mock obs in band)
- v32 / v33 paper does NOT fire on WNBA ticker (cascade re-block verified)
- v37_soccer does NOT fire on WNBA ticker (sibling separation verified)
- v34/v35 does NOT fire on WNBA (ALLOWED_CELLS natural block)
- Pair-fire dedup: 2nd WNBA fire on same game_key returns None when sibling ticker already in `_traded_tickers`
- v36_wnba fires on WNBA-pre 30-55c TEAM with fair_prob > 0.55
- v37_wnba fires on WNBA 20-29c TEAM with payoff >= 2.0

### 2. Observer replay sanity

Extend `scripts/strategy_replay.py` with `v36_wnba` + `v37_wnba` callables. Re-run on existing 11-game WNBA observer set:
- Expected fires (post-dedup): longshot 1, midshot 4, underdog 7, v36_wnba ~3, v37_wnba 2
- Verify totals match yesterday's per-strategy analysis (within rounding)

### 3. Local dry-run

Run main.py with `LIVE_STRATEGIES=""` and observe scan-loop logs for fire patterns on WNBA tickers (without placing orders).

## Rollout

1. Local: implement, run pytest (full suite ≤ master baseline failures).
2. Commit each logical change separately (3 commits: v31 trio mods, v36_wnba/v37_wnba siblings, registration glue).
3. Push origin master.
4. SSH prod: `git pull`.
5. Edit `.env` on prod to add 2 new LIVE_STRATEGIES tokens.
6. `sudo systemctl restart kalshi-bot && sudo systemctl is-active kalshi-bot`.
7. Tail `journalctl -u kalshi-bot -f` for ~5 min to confirm:
   - "Targeted scan: ... strategy markets from N series" — N unchanged at 16
   - First WNBA fire (next slate: 5/14 WNBA games)
   - No exception traces from new strategies

## Success Criteria

### 2026-05-23 promotion eval (n≥25 settled games)

Per-strategy decision matrix:
| Outcome | Action |
|---------|--------|
| ROI ≥ +5% AND CI 95% lower bound > -10% | Keep live, consider size-up |
| ROI -5% to +5% AND CI tight | Run another 2 weeks |
| ROI < -5% OR drop-top-3 < -20% | Retire / paper-only |
| Drop-top-1 collapses ROI by >50pp | Single-game alpha, retire |

### Cross-strategy correlation check

If 3+ strategies show >+50% ROI on same set of games (high inter-strategy correlation), suggests slate-level luck not strategy-level alpha. Apply skepticism to all 5 simultaneously.

## Risks + Mitigations

| Risk | Mitigation |
|------|-----------|
| n=11 single-game noise | Accept; 5/23 eval will have n≥25 minimum. Don't make promotion/retire decisions before then. |
| v36 logic untested on WNBA reg season | Sibling class isolates risk. PnL contribution limited by size 8. |
| Pair-fire dedup in WNBA only (NBA/MLB still 2x) | Documented inconsistency; matches user's choice to apply best-practice forward only. Universal fix remains P2 backlog. |
| Cascade to v32/v33 missed | Re-block explicit in spec; unit test enforces. |
| `_traded_tickers` doesn't have sibling yet at scan time (TTL race) | v40 uses same pattern in prod 24h+ successfully. Sibling fire likely separated by ≥1 scan cycle (60s+). |

## Memory Updates Required (post-deploy)

- New memory: `wnba_5strat_expansion_2026-05-13.md` — captures launch state, sizing, expected n at eval.
- Update [strategy_priority.md](../../memory/strategy_priority.md) — bump from 10 live to 12 (+v36_wnba +v37_wnba).
- Update [wnba_deferred.md](../../memory/wnba_deferred.md) — change from "observer-only" to "5 strategies live".
- Update [v36_playoff_strategy.md](../../memory/v36_playoff_strategy.md) — note v36_wnba sibling exists.
- Update [v37_soccer_strategy.md](../../memory/v37_soccer_strategy.md) — note v37_wnba sibling exists.

## Open Questions Resolved

None — design is complete.

## Open Items for Implementation Plan

- Exact location of pair-fire dedup helper (per-class duplicate vs base class method)
- v36_wnba evaluate() needs floor35 logic ported from where? (memory says "v36" = v36_us/v36_playoff with confidence_min = 0.55) — verify exact thresholds against v36_us source
- v33 paper variants: enumerate which paper-twin files exist (v33_us / v33_eu / etc.) — need full list for re-block patches
- Existing strategy_replay.py needs v36_wnba and v37_wnba helpers added for dry-run replay
