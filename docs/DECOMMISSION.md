# Decommission runbook — 2026-09-12

Ordered shutdown. The order is the point: several of these steps are unsafe or
irreversible if taken early, and two of them are unsafe if taken *late*.

Live surface as of 2026-08-31:

- `TRADING_MODE=paper` globally, with a real-money allowlist:
  `sports_mlb_pitcher_edge_v2_1`, `sports_mlb_pitcher_edge_v1`,
  `sports_tennis_underdog_v1`, `sports_tennis_live_fade_v1`
  (combined −$19.34 over n=404 — effectively flat)
- 7 running services, 59 enabled timers
- Public surfaces: keepmypick.com, TestFlight app, Telegram alerts

---

## Ordering constraints

| Must happen before | because |
|---|---|
| stop trading → stop timers | settlement runs on timers; killing them first strands open positions unsettled |
| settlement finishes → cancel Odds API | settlement reads `/scores`; no API, no grading, and the ledger closes wrong |
| rotate every leaked credential → repo public | two live secrets sat in git history (the Odds API key and the Telegram bot token); publishing before rotation exposes working credentials |
| announce → take down keepmypick | anyone following the public record deserves notice, not a 404 |

---

## 1. Pre-flight (run the day before)

Confirm nothing real-money is open and unsettled:

```bash
ssh kalshi-ec2 'sqlite3 /home/ubuntu/kalshi-arb/data/bot.db "SELECT strategy, status, COUNT(*), MIN(date(timestamp)) FROM trades WHERE status IN (\"filled\",\"placing\") GROUP BY 1,2;"'
```

Real-money rows are `filled` (open) and `settled` (closed); paper rows use
`paper_filled`/`paper_settled`. Do not filter on `NOT IN ('settled','closed')`
— that returns every paper row in the ledger. As of 2026-09-01 the only hits
are 17 orphan rows from April–May with no live position behind them; anything
newer is a real open position.

Empty output (or only those orphans) is the go signal. Anything listed is an open position — let it
settle before proceeding, or close it deliberately.

Also confirm nothing is mid-flight on the exchange:

```bash
ssh kalshi-ec2 'sqlite3 /home/ubuntu/kalshi-arb/data/bot.db "SELECT COUNT(*) FROM orders WHERE status IN (\"open\",\"pending\",\"resting\");"'
```

## 2. Stop real money, keep everything else running

This is the surgical step: it ends risk without touching data collection or
settlement. Do not stop services here.

```bash
ssh kalshi-ec2 'cd /home/ubuntu/kalshi-arb && cp .env .env.bak-decom && sed -i "s/^LIVE_STRATEGIES=.*/LIVE_STRATEGIES=/" .env && grep -E "^(TRADING_MODE|LIVE_STRATEGIES)=" .env'
```

Restart so the bot re-reads it (config is cached at startup — the same reason a
rotated API key needs a restart):

```bash
ssh kalshi-ec2 'sudo systemctl restart kalshi-bot && sleep 15 && systemctl is-active kalshi-bot'
```

From here the system is fully paper. Everything below can take its time.

## 3. Let settlement drain

Give open positions from step 1 time to grade out. Re-run the pre-flight query
until it is empty. The `/scores` feed lags real-world finishes, and tennis and
the smaller leagues lag the most — this is exactly the no-feed gap that needed
a backfill in June, so do not assume same-day.

## 4. Record the final numbers before anything is deleted

```bash
ssh kalshi-ec2 'cd /home/ubuntu/kalshi-arb/scripts && ../venv/bin/python usopen_med_ml_prereg.py'
```

Run it for the record even though the termination decision does not depend on
the verdict. If the US Open has not finished settling it will print
INSUFFICIENT — that is a legitimate outcome and should be recorded as one. Do
not lower the pre-registered thresholds to force a verdict.

Snapshot the closing state:

```bash
ssh kalshi-ec2 'cd /home/ubuntu/kalshi-arb && sqlite3 data/bot.db "SELECT COUNT(*), ROUND(SUM(pnl),2), ROUND(SUM(fee),2) FROM trades WHERE status IN (\"settled\",\"closed\") AND pnl IS NOT NULL;" && cp web/results.json /home/ubuntu/results-final.json'
```

Pull the databases down before the host goes away — they are the only copy, and
they are gitignored:

```bash
scp kalshi-ec2:/home/ubuntu/kalshi-arb/data/softbook_paper.db ~/archive/
scp kalshi-ec2:/home/ubuntu/kalshi-arb/data/softbook_ev.db ~/archive/
scp kalshi-ec2:/home/ubuntu/results-final.json ~/archive/
# bot.db is 7.2 GB — decide whether the trades table alone is enough:
ssh kalshi-ec2 'sqlite3 /home/ubuntu/kalshi-arb/data/bot.db ".dump trades" | gzip > /home/ubuntu/trades.sql.gz'
scp kalshi-ec2:/home/ubuntu/trades.sql.gz ~/archive/
```

## 5. Notify, then take down the public surfaces

Anyone following the published track record should hear it from you rather than
from a dead link. Then retire keepmypick.com and stop the TestFlight build.

Telegram alerts stop on their own once the timers do (step 6), but a final
message closing it out is cheaper than confusion.

## 6. Stop the machine

Stop the timers **before** the services. `kalshi-livefade-*` units declare
`Wants=kalshi-bot`, so stopping the bot while they are still armed will start it
again.

```bash
ssh kalshi-ec2 'sudo systemctl stop kalshi-livefade-firstorder-watch.timer kalshi-livefade-postfix-gate.timer'
ssh kalshi-ec2 'for t in $(systemctl list-unit-files --type=timer --no-pager | grep kalshi | awk "{print \$1}"); do sudo systemctl disable --now $t; done'
ssh kalshi-ec2 'for s in $(systemctl list-units --type=service --state=running --no-pager | grep kalshi | awk "{print \$1}"); do sudo systemctl disable --now $s; done'
ssh kalshi-ec2 'systemctl list-units --all --no-pager | grep kalshi | grep -c running'   # expect 0
```

## 7. Cancel the Odds API — and rotate the key

Cancel the subscription, **then regenerate the key at the provider** if they
offer it. Cancelling alone leaves a key that works again the moment anyone
re-subscribes on that account, and step 8 puts that key in front of the public.
Two things learned on 2026-09-01: the provider's docs describe no self-service
regeneration (ask support), and a cancelled subscription stays active until the
end of the billing cycle, so cancellation is not revocation.

## 8. Repo → public

Only after step 7, and only after every credential below is dead.

There were **two** leaked credentials, not one. This runbook originally knew
about the first only:

- The Odds API key sat in `tests/test_tennis_endpoint_rotation.py` from
  2026-07-14 until it was replaced (two commits).
- The Telegram bot token was hardcoded in
  `.github/workflows/atm_hold_monitor.yml` from 2026-06-07 onward — in HEAD
  and in ~410 commits — and was the same token the live bot used.

**Status 2026-09-01:** the Telegram token was revoked at BotFather and the
replacement deployed to EC2; the workflow now reads `secrets.TELEGRAM_BOT_TOKEN`
and `secrets.TELEGRAM_CHAT_ID`. Both strings were scrubbed from every branch
with `git filter-repo --replace-text` and force-pushed (every SHA changed; the
commit links under `docs/` were remapped from the commit map). The Odds API key
is still the live key on EC2 until step 7 runs.

**Scrubbing is not enough on GitHub.** A force-push does not remove commits
reachable from pull-request refs (`refs/pull/*/head`); the pre-rewrite HEAD is
still served by the API afterwards. Either ask GitHub Support to purge the
cached commits, or rely on rotation — which is why rotation is mandatory and
the scrub is only hygiene. The original options, for the record:

- **Publish as-is.** Safe once the key is cancelled and rotated, but GitHub
  secret scanning will flag it and it is visible to anyone reading history — a
  hardcoded credential in a portfolio repo reads badly regardless of whether it
  still works.
- **Scrub first** (recommended). The repo is 7.4 MB; this takes minutes:

```bash
git clone --mirror git@github.com:taejunoh/kalshi-arb.git kalshi-arb-scrub
cd kalshi-arb-scrub
echo '<THE-KEY>==>REDACTED' > /tmp/replacements.txt
git filter-repo --replace-text /tmp/replacements.txt
git push --force --mirror
```

Rewriting history changes every SHA, which invalidates the commit links in the
README and in `docs/`. `filter-repo` leaves a commit map
(`filter-repo/commit-map` inside the mirror) — remap from it rather than
hand-editing.

Verify before flipping visibility, once per secret:

```bash
git grep -I -F "<THE-SECRET>" $(git rev-list --all) | head    # expect empty
```

A cheap sweep for anything the runbook missed (this is how the Telegram token
was found):

```bash
git grep -I -E -l 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|[0-9]{8,10}:AA[0-9A-Za-z_-]{33}|ghp_[A-Za-z0-9]{36}' $(git rev-list --all)
```

## 9. Host

Snapshot the EBS volume before terminating, or keep the instance stopped for a
while — a stopped t3.medium costs only the volume. There is no rush, and the
databases are the only record of five months of observations.

---

## What is deliberately not in this list

**Deleting the ledgers.** The paper ledger and the published record are bet
records. They are the evidence behind everything written about this project, and
they cost five months to produce. Archive them; do not delete them.

**Lowering the pre-registered thresholds to get a verdict in step 4.** If the
US Open sample is short, the answer is INSUFFICIENT. Adjusting a threshold after
seeing the data is the specific failure the registration exists to prevent, and
it would be a strange note to end on.
