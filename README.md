# kalshi-arb

**An automated trading system for prediction markets and sports betting. Archived September 2026.**

Five and a half months, 1,278 commits, 24 strategies, 1,983 real-money trades.
Final result: **−$2,004.37**. Strip the exchange fees out and the same trades
lose **$969.36** — fees more than doubled the loss.

I am archiving it because the system worked and the answer it gave was no. This
README is about how it was built and, more usefully, about the part that took
longest to get right: the machinery for finding out whether a strategy is real.

---

## What it did

Two independent surfaces, one codebase:

**1. Kalshi (prediction market exchange).** Automated trading against Kalshi's
event contracts — sports game winners, crypto price ranges, economic releases.
Strategies placed real orders through Kalshi's v2 API, with position management,
fill tracking, and settlement reconciliation.

**2. Soft-book +EV (sportsbook value betting).** Not automated betting — a
detection and alerting system. It compared eight New York-legal sportsbooks
against Pinnacle (the sharp reference) every 10 minutes, and pushed the
mispricings to a phone app and Telegram for manual placement. It published its
own track record publicly, scored under the rule that was live on each pick's
date — that page and the TestFlight app were both retired with the project.

Both ran unattended on a single t3.medium EC2 instance on systemd timers.

---

## Architecture

```
                    ┌──────────────────────────────────┐
   Kalshi API ─────▶│  main.py — strategy scan loop    │
   Odds API   ─────▶│  order_manager, position sizing  │──▶ Kalshi orders
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────▼───────────────────┐
                    │  SQLite (bot.db, 7.2 GB)         │
                    │  trades · odds_observations      │
                    └──────────────┬───────────────────┘
                                   │
   ┌───────────────────────────────┼───────────────────────────────┐
   │                               │                               │
┌──▼──────────────┐   ┌────────────▼─────────────┐   ┌─────────────▼──────┐
│ FastAPI         │   │ softbook_ev_* pipeline   │   │ Expo / React       │
│ dashboard       │   │ recorder → paper ledger  │   │ Native app         │
│ strategy-monitor│   │ → settle → CLV → export  │   │ (TestFlight)       │
└─────────────────┘   └────────────┬─────────────┘   └────────────────────┘
                                   │
                       ┌───────────▼────────────┐
                       │ Telegram alerts        │
                       │ keepmypick.com         │
                       └────────────────────────┘
```

**Stack:** Python 3.12, FastAPI, SQLite, systemd timers, React Native (Expo),
vanilla JS dashboard. No queue, no ORM, no container. One box, one database
file per concern, ~40 timers.

The unglamorous choices were mostly right. SQLite handled 9.9M odds
observations; the thing that eventually hurt was not throughput but a missing
index and an unbounded `GROUP BY` that OOM-killed the host. systemd timers
replaced what would otherwise have been Celery, and the failure modes were
legible: a unit either ran or it didn't, and `journalctl` said which.

---

## The interesting part: how you find out you're wrong

Most of the code that survived contact with reality is not strategy logic. It
is the apparatus that stops you from believing a strategy that isn't there.
This is what I would actually reuse.

### 1. Era-scored history

The public track record re-derives itself from source every morning. That means
a rule change silently rewrites the past — every historical pick gets scored
under today's rule, and since you changed the rule *because it looked better on
the data you already had*, the rewrite always flatters.

Concretely, one tier change would have moved the published record overnight,
with no bet settling:

```
published now    140-160    ROI  +2.13%    n=300
after rewrite    332-316    ROI +13.90%    n=648
```

Six times the ROI and double the sample, from an edit. The fix is
`BET_TIER_ERAS` — a newest-first tuple of (date, rule), with `bet_tiers_at(market, day)`
for scoring history and `bet_tiers(market)` for what is live now. The live
policy still has exactly one definition; history is scored under whatever was
true at the time. A test ties the published changelog to the era tuple, so a new
era without a disclosure fails CI.

The internal dashboard never got this, and the difference is instructive: the
same cohort reads **+11.0%** on the dashboard and **−2.5%** on the public page.
Both are arithmetically correct. Only one is a track record.

### 2. One definition of "a bet"

`core.is_bet(first_entry, market, tier)` is the single source of truth for the
Telegram tag, the app's flag, the push worker, the paper ledger, and the public
export. It exists because those five once disagreed.

`tier` is a required argument rather than a defaulted one, deliberately: a
default lets a caller that nobody updated keep enforcing the old policy in
silence. Making it required turns a silent divergence into an import error.
That choice later caught a stale module shadowing the real one — see below.

### 3. Paper ledgers that record what you did *not* do

The paper ledger records every +EV opportunity at every confidence tier,
including tiers that were never alerted. Without it, "should we also bet the
Med tier?" can only be answered by turning it on and waiting months.

This has a sharp edge. Those unsent rows are *observations*, not a record, and
mixing them into a performance number inflates it. Of one tier's 760 recorded
bets, 556 predate the rule that made them bets at all — and those 556 are
exactly the rows the promotion decision was read off. In-sample: +16.4%.
Forward, after the change: **+0.19%**.

### 4. Closing-line value, and the confound that ruined it

Realized P&L is a hopeless measurement instrument here. Per-bet standard
deviation is ~120%, so confirming a +5% edge needs about **4,500 settled bets**.
No cohort in this project ever reached 1,200.

Closing-line value resolves far faster — the same 457 bets give ±0.3pp on CLV
versus ±10.7pp on ROI. So CLV became the primary metric.

Then it produced a beautiful result: a clean monotone dose-response where higher
entry EV meant worse CLV, reproducing independently in both halves of the data
(t = −4.16 and −4.13, near-identical slopes) and beating 2,000 placebo cuts
0/2000. Every robustness check passed.

It was an artifact. The two quantities share a term:

```
entry EV    = open_fair  × soft_price − 1
CLV         = close_fair − open_fair          ← shares open_fair
closing EV  = close_fair × soft_price − 1     ← clean
```

Noise in `open_fair` selects moments where it is high, which is the definition
of a high-EV pick; it then reverts, which is the definition of negative CLV.
The relationship appears with zero underlying signal. Measured the clean way,
the correlation vanishes: **r = −0.186 (t = −5.69) → +0.027 (t = +0.80)**, and
nothing is significant in any split.

The lesson generalizes past this codebase: **reproducibility and placebo
survival are not evidence of causation.** A mechanical artifact reproduces
perfectly and passes placebo tests by construction. Checking whether your two
variables share a term is the only defence, and no amount of resampling
substitutes for it.

### 5. Pre-registration

`scripts/usopen_med_ml_prereg.py` fixes thresholds for a hypothesis test in
code, committed before the data existed, with a test that duplicates the
constants so moving a threshold takes two files in one diff and has to be
stated. It is a small file and it is the most valuable thing here.

### 6. Reliability tiers on every recommendation

Every simulation result carries a ★1–5 confidence tier, and a promotion to real
money requires ★4+. Recommendations without an uncertainty label are how a
`n=11` result becomes a live strategy.

---

## Results

### Real money (Kalshi), 2026-04-15 → 2026-08-26

| | trades | P&L | fees |
|---|---|---|---|
| Sports | 1,696 | −$1,769.15 | $941.38 |
| Crypto | 247 | −$195.53 | $82.33 |
| Other | 40 | −$39.69 | $11.31 |
| **Total** | **1,983** | **−$2,004.37** | **$1,035.01** |

Worst: `sports_underdog_v32` −$554 (n=143) · `sports_playoff_underdog_v1`
−$317 · `sports_underdog_v31` −$255 · `sports_midshot_v31` −$159.
Best: `sports_underdog_v35` +$95 · `sports_midshot_v35` +$22.

**Without fees, the same trades would have lost $969.36. Fees made it
$2,004.37.** Kalshi charges a flat $0.07 per contract, which
on a $0.30 contract is a 23% toll. The gross edge and the fee were the same
order of magnitude at every price band tested — at 25–35¢, gross +5.2% against a
5.26% fee. Most strategies here were not wrong about direction. They were
correct by less than the cost of transacting.

### Soft-book +EV

Public first-entry record at retirement: 677 picks, 309–368, +$19.15 — approximately
breakeven, which is what the code's own docstring predicted in June ("no
confirmed pick edge; ≈BE bonus-clearing vehicle, bet small").

Measured properly, entry prices sat **at** the closing line: closing EV
**+0.30%, 95% CI [−0.45, +1.04]** (n=534 against true closes). Not beaten by
the market. Not beating it either.

Nine tier × market cells were tested; none survived multiple-comparison
correction. Consistently: the larger the sample, the closer to zero.

---

## Why it stopped

Not because it lost money — $2,004 over five months was tuition, and I would
pay it again for what the measurement infrastructure taught me.

It stopped because the economics stopped closing:

- Data cost **$120/month** (The Odds API, 5M credits — running at 94% utilization).
- Monthly handle was ~385 picks × $25 = **~$9,625**.
- Break-even therefore requires a **1.25%** edge.
- Measured edge: **+0.30%, CI [−0.45, +1.04]** — *the entire interval sits below break-even.*

Even the optimistic end of what the data permits does not pay for the tool that
measures it. And the trap is closed on both sides: the project's own rule is no
size increase until forward CLV confirms an edge, but the edge only pays for
itself at much larger size. You cannot afford the proof without the size, and
you cannot justify the size without the proof.

Continuing to measure fails the same test. Three or four more months costs
$360–480 to learn whether a ~$100/month edge exists.

Kalshi trading stops for a simpler reason: seven distinct edge vectors were
tested and closed, and the exchange priced sports better than Pinnacle did.
There was no remaining hypothesis worth the fee drag.

---

## What I'd keep

- **`BET_TIER_ERAS` / era-scoring.** Any system publishing a track record across
  a rule change needs this or it is lying by default.
- **Single-source rule definitions with required arguments.** Five surfaces
  disagreeing about "what counts" is the most expensive class of bug here.
- **Pre-registration in code.** Cheap, and the only real defence against
  choosing the threshold after seeing the result.
- **Checking for shared terms before believing a correlation.** The one that
  nearly got me.

## What I'd do differently

- **Measure the fee first.** A flat per-contract fee against a probabilistic
  payout is a price-dependent tax; at longshot prices it exceeds any plausible
  edge. This is computable on day one, before any strategy exists. I computed it
  in month four.
- **Pick markets where measurement is possible.** With ~120% per-bet SD, a
  realistic edge is unmeasurable in any tolerable time. That is a property of
  the market, knowable in advance, and it should have been a selection criterion
  rather than a discovery.
- **Fewer strategy variants, more instrumentation.** Twenty-four strategies
  produced less knowledge than the six measurement tools did.

---

## Repository notes

- `strategies/` — strategy implementations (v-numbered variants are iterations, mostly retired)
- `core/` — order management, settlement, config, CLV tracking
- `scripts/` — the soft-book pipeline, analysis and one-off research scripts
- `monitoring/dashboard/` — FastAPI + vanilla JS ops dashboard
- `app/` — React Native (Expo) companion app
- `tests/` — 200 test files
- `docs/analysis`, `docs/research` — investigation writeups

Credentials come from `.env` (see `.env.example`); nothing runs without it. The
databases are gitignored.

The companion iOS app (TestFlight) and the public track-record site were both
retired in September 2026 along with the trading. Nothing here runs any more:
the Odds API subscription is cancelled and the EC2 host is gone.

Not maintained, not accepting issues. Published as a record.
