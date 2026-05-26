#!/usr/bin/env python3
"""Kalshi WebSocket game-time measurement.

Runs for 60 minutes, subscribes to orderbook_delta + ticker on a set of
LIVE markets (MLB games in progress, WNBA active, NBA Finals if applicable).

Captures per-second metrics:
  - msg rate (overall + per channel)
  - per-market activity
  - delta vs snapshot ratio
  - any connection issues

Output:
  - JSON summary to /home/ubuntu/kalshi-arb/data/ws_metrics_{ts}.json
  - Telegram message with summary stats
"""
import asyncio
import base64
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import websockets
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ws_gametime")

KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KEY_PATH = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
DATA_DIR = "/home/ubuntu/kalshi-arb/data"
RUN_DURATION_S = int(os.getenv("WS_RUN_DURATION_S", "3600"))  # 60 min default
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")


def sign(timestamp_ms, method, path):
    with open(KEY_PATH, "rb") as f:
        priv = load_pem_private_key(f.read(), password=None)
    msg = f"{timestamp_ms}{method}{path}"
    sig = priv.sign(
        msg.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


def get_live_markets():
    """Pick MLB tickers from mlb_pitcher_observations + WNBA from wnba_game_observations.
    These represent today's active games where the market matters.
    """
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")
    cur = conn.cursor()
    # Today's MLB games (era_diff >= 1.0 to skip TBD games)
    cur.execute("""SELECT DISTINCT ticker_better_yes FROM mlb_pitcher_observations
                   WHERE ticker_better_yes IS NOT NULL AND era_diff IS NOT NULL
                   ORDER BY id DESC LIMIT 10""")
    mlb = [r[0] for r in cur.fetchall()]
    # WNBA tickers
    cur.execute("""SELECT DISTINCT ticker_home_yes FROM wnba_game_observations
                   WHERE ticker_home_yes IS NOT NULL ORDER BY id DESC LIMIT 6""")
    wnba_h = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ticker_away_yes FROM wnba_game_observations
                   WHERE ticker_away_yes IS NOT NULL ORDER BY id DESC LIMIT 6""")
    wnba_a = [r[0] for r in cur.fetchall()]
    # NBA (only Finals Game 1 if exists)
    cur.execute("""SELECT DISTINCT ticker_home_yes FROM nba_game_observations
                   WHERE ticker_home_yes IS NOT NULL ORDER BY id DESC LIMIT 4""")
    nba = [r[0] for r in cur.fetchall()]
    conn.close()
    return mlb + wnba_h + wnba_a + nba


def send_telegram(msg):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=10).read()
    except Exception as e:
        logger.warning(f"telegram: {e}")


async def main():
    if not (KEY_ID and os.path.exists(KEY_PATH)):
        logger.error(f"missing auth: KEY_ID={bool(KEY_ID)} KEY_PATH={KEY_PATH}")
        sys.exit(1)
    tickers = get_live_markets()
    if not tickers:
        logger.error("no live tickers found in DB")
        sys.exit(1)
    logger.info(f"watching {len(tickers)} tickers: {tickers[:5]}...")

    ts_ms = int(time.time() * 1000)
    sig = sign(ts_ms, "GET", "/trade-api/ws/v2")
    headers = {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
    }
    metrics = {
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": RUN_DURATION_S,
        "tickers": tickers,
        "by_type": defaultdict(int),
        "by_ticker": defaultdict(int),
        "per_minute": defaultdict(int),
        "connect_ms": None,
        "first_msg_ms": None,
        "reconnects": 0,
        "errors": [],
    }
    connect_start = time.time()
    end_at = connect_start + RUN_DURATION_S

    while time.time() < end_at:
        try:
            async with websockets.connect(WS_URL, additional_headers=headers, open_timeout=15) as ws:
                if metrics["connect_ms"] is None:
                    metrics["connect_ms"] = (time.time() - connect_start) * 1000
                sub_msg = {
                    "id": 1, "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta", "ticker"], "market_tickers": tickers},
                }
                await ws.send(json.dumps(sub_msg))
                logger.info(f"subscribed {len(tickers)} tickers, listening...")
                while time.time() < end_at:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(60.0, end_at - time.time()))
                    except asyncio.TimeoutError:
                        if time.time() >= end_at:
                            break
                        continue
                    if metrics["first_msg_ms"] is None:
                        metrics["first_msg_ms"] = (time.time() - connect_start) * 1000
                    msg = json.loads(raw)
                    mtype = msg.get("type", "?")
                    metrics["by_type"][mtype] += 1
                    mt = msg.get("msg", {}).get("market_ticker")
                    if mt:
                        metrics["by_ticker"][mt] += 1
                    minute = int((time.time() - connect_start) // 60)
                    metrics["per_minute"][minute] += 1
        except Exception as e:
            metrics["errors"].append(str(e))
            metrics["reconnects"] += 1
            logger.warning(f"connection error: {e}, reconnecting in 5s")
            ts_ms = int(time.time() * 1000)
            sig = sign(ts_ms, "GET", "/trade-api/ws/v2")
            headers["KALSHI-ACCESS-SIGNATURE"] = sig
            headers["KALSHI-ACCESS-TIMESTAMP"] = str(ts_ms)
            await asyncio.sleep(5)

    total_msgs = sum(metrics["by_type"].values())
    metrics["total_msgs"] = total_msgs
    metrics["avg_msgs_per_sec"] = total_msgs / RUN_DURATION_S
    metrics["end_utc"] = datetime.now(timezone.utc).isoformat()

    # Save JSON
    os.makedirs(DATA_DIR, exist_ok=True)
    out_file = f"{DATA_DIR}/ws_metrics_{int(connect_start)}.json"
    with open(out_file, "w") as f:
        json.dump({k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in metrics.items()}, f, indent=2)
    logger.info(f"saved {out_file}")

    # Telegram summary
    by_type_summary = ", ".join(f"{k}={v}" for k, v in sorted(metrics["by_type"].items(), key=lambda x: -x[1])[:5])
    top_tickers = sorted(metrics["by_ticker"].items(), key=lambda x: -x[1])[:5]
    top_ticker_str = "\n".join(f"  • `{t}`: {n}" for t, n in top_tickers)
    msg = (
        f"📊 *Kalshi WS game-time metrics ({RUN_DURATION_S/60:.0f}min)*\n"
        f"Total msgs: *{total_msgs}* ({metrics['avg_msgs_per_sec']:.1f}/sec)\n"
        f"Connect: {metrics['connect_ms']:.0f}ms / first: {metrics['first_msg_ms']:.0f}ms\n"
        f"By type: {by_type_summary}\n"
        f"Reconnects: {metrics['reconnects']}, errors: {len(metrics['errors'])}\n"
        f"Top tickers:\n{top_ticker_str}"
    )
    send_telegram(msg)
    logger.info(msg)


if __name__ == "__main__":
    asyncio.run(main())
