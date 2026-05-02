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
