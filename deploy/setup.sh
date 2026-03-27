#!/bin/bash
# Kalshi Arb Bot — EC2 Setup Script
# Run on a fresh Ubuntu 22.04 EC2 instance (t3.micro, us-east-1)
#
# Usage:
#   1. Launch EC2 t3.micro (Ubuntu 22.04, us-east-1)
#   2. SSH in: ssh -i your-key.pem ubuntu@<ip>
#   3. Copy this script and run: bash setup.sh

set -e

echo "=== Installing dependencies ==="
sudo apt update -y
sudo apt install -y python3.11 python3.11-venv python3-pip git

echo "=== Cloning repo ==="
cd /home/ubuntu
git clone https://github.com/taejunoh/kalshi-arb.git
cd kalshi-arb

echo "=== Setting up Python venv ==="
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pymupdf

echo "=== Creating data directory ==="
mkdir -p data logs

echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Create .env file:  nano /home/ubuntu/kalshi-arb/.env"
echo "  2. Copy your kalshi_private_key.pem to /home/ubuntu/kalshi-arb/"
echo "  3. Enable the bot service:  sudo systemctl enable kalshi-bot && sudo systemctl start kalshi-bot"
echo "  4. Check status:  sudo systemctl status kalshi-bot"
echo "  5. View logs:  journalctl -u kalshi-bot -f"
