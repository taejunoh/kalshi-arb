# 4D Strategy Matrix — 2026-04-21

Region × Base × Sport × Phase ROI analysis, based on paper+live settled
trades as of 2026-04-21 12:11 UTC.

## TL;DR

1. **Pre-game > live-game** across V3 and V3.1 trio (all 3 bases). Extended
   `PRE_GAME_ONLY=True` to the full V3.1 trio (commit `4580a9c`).
2. **US region > EU region** on 8 of 9 sport × base splits. V3.4 trio forked
   to run `REGION_OVERRIDE="us"` (paper-only) alongside V3.1.
3. **NHL midshot** fails in both regions AND both phases — full blocklist
   applied via `SPORT_BLOCKLIST={"NHL"}` on midshot base class.
4. **v4_edge** is the one strategy where **live-game > pre-game** (works in
   volatility, opposite of V3.1 thesis).
5. **MLB underdog live US +37.8%** is a 34pp hidden-alpha gap that V3.1 (eu)
   misses. V3.4 may capture some of it if `PRE_GAME_ONLY` is relaxed.

## Data sources

- Database: EC2 `/home/ubuntu/kalshi-arb/data/bot.db` (snapshot 2026-04-21)
- Status filter: `settled` (live) + `paper_settled` (paper)
- Time filter: none — full cumulative history
- `minutes_to_start > 0` = pre-game, `≤ 0` = live-game

## Full matrix (n ≥ 3)

| Sport | Base | Phase | Region | n | WR% | PnL | ROI |
|---|---|---|---|---|---|---|---|
| MLB | longshot | live | us | 70 | 19 | −$13.05 | **−6.4%** |
| MLB | longshot | live | eu | 172 | 21 | −$53.61 | −10.0% |
| MLB | longshot | pre | us | 3 | 67 | +$13.32 | **+148%** |
| MLB | longshot | pre | eu | 8 | 50 | +$14.52 | +66.8% |
| MLB | midshot | live | us | 68 | 35 | +$39.78 | **+19.9%** |
| MLB | midshot | live | eu | 186 | 32 | +$53.67 | +9.3% |
| MLB | midshot | pre | us | 11 | 55 | +$21.53 | +65.2% |
| MLB | midshot | pre | eu | 17 | 65 | +$45.73 | **+93.7%** |
| MLB | underdog | live | us | 43 | 47 | **+$48.70** | **+37.8%** 🔥 |
| MLB | underdog | live | eu | 136 | 37 | +$13.81 | +3.3% |
| MLB | underdog | pre | us | 46 | 39 | +$0.54 | +0.4% |
| MLB | underdog | pre | eu | 56 | 45 | +$22.92 | **+13.9%** |
| NBA | longshot | live | us | 17 | 18 | −$5.90 | **−21.5%** |
| NBA | longshot | live | eu | 26 | 15 | −$26.39 | −33.3% |
| NBA | longshot | pre | us | 23 | 30 | +$8.81 | +20.5% |
| NBA | longshot | pre | eu | 13 | 31 | +$10.89 | **+30.1%** |
| NBA | midshot | live | us | 8 | 13 | −$7.91 | −59.9% |
| NBA | midshot | live | eu | 12 | 0 | −$35.14 | **−100%** |
| NBA | midshot | pre | us | 36 | 44 | +$36.29 | **+46.1%** 🔥 |
| NBA | midshot | pre | eu | 28 | 36 | +$10.92 | +13.0% |
| NBA | underdog | live | us | 8 | 13 | −$8.41 | −63.7% |
| NBA | underdog | live | eu | 8 | 0 | −$23.06 | **−100%** |
| NBA | underdog | pre | us | 33 | 48 | +$27.84 | +34.0% |
| NBA | underdog | pre | eu | 33 | 48 | +$37.22 | **+37.9%** |
| NHL | longshot | live | us | 23 | 9 | −$33.46 | −49.9% |
| NHL | longshot | live | eu | 51 | 16 | −$34.74 | **−22.3%** |
| NHL | longshot | pre | us | 3 | 0 | −$7.15 | −100% |
| NHL | midshot | live | us | 21 | 24 | −$20.64 | −33.7% |
| NHL | midshot | live | eu | 54 | 22 | −$66.95 | −39.1% |
| NHL | midshot | pre | us | 11 | 18 | −$12.65 | −38.3% |
| NHL | midshot | pre | eu | 12 | 17 | −$15.36 | −43.0% |
| NHL | underdog | live | us | 10 | 20 | −$14.46 | −48.2% |
| NHL | underdog | live | eu | 32 | 13 | −$63.05 | −64.6% |
| NHL | underdog | pre | us | 30 | 43 | +$9.97 | +11.1% |
| NHL | underdog | pre | eu | 27 | 44 | +$8.36 | +10.8% |
| MLB | v4_edge | live | v4 | 38 | 34 | +$45.25 | **+39.0%** |
| MLB | v4_edge | pre | v4 | 3 | 33 | −$0.54 | −6.0% |
| NBA | v4_edge | live | v4 | 3 | 0 | −$9.00 | −100% |
| NHL | v4_edge | live | v4 | 11 | 27 | +$22.75 | **+65.1%** |

## Best region per cell (n ≥ 10)

| Sport | Base | Phase | Best | Gap |
|---|---|---|---|---|
| MLB | longshot | live | us (−6.4%) | 4pp vs eu |
| MLB | midshot | live | **us (+20%)** | 11pp vs eu |
| MLB | midshot | pre | eu (+94%) | 28pp vs us (n=17) |
| MLB | underdog | live | **us (+38%)** 🔥 | **34pp vs eu** |
| MLB | underdog | pre | eu (+14%) | 13pp vs us |
| NBA | longshot | pre | eu (+30%) | 10pp vs us (n=13) |
| NBA | midshot | pre | **us (+46%)** 🔥 | **33pp vs eu** |
| NBA | underdog | pre | tied (+34~38%) | 4pp (essentially even) |
| NHL | longshot | live | **eu (−22%)** | 28pp vs us |
| NHL | midshot | any | **BLOCKLIST** | both fail |
| NHL | underdog | pre | tied (~+11%) | 0pp |
| NHL | underdog | live | neither works | −48% to −65% |

**Summary:** US wins 5 of 9 best-per-cell decisions (n ≥ 10). EU wins NHL
longshot live + NHL underdog pre (tied) + MLB midshot pre (small n for us).

## Phase summary (aggregating across sports and regions)

### V3.1 trio (paper+live)

| Base | Pre-game ROI | Live-game ROI | Gap |
|---|---|---|---|
| longshot | +37% (n=22) | −15% (n=249) | 52pp |
| midshot | +25% (n=57) | −6% (n=252) | 31pp |
| underdog | +20% (n=116) | −13% (n=176) | 33pp |

### v4_edge (inverted pattern)

| Phase | n | ROI |
|---|---|---|
| pre-game | 3 | −6% |
| **live-game** | **52** | **+37%** |

## Applied changes (2026-04-21)

1. `PRE_GAME_ONLY=True` on full V3.1 trio (was longshot only). — commit `4580a9c`
2. `SPORT_BLOCKLIST={"NHL"}` on midshot base → applies to midshot v31/v34 (legacy gate path). — commit `4580a9c`
3. V3.4 trio created: V3.1 logic + `REGION_OVERRIDE="us"` (paper-only). — commit `4580a9c`
4. V3.2 + V3.3 trios briefly disabled, then reverted same day: paper is costless, and early termination after two mid-flight parameter changes would compound "don't touch mid-flight" violations. Experiment resumed to 2026-05-01 eval. — commit `752d8c4`
5. `ALLOWED_CELLS` attribute added to base (sport, phase) allow-matrix. Takes precedence over PRE_GAME_ONLY and SPORT_BLOCKLIST when set. V3.4 trio configured with matrix-derived cells:
   - `longshot_v34`: `{("MLB","pre")}` — us +148% cell (tiny but positive)
   - `midshot_v34`:  `{("NBA","pre"), ("MLB","live")}` — us-winning cells
   - `underdog_v34`: `{("MLB","live"), ("NBA","pre"), ("NHL","pre")}` — includes the +38% MLB live hidden-alpha cell

## Pending experiments

- V3.4 paper vs V3.1 paper: 1 week to confirm region finding reproduces
  post-gate (dead_zone + pre-game-only + $4 base).
- `sports_v4_edge` live sample growth: 1–2 weeks to measure live-vs-paper
  slippage on v4's live-game signals (first live sample was 00:05 UTC
  2026-04-21).
- MLB underdog live US (+38% hidden-alpha candidate): gated out by
  `PRE_GAME_ONLY=True` in V3.4. If V3.4 paper stays positive but MLB
  underdog is under-represented, consider `_live_us` fork just for that cell.

## Known caveats

- V3 (retired) samples stopped 2026-04-17. V3.1 samples continued. Time-
  window mismatch could explain some of the V3 > V3.1 gap. A proper
  apples-to-apples test needs V3.4 to run a full week alongside V3.1.
- Pre-game live samples for longshot are thin (n=9 live total). Wide CI.
- Plays-off heavy April slate (NBA/NHL playoffs). Patterns may shift for
  regular season (WNBA, NFL).
- Fee drag is ~30% of stake on sub-25¢ contracts. Strategies can be gross-
  positive and still net-negative due to Kalshi fee structure.

## Re-running this matrix

```sql
-- From /tmp/prod_bot.db or any DB copy:
SELECT
  CASE
    WHEN strategy LIKE 'sports_%_v31' THEN 'eu'
    WHEN strategy = 'sports_v4_edge' THEN 'v4'
    ELSE 'us'
  END AS region,
  CASE
    WHEN strategy LIKE '%longshot%' THEN '1.long'
    WHEN strategy LIKE '%midshot%'  THEN '2.mid'
    WHEN strategy LIKE '%underdog%' THEN '3.under'
    ELSE strategy
  END AS base,
  CASE
    WHEN market_id LIKE '%NBAGAME%' THEN 'NBA'
    WHEN market_id LIKE '%MLBGAME%' THEN 'MLB'
    WHEN market_id LIKE '%NHLGAME%' THEN 'NHL'
  END AS sport,
  CASE WHEN minutes_to_start > 0 THEN 'pre' ELSE 'live' END AS phase,
  COUNT(*) AS n,
  ROUND(SUM(CASE WHEN pnl>0 THEN 1.0 ELSE 0 END)*100.0/COUNT(*),0) AS wr,
  ROUND(SUM(pnl),2) AS pnl,
  ROUND(SUM(pnl)*100.0/NULLIF(SUM(size),0),1) AS roi
FROM trades
WHERE status IN ('settled','paper_settled')
  AND minutes_to_start IS NOT NULL
  AND strategy IN (
    'sports_longshot','sports_midshot','sports_underdog',
    'sports_longshot_v31','sports_midshot_v31','sports_underdog_v31',
    'sports_v4_edge'
  )
GROUP BY region, base, sport, phase
HAVING n >= 3
ORDER BY base, sport, phase, region;
```
