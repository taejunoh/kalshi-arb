# Kalshi Arb Bot — Setup Guide

## Prerequisites
- Python 3.11+
- A Kalshi account with API access
- Funds deposited via bank transfer (for live trading)

## 1. Kalshi Account Setup

1. Go to https://kalshi.com and create an account
2. Navigate to Account → API Keys
3. Generate an API key — save the API Key ID
4. Download the RSA private key PEM file and store it securely

## 2. Fund Your Account

Kalshi accepts deposits via bank transfer (ACH). No cryptocurrency or wallet setup is needed.
1. Go to Account → Deposits
2. Link your bank account
3. Transfer funds — they will be available for trading once settled

## 3. Installation

```bash
cd polymarket-arb
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Configuration

```bash
cp .env.example .env
# Edit .env with your Kalshi API credentials:
#   KALSHI_API_KEY_ID=your_key_id
#   KALSHI_RSA_PRIVATE_KEY_PATH=/path/to/your/private_key.pem
```

## 5. Running (Paper Mode)

```bash
python main.py
```

The bot starts in paper mode by default. It will:
- Connect to Binance price feeds
- Scan Kalshi for event markets
- Detect sum arb opportunities
- Log simulated trades (no real orders)

## 6. Switching to Live Mode

After validating in paper mode (48+ hours recommended):
1. Edit `.env`: change `TRADING_MODE=paper` to `TRADING_MODE=live`
2. Ensure funds are deposited in your Kalshi account
3. Restart the bot

## 7. Monitoring

- Console output: real-time trade logs
- File logs: `logs/bot.log` (daily rotation)
- Trade history: `logs/trades.csv`
- Database: `data/bot.db` (SQLite)
