# Deploy to AWS EC2

## 1. Launch EC2 Instance

- **Region**: us-east-1 (Virginia)
- **Instance**: t3.micro (Free Tier eligible)
- **AMI**: Ubuntu 22.04 LTS
- **Storage**: 8 GB gp3
- **Security Group**: Allow SSH (port 22) + HTTP (port 8080) from your IP

## 2. SSH In and Run Setup

```bash
ssh -i your-key.pem ubuntu@<public-ip>
curl -sL https://raw.githubusercontent.com/taejunoh/kalshi-arb/master/deploy/setup.sh | bash
```

## 3. Configure

```bash
cd /home/ubuntu/kalshi-arb

# Create .env (copy from your local .env)
nano .env

# Copy your RSA private key
# From your local machine:
# scp -i your-key.pem kalshi_private_key.pem ubuntu@<ip>:/home/ubuntu/kalshi-arb/
```

## 4. Install and Start the Service

```bash
sudo cp deploy/kalshi-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kalshi-bot
sudo systemctl start kalshi-bot
```

## 5. Monitor

```bash
# Status
sudo systemctl status kalshi-bot

# Live logs
journalctl -u kalshi-bot -f

# Dashboard (from your browser)
http://<public-ip>:8080
```

## 5b. Install daily ticker-settlement backfill (optional)

Runs once a day at 10:15 UTC to query Kalshi for settlement results of
observed sports markets. Fills `ticker_settlements` for universe-wide ROI
analysis (independent of which tickers the bot traded).

```bash
sudo cp deploy/kalshi-ticker-backfill.service /etc/systemd/system/
sudo cp deploy/kalshi-ticker-backfill.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-ticker-backfill.timer

# Verify next fire time
systemctl list-timers kalshi-ticker-backfill.timer

# Manual run (for initial backfill after observer accumulates data)
sudo systemctl start kalshi-ticker-backfill.service
journalctl -u kalshi-ticker-backfill.service -n 50
```

## 5c. Install daily data quality monitor (optional)

Runs daily at 10:45 UTC (30 min after settlement backfill) to report per-sport
observer health: row counts, n_books p10/p50, Pinnacle %, Kalshi OI median,
soccer 3-way leg balance. Sends Telegram if any sport degrades >25% vs 7-day
baseline. Exit code 2 = alerts present (timer treats as success — alert is
delivered out-of-band via Telegram).

```bash
sudo cp deploy/kalshi-data-quality.service /etc/systemd/system/
sudo cp deploy/kalshi-data-quality.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-data-quality.timer

# Verify next fire time
systemctl list-timers kalshi-data-quality.timer

# Manual run (dry-run = print report locally, no Telegram)
cd /home/ubuntu/kalshi-arb && venv/bin/python scripts/daily_data_quality.py --dry-run
```

## 5d. Install daily promotion eligibility evaluator (optional)

Runs daily at 10:50 UTC (5 min after data quality monitor) to evaluate
PROMOTE / HOLD / KILL recommendation per OBSERVATION_ONLY sport based on
last 7 days of observer + settlement data. Sends Telegram report.

Decision criteria: n_settled_games >= 25, top edge bucket ROI >= +3%,
n_books_eu p50 >= 8, Kalshi OI median > $500. Pinnacle % is shown for
context but NOT gated (live strategies use eu consensus, not Pinnacle).

```bash
sudo cp deploy/kalshi-promotion-eval.service /etc/systemd/system/
sudo cp deploy/kalshi-promotion-eval.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-promotion-eval.timer

# Manual run options
cd /home/ubuntu/kalshi-arb && venv/bin/python scripts/promotion_eval.py --dry-run
venv/bin/python scripts/promotion_eval.py --sport EPL --window 7
```

## 5e. Install macro consensus refresh cron (4h, for C1 macro_event)

Fetches CME FedWatch + Cleveland Fed CPI Nowcast every 4h and upserts
into `macro_consensus`. Required by `macro_event` strategy. **Note:**
some sources (FedWatch JSON) may be IP-blocked on residential ISPs but
work from EC2.

```bash
sudo cp deploy/kalshi-macro-consensus.service /etc/systemd/system/
sudo cp deploy/kalshi-macro-consensus.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-macro-consensus.timer

systemctl list-timers kalshi-macro-consensus.timer
sudo systemctl start kalshi-macro-consensus.service   # manual first run
journalctl -u kalshi-macro-consensus.service -n 50
```

## 5f. Install Deribit IV refresh cron (4h, for B2 crypto_fair_value)

Fetches BTC + ETH option chains from Deribit every 4h, extracts ATM IV
per expiry, upserts into `crypto_iv`. Required by `crypto_fair_value`
strategy.

```bash
sudo cp deploy/kalshi-deribit-iv.service /etc/systemd/system/
sudo cp deploy/kalshi-deribit-iv.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-deribit-iv.timer

systemctl list-timers kalshi-deribit-iv.timer
sudo systemctl start kalshi-deribit-iv.service   # manual first run
journalctl -u kalshi-deribit-iv.service -n 50
```

## 5g. Install Polymarket quote refresh service (1-min loop, for B1 xvenue_polymarket)

Long-running service (`Type=simple`, not a timer) that polls Polymarket
gamma-api every 60s and upserts into `polymarket_quotes`. Run as a
service rather than per-minute timer to avoid systemd journal churn.

```bash
sudo cp deploy/kalshi-polymarket-quote.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-polymarket-quote.service

systemctl status kalshi-polymarket-quote.service
journalctl -u kalshi-polymarket-quote.service -f
```

## 5h. Install Phase 0 prevalence scanners (4 services, A1/B1/B2/C1)

Each scanner mirrors its strategy's signal logic but does NOT fire any
trade. Logs every (would-fire / skip) evaluation to its signal table
for offline analysis. Run for >=1-2 weeks before deciding paper
promotion or live promotion.

Output tables:
- A1 (ladder): `ladder_violations`
- B1 (xvenue): `cross_venue_signals`
- B2 (crypto): `crypto_fair_value_signals`
- C1 (macro): `macro_event_signals`

```bash
cd /home/ubuntu/kalshi-arb
sudo cp deploy/kalshi-scanner-ladder.service /etc/systemd/system/
sudo cp deploy/kalshi-scanner-macro.service  /etc/systemd/system/
sudo cp deploy/kalshi-scanner-crypto.service /etc/systemd/system/
sudo cp deploy/kalshi-scanner-xvenue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-scanner-ladder.service \
                            kalshi-scanner-macro.service  \
                            kalshi-scanner-crypto.service \
                            kalshi-scanner-xvenue.service

# Verify
systemctl is-active kalshi-scanner-{ladder,macro,crypto,xvenue}.service
journalctl -u kalshi-scanner-ladder.service -n 30 -f
```

Cadences (script `--interval`): ladder 300s, xvenue 300s, crypto 300s,
macro 3600s (1h — macro_consensus refreshes 4h, so anything more
frequent than ~1h doesn't add new signals). Originally 60s for the
three fast scanners but reduced to 300s on 2026-05-11 after bot.db
write-lock contention with 8+ concurrent writers — see [Database
locking](#database-locking-wal-mode) below.

## 5i. Install +EV refresh scan (20-min; instant Telegram alerts + app-push trigger)

The soft-book +EV scan that powers the dashboard JSON **and** the instant
🟣 Telegram bet alerts (`softbook_ev_today.py` → `rec.alert_new_high`, inline
at :00/:20/:40 UTC — the unit's "no telegram" description refers only to the
4×/day digest, which is off here).

The `kalshi-ev-refresh.service.d/push-trigger.conf` drop-in fires the Expo
push worker the instant the scan finishes. Without it the app push only went
out on `kalshi-softbook-push.timer`'s next 5-min tick, so the app notification
trailed Telegram by ~5 min. With it the gap is 0–4 s (verified 2026-06-19).
The push worker dedups via `pushed_high` (idempotent) and the 5-min timer
remains a backstop.

```bash
sudo cp deploy/kalshi-ev-refresh.service /etc/systemd/system/
sudo cp deploy/kalshi-ev-refresh.timer   /etc/systemd/system/
# drop-in must preserve the .service.d/ subdir:
sudo mkdir -p /etc/systemd/system/kalshi-ev-refresh.service.d
sudo cp deploy/kalshi-ev-refresh.service.d/push-trigger.conf \
        /etc/systemd/system/kalshi-ev-refresh.service.d/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-ev-refresh.timer

# Verify the drop-in is active (ExecStartPost present) + timer scheduled
systemctl cat kalshi-ev-refresh.service | grep ExecStartPost
systemctl list-timers kalshi-ev-refresh.timer
```

## 5j. Install KXETH cme_basis signal emitter (5-min, cme_basis_follow_v1 paper-twin source)

Standalone KXETH-only `cme_basis_fade` signal emitter
(`scripts/cme_eth_signal_emitter.py`), decoupled from the frozen main crypto
observer (see crypto freeze 2026-05-27), so the `cme_basis_follow_v1` paper twin
keeps getting fresh signals without un-freezing the full observer. Reuses
`CmeBasisFadeObserver.evaluate()` + `_build_leg` from
`strategies/crypto_observer.py` (identical gates). ETH-only, REST spot, low-freq
timer with a per-market DB cooldown + RandomizedDelaySec jitter (avoids the
2026-05-27 boot-stampede SQLite contention).

```bash
sudo cp deploy/kalshi-cme-eth-emitter.service /etc/systemd/system/
sudo cp deploy/kalshi-cme-eth-emitter.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-cme-eth-emitter.timer

systemctl list-timers kalshi-cme-eth-emitter.timer
cd /home/ubuntu/kalshi-arb && venv/bin/python scripts/cme_eth_signal_emitter.py --dry  # manual dry-run
```

## Database locking (WAL mode)

bot.db now runs in **WAL journal mode** (set 2026-05-11). Apply this
on any new host one-time after bot.db is first created:

```bash
cd /home/ubuntu/kalshi-arb
venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/bot.db', timeout=30.0)
print('mode:', c.execute('PRAGMA journal_mode=WAL').fetchone()[0])
c.execute('PRAGMA synchronous=NORMAL')
c.close()
"
ls data/bot.db*   # should show bot.db, bot.db-wal, bot.db-shm
```

WAL = Write-Ahead Logging. Readers don't block writers and vice
versa, so dashboard reads, observer recordings, settlement checks
don't queue behind strategy writes. With 8+ concurrent writers
(kalshi-bot, 4 scanners, 3 crons, dashboard) the default rollback
journal mode produced cascading "database is locked" errors despite
busy_timeout=30s — verified 2026-05-11.

## 6. Common Commands

```bash
# Restart bot
sudo systemctl restart kalshi-bot

# Stop bot
sudo systemctl stop kalshi-bot

# Update code
cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot

# View trade count
curl localhost:8080/api/status

# View settlement PnL
curl localhost:8080/api/settlement
```

## Cost

- **t3.micro Free Tier**: 750 hrs/month free for 12 months
- **After Free Tier**: ~$8.50/month
- **Storage**: Free (under 30GB)
