#!/usr/bin/env python3
"""Kalshi WebSocket POC — connect, authenticate, subscribe to a few
markets' orderbook_delta + ticker channels. Log:
  - connection success / auth
  - first message receipt latency
  - update frequency
  - any errors

Goal: 3-5 hour POC to decide if WS migration is worth it. Reads RSA key
from same path as REST client.
"""
import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

import websockets
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

# Load env
KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KEY_PATH = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH")
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

# Test markets — pick a few WNBA + MLB tickers that are currently active
TEST_TICKERS = [
    "KXMLBGAME-26MAY261940NYYKC-NYY",   # tonight's NYY@KC (era_diff=8.32, our pick)
    "KXMLBGAME-26MAY262145AZSF-AZ",    # tonight's AZ@SF (era_diff=3.86)
    "KXNBAGAME-26MAY26SAOKC-OKC",      # Game 1 NBA WCF
]


def sign(timestamp_ms, method, path):
    with open(KEY_PATH, "rb") as f:
        private_key = load_pem_private_key(f.read(), password=None)
    msg = f"{timestamp_ms}{method}{path}"
    signature = private_key.sign(
        msg.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


async def run():
    if not (KEY_ID and KEY_PATH):
        print(f"missing KALSHI_API_KEY_ID or KALSHI_RSA_PRIVATE_KEY_PATH")
        sys.exit(1)

    ts_ms = int(time.time() * 1000)
    sig = sign(ts_ms, "GET", "/trade-api/ws/v2")
    headers = {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
    }
    print(f"[{datetime.now(timezone.utc).isoformat()}] connecting to {WS_URL}")
    connect_start = time.time()
    async with websockets.connect(WS_URL, additional_headers=headers, open_timeout=15) as ws:
        connect_elapsed = (time.time() - connect_start) * 1000
        print(f"[{datetime.now(timezone.utc).isoformat()}] connected ({connect_elapsed:.0f}ms)")

        # Subscribe to orderbook_delta + ticker for test markets
        sub_msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "ticker"],
                "market_tickers": TEST_TICKERS,
            },
        }
        await ws.send(json.dumps(sub_msg))
        print(f"[{datetime.now(timezone.utc).isoformat()}] subscribed: {TEST_TICKERS}")

        # Listen for 30 seconds, log msgs
        msg_count = 0
        by_type = {}
        first_msg_time = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                break
            if first_msg_time is None:
                first_msg_time = time.time()
                first_latency_ms = (first_msg_time - connect_start) * 1000
                print(f"[{datetime.now(timezone.utc).isoformat()}] first msg @ {first_latency_ms:.0f}ms after connect")
            msg = json.loads(raw)
            mtype = msg.get("type", "?")
            by_type[mtype] = by_type.get(mtype, 0) + 1
            msg_count += 1
            # Print first 5 of each type for inspection
            if by_type[mtype] <= 3:
                print(f"  [{mtype}] {json.dumps(msg)[:200]}")

        elapsed = time.time() - connect_start
        print(f"\n[summary] 30s window:")
        print(f"  total msgs: {msg_count}")
        print(f"  by type: {by_type}")
        print(f"  avg msgs/sec: {msg_count/elapsed:.1f}")


if __name__ == "__main__":
    asyncio.run(run())
