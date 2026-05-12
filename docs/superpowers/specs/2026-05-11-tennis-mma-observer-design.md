# Tennis + MMA observer expansion — Design

**Date**: 2026-05-11
**Status**: Spec
**Owner**: taejun + Claude
**Companion plan**: TBD (writing-plans next)

## Goal

Wire observer-only data collection for two new individual sports — ATP/WTA Tennis and UFC MMA — using the established v37 soccer pattern. **No live strategy** in this spec; the observer + bucket replay output will produce the data needed to choose a strategy archetype (longshot / underdog / fav / cell-matrix) in a follow-up spec at T+14.

NCAAB was originally in the user's request but is off-season until 2026-11-03 ([`scripts/season_reminders.py:31-35`](../../../scripts/season_reminders.py#L31-L35)). Deferred to that opening — the T-7 reminder cron will re-surface it.

## Scope decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Sports | Tennis (ATP+WTA), MMA (UFC) | Year-round + active markets verified; NCAAB blocked by season |
| Tennis universe | Grand Slams + ATP Masters 1000 + WTA 1000 | Mid-scope: enough volume, Kalshi listing reliable, Pinnacle deep |
| MMA universe | UFC full card (main + prelim) | Maximize sample; cell-split (main vs prelim) deferred to data |
| Spec scope | **Observer only** (no strategy file) | Avoid v34-style premature parameter setting (`MIN_DEPTH` / entry band / dead zone all need data) |
| Archetype decision | Deferred to T+14 | Replay across 4 buckets (5-25, 25-39, 39-75, 75+) selects archetype |

## Step 0 — Kalshi listing verification (DONE)

Live probe of Kalshi `/markets?status=open&series_ticker=...` (executed 2026-05-11 23:30 UTC, results recorded below):

| Series | Open markets | Format | Example |
|---|---|---|---|
| `KXATPMATCH` | 16 (Italian Open R16) | `KXATPMATCH-{YYMMMDD}{L3}{L3}-{WIN_L3}` | `KXATPMATCH-26MAY12TIRMED-TIR` (Tirante vs Medvedev, Tirante yes) |
| `KXWTAMATCH` | 4 (Italian Open QF) | same | `KXWTAMATCH-26MAY12GAUAND-GAU` (Gauff vs Andreeva) |
| `KXUFCFIGHT` | 20 (May 16 card) | `KXUFCFIGHT-{YYMMMDD}{L3}{L3}-{WIN_L3}` | `KXUFCFIGHT-26MAY16WELDAL-WEL` (Wells vs Dalby) |

Confirmed out of scope:
- `KXATP` / `KXWTA`: tournament-winner (futures) markets — different framework, deferred
- `KXBELLATOR`, `KXPFL`, `KXMMA`: 0 markets — not listed by Kalshi

Observed details that inform implementation:
- `yes_sub_title` is the **full player/fighter name** (e.g., "Thiago Agustin Tirante"). Direct match to Pinnacle should work without the ~280-name normalization layer originally planned.
- All probed markets show `yes_ask = None` / `no_ask = None`. Orderbooks fill in the hours leading up to the event — observer should still log Pinnacle data, with ask = NULL captured.
- Round info is parseable from `title`: "Round Of 16", "Quarterfinal", "Semifinal", "Final" (Tennis); "professional MMA fight" (MMA — no round structure, single-fight).

## Architecture

Mirrors the v37 soccer observer pattern. No new tables; no new abstract classes (deferred until a 3rd individual sport joins).

### Tokens & sport shorts

[`strategies/sports_trader.py:149-166`](../../../strategies/sports_trader.py#L149-L166):

```python
SPORT_GAME_TOKENS = (
    "WNBAGAME",          # MUST come before NBAGAME
    "NBAGAME", "MLBGAME", "NHLGAME",
    "NPBGAME", "KBOGAME",
    "MLSGAME", "EPLGAME", "LALIGAGAME", "UCLGAME", "BUNDESLIGAGAME",
    "ATPMATCH",          # NEW — ATP tennis match h2h
    "WTAMATCH",          # NEW — WTA tennis match h2h
    "UFCFIGHT",          # NEW — UFC fight h2h
)

SPORT_SHORT_BY_TOKEN = {
    # ... existing
    "ATPMATCH":  "ATP",
    "WTAMATCH":  "WTA",
    "UFCFIGHT":  "UFC",
}
```

Substring collision check: `ATPMATCH` includes "ATP" but neither `ATP` nor `WTA` are existing tokens, so no ordering risk. `UFCFIGHT` likewise distinct.

### Observation-only registration

[`strategies/sports_trader.py:381`](../../../strategies/sports_trader.py#L381) — add 3 entries:

```python
OBSERVATION_ONLY_SPORTS: frozenset = frozenset({
    # ... existing
    "ATP", "WTA", "UFC",
})
```

This routes scanned markets into `odds_observations` only; no strategy fires.

### Odds API endpoint mapping

[`strategies/sports_scan_observer.py:85`](../../../strategies/sports_scan_observer.py#L85) `SPORT_MAP` extension:

**Tennis** (per-tournament endpoints):
```python
SPORT_MAP = {
    # ... existing
    "KXATPMATCH": "tennis_atp_italian_open",      # active 5/11
    "KXWTAMATCH": "tennis_wta_italian_open",      # active 5/11
    # French Open starts 5/26 — endpoints added then via planned weekly maintenance
}
```

This is **NOT scalable to multiple tournaments under one series**. Tennis cycles through 4 GS + 9 Masters + 10 WTA1000 ≈ 23 endpoints/year, only 1-2 active at a time. Maintenance approach:

**Option chosen**: Single `SPORT_MAP` slot per series pointing to the currently-active tournament. Manually update every ~2 weeks as tour calendar progresses. Pros: simple, fits existing one-endpoint-per-series scheme. Cons: requires manual rotation; missed rotation = observer silent for that tournament.

A `scripts/tennis_endpoint_rotation.py` daily cron will Telegram-alert when current endpoint returns 0 events for 2 consecutive days, prompting rotation. (Lightweight; doesn't auto-rotate to avoid surfacing wrong endpoint.)

**MMA**:
```python
SPORT_MAP["KXUFCFIGHT"] = "mma_mixed_martial_arts"   # single, year-round
```

### Player/fighter name matching

`yes_sub_title` is full name. Match pipeline:

1. Lowercase both sides
2. Direct equality → match
3. Token-overlap fallback: split both into space-tokens, require all `yes_sub_title` tokens present in Pinnacle full name (handles "Carlos Alcaraz" matching "Carlos Alcaraz Garfia")
4. No `ODDS_TO_KALSHI_SHORT` additions needed for first cut; add disambig entries case-by-case as the daily quality report surfaces them

### Schema additions

None. Existing `odds_observations` table fits — player names go in `home_team` / `away_team` (no semantic h/a for individual sports; assignment is alphabetical or per Pinnacle order, doesn't matter for analysis). `is_yes_home` becomes informational only.

`phase` column: always `"pre"` for individual sports (PRE_GAME_ONLY enforced — no live betting on Kalshi Tennis/MMA). Round info (`"R16"`, `"QF"`, `"SF"`, `"F"` for Tennis; `"main"` for MMA single fights) stored as a new optional column? **No** — added inline parsing from title in promotion_eval at analysis time, not in the observer write path. Avoids schema migration; round/card-tier analysis is cell-expansion (next spec).

### Settlement

Existing `ticker_settlements` infrastructure handles binary YES/NO outcomes. Tennis/MMA are 2-way binary (no draw), so the same `scripts/backfill_ticker_settlements.py` works without change. Integration test added to confirm.

Edge case: **MMA fighter withdrawal / replacement**. Kalshi typically voids and re-lists with new fighter. Existing `ticker_settlements.status = 'cancelled'` path handles. Observer logs the cancelled record but skips ROI computation.

### Promotion-eval 4-bucket replay

[`scripts/promotion_eval.py`](../../../scripts/promotion_eval.py) extension — for each new sport (ATP, WTA, UFC), output:

```
=== ATP (Tennis ATP-only, 14-day window) ===
Active events scanned: Italian Open, French Open (partial)
Markets observed:  N (matched X.X% to Pinnacle)
Book depth p50:    $X  (threshold $20)
Pinnacle coverage: N_books_p50 = X, has_pinnacle = X.X%

Replay across kalshi_yes_ask buckets (5¢ edge gate):
  5-25¢   (longshot):  n=N  win-rate X.X%  ROI +XX% / -XX%  ← longshot archetype
  25-39¢  (underdog):  n=N  win-rate X.X%  ROI +XX% / -XX%  ← underdog archetype
  39-75¢  (mid-fav):   n=N  win-rate X.X%  ROI +XX% / -XX%  ← mid-fav
  75¢+    (chalk):     n=N  win-rate X.X%  ROI +XX% / -XX%  ← chalk-shadow

Decision: <bucket with top ROI, ≥+3% threshold>
```

Same block for WTA and UFC. If a sport has < 25 markets (`n_games < 25`), output `Sample insufficient — extend window`.

The `5¢ edge gate` means: include a market in the replay only if `eu_yes_prob - kalshi_yes_ask >= 0.05` (i.e., a sharp-book signal would have fired). This mirrors the existing midshot/longshot edge threshold.

### Daily data quality

[`scripts/daily_data_quality.py`](../../../scripts/daily_data_quality.py) extension — 23:50 UTC daily Telegram:

```
=== ATP Tennis ===
Active endpoint: tennis_atp_italian_open
Markets scanned today: 16  matched: 14 (87.5%)
Unmatched: 2 — Thiago Agustin Tirante (qual), Andrea Pellegrino (qual)
Pinnacle coverage: 14/14 (100%)

=== WTA Tennis ===
... (same template)

=== UFC ===
Active endpoint: mma_mixed_martial_arts
Markets scanned today: 0 (no card today; next: May 16)
Pinnacle coverage: n/a
```

Tracked rolling 7-day match rate; alert if drops below 70%.

## Implementation tasks (in order)

1. **Sport tokens + shorts** — 3 entries each in `SPORT_GAME_TOKENS`, `SPORT_SHORT_BY_TOKEN`, `OBSERVATION_ONLY_SPORTS`. Unit test for substring collision + frozenset membership.
2. **SPORT_MAP entries** — `KXATPMATCH`, `KXWTAMATCH`, `KXUFCFIGHT` → respective Odds API endpoints. Initial tennis endpoints = Italian Open.
3. **Player-name matching enhancement** — token-overlap fallback in the consensus-match path; preserve existing strict-equality fast path.
4. **promotion_eval 4-bucket** — add sport iteration over `["ATP", "WTA", "UFC"]`; 4-bucket replay computation; Telegram template extension.
5. **daily_data_quality extension** — per-sport block, rolling match-rate tracking.
6. **Settlement integration test** — feed sample `KXATPMATCH-...` and `KXUFCFIGHT-...` tickers through `backfill_ticker_settlements.py`, confirm rows insert correctly.
7. **Endpoint-rotation cron** — `scripts/tennis_endpoint_rotation.py` (daily): if active endpoint returns 0 events for 2 consecutive days, Telegram alert with suggested next endpoint.
8. **2-week eval cron** — extension to existing `scripts/weekly_live_phase_backtest.py` Monday cron to include ATP/WTA/UFC.

LOC estimate: ~350 (observer extensions ~200, promotion_eval ~80, daily_data_quality ~40, rotation cron ~30).

## Testing

### Unit tests (new)

- `tests/test_sport_tokens_tennis_mma.py`: SPORT_GAME_TOKENS contains ATPMATCH/WTAMATCH/UFCFIGHT in correct positions; SPORT_SHORT_BY_TOKEN maps correctly; OBSERVATION_ONLY_SPORTS includes ATP/WTA/UFC.
- `tests/test_player_name_match.py`: 6+ cases per sport — direct match, token-overlap match, nickname (e.g., "Dustin 'The Diamond' Poirier" → "Dustin Poirier"), accent (e.g., "Garbiñe Muguruza"), Jr/Sr, single-name (unlikely for Tennis/MMA but defensive).
- `tests/test_promotion_eval_buckets.py`: synthetic observations + settlements → 4-bucket ROI calculation, threshold logic.
- Existing tests must remain green; specifically [`tests/test_realized_fill_logging.py`](../../../tests/test_realized_fill_logging.py), [`tests/test_dedup_rollback.py`](../../../tests/test_dedup_rollback.py).

### Integration tests

- Mock Odds API response (sample Italian Open ATP fixture from `mma_mixed_martial_arts` style payload) → observer flow → odds_observations row + matched correctly.
- Mock UFC card response → same.
- Settlement backfill on sample fixtures.

### Smoke test (post-deploy, manual)

- T+0 deploy: check `journalctl -u kalshi-bot --since "5 minutes ago"` for Tennis/MMA scan log lines.
- T+24h: check daily quality report; match rate ≥ 70% target.
- T+24h: query `SELECT COUNT(*) FROM odds_observations WHERE sport IN ('ATP','WTA','UFC') AND date(timestamp) = today` ≥ 20.

## Rollout

```
T+0      Step 0 verified (DONE).  Code merge + deploy via existing
         EC2 systemctl restart pattern.
T+1d     First daily quality report.  Verify match rate.
T+1-7d   Italian Open WTA/ATP late rounds (Final May 18) + UFC 314 (May 16).
T+8-14d  French Open R128 starts May 26 (T+15).  Endpoint rotation
         needed pre-French Open — covered by rotation cron alert.
T+14d    Weekly backtest cron emits 4-bucket replay report.
T+15d    User reviews report → decision: write strategy spec (which
         archetype) OR extend observer window.
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Tennis endpoint rotation missed → silent observer | Medium | Daily cron alert when 0 events for 2 days |
| Player name match rate < 70% at start | Medium | Token-overlap fallback; daily report surfaces unmatched names for manual disambig |
| MMA fighter withdrawal pattern overflows cancelled-status handling | Low | Existing `ticker_settlements.cancelled` path; integration test for sample voided ticker |
| Tennis sample insufficient by T+14 (< 25 matches per ATP/WTA) | Medium | Extend window 7 more days; Italian Open R16-F + French Open R128-R64 should accumulate enough |
| Kalshi orderbook empty until event-day → kalshi_yes_ask = NULL on most observations | Confirmed | Replay sims only run on rows with ask populated; expect ~80% of total observation rows to have NULL ask but ~100% have Pinnacle |
| UFC card cadence (~weekly) → only 2 cards in 14d window | High | Acceptable — second spec can extend window if MMA undersample |
| Bucket replay shows no archetype passes +3% threshold | Medium | Acceptable outcome — defer strategy design, accumulate more data, or abandon sport |

## Open questions (resolved in implementation if data answers them)

- **Tennis surface effect (clay/grass/hard)**: Not in scope for first cut. Italian Open + French Open are both clay → no surface variance in initial window. Cell-split deferred.
- **MMA weight class effect**: Same — not in scope.
- **Pinnacle WTA reliability**: WTA Pinnacle coverage might be thinner than ATP. The data quality report will surface this via `n_books_p50` per sport.
- **Match round influence on edge** (R128 vs F): Deferred to cell-matrix in T+14+ spec.

## Deferred to next spec (≥ T+14)

- Strategy archetype selection from 4-bucket replay output
- Strategy file (e.g., `sports_longshot_v39_tennis` if longshot bucket wins) with concrete entry band, depth gate, sizing, dead zone — all data-driven
- Cell-matrix expansion if round/card-tier alpha differential ≥ 20pp
- Tournament-winner (`KXATP`, `KXWTA`) framework — different multi-outcome model
- NCAAB observer wiring (auto-triggered by `season_reminders.py` T-7 on 2026-10-27)

## Success criteria for THIS spec

By T+14, the observer + promotion_eval must produce:

1. ≥ 25 matched markets per sport (ATP, WTA, UFC if 2 cards)
2. ≥ 70% player-name match rate
3. ≥ 70% Pinnacle coverage
4. 4-bucket replay table per sport, with at least one bucket showing n ≥ 25 and a directional ROI signal (positive or negative — either informs next-spec decision)

If criteria pass → write strategy spec. If criteria fail → diagnose and extend window or abandon sport.
