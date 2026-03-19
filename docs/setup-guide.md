# Polymarket Arb Bot — Setup Guide

## Prerequisites
- Python 3.11+
- A Polymarket account with API access
- USDC on Polygon network (for live trading)

## 1. Polymarket Account Setup

1. Go to https://polymarket.com and create an account
2. Navigate to Settings → API Keys
3. Generate an API key — save the key, secret, and passphrase
4. Your private key is your wallet's private key (the one used to sign transactions)

## 2. Proxy Wallet

Polymarket uses proxy wallets on Polygon. After creating your account:
1. Deposit USDC via the Polymarket bridge
2. Your proxy wallet address is visible in your account settings
3. This is the wallet that will execute trades

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
# Edit .env with your API credentials
```

## 5. Running (Paper Mode)

```bash
python main.py
```

The bot starts in paper mode by default. It will:
- Connect to Binance price feeds
- Scan Polymarket for crypto markets
- Detect sum arb opportunities
- Log simulated trades (no real orders)

## 6. Switching to Live Mode

After validating in paper mode (48+ hours recommended):
1. Edit `.env`: change `TRADING_MODE=paper` to `TRADING_MODE=live`
2. Ensure USDC is funded in your Polymarket proxy wallet
3. Restart the bot

## 7. Monitoring

- Console output: real-time trade logs
- File logs: `logs/bot.log` (daily rotation)
- Trade history: `logs/trades.csv`
- Database: `data/bot.db` (SQLite)
