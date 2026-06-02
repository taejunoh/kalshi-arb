#!/usr/bin/env python3
"""Phase 0 WS recording harness (speed-edge experiment) — captures ms-resolution
Kalshi tick data for OFFLINE analysis. NO TRADING.

Subscribes orderbook_delta + trade + ticker on today's most-liquid game markets,
reconstructs the order book from snapshot+deltas, and writes one row per event
(our receive ms + exchange ts_ms + reconstructed best yes bid/ask + sizes + trade
price/aggressor) to data/ws_ticks_<date>.db.

Enables the three Phase-1 measurements:
  1. aggressor-follow  — does a trade-sweep predict the next-Ns move > our ~130ms latency?
  2. quote-staleness   — after fair value moves, how long does a stale quote persist?
  3. repricing-speed   — distribution of event->reprice latency by liquidity tier.
  (exch_ms vs recv_ms also gives the live feed-delivery latency.)

Run detached during a game window:
  WS_REC_DURATION_S=28800 nohup venv/bin/python scripts/kalshi_ws_record.py &
"""
import asyncio, base64, json, os, sqlite3, sys, time
from collections import defaultdict
from datetime import datetime, timezone

import websockets
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

sys.path.insert(0, "/home/ubuntu/kalshi-arb")


def _load_env(path="/home/ubuntu/kalshi-arb/.env"):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KEY_PATH = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
DUR = int(os.getenv("WS_REC_DURATION_S", "28800"))   # 8h default
MAX_TICKERS = int(os.getenv("WS_REC_MAX_TICKERS", "40"))
OUT = os.getenv("WS_REC_DB",
                f"/home/ubuntu/kalshi-arb/data/ws_ticks_{datetime.now(timezone.utc).strftime('%Y%m%d')}.db")


def sign(ts_ms, method, path):
    with open(KEY_PATH, "rb") as f:
        pk = load_pem_private_key(f.read(), password=None)
    sig = pk.sign(f"{ts_ms}{method}{path}".encode(),
                  padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                              salt_length=padding.PSS.DIGEST_LENGTH),
                  hashes.SHA256())
    return base64.b64encode(sig).decode()


def _c(dollar_str):
    """'0.2500' -> 25 (cents int)."""
    try:
        return int(round(float(dollar_str) * 100))
    except (TypeError, ValueError):
        return None


def _f(fp_str):
    try:
        return float(fp_str)
    except (TypeError, ValueError):
        return 0.0


def get_liquid_markets():
    from core.config import Config
    from core.kalshi_client import KalshiClient
    k = KalshiClient(Config()); k._init_http()
    out = []
    for ser in ("KXMLBGAME", "KXWNBAGAME", "KXNBAGAME"):
        d = k._get(f"/trade-api/v2/markets?status=open&series_ticker={ser}&limit=200")
        ms = [m for m in (d.get("markets", []) if d else [])
              if float(m.get("volume_fp", 0) or 0) > 0]
        ms.sort(key=lambda m: float(m.get("volume_fp", 0) or 0), reverse=True)
        out += [m["ticker"] for m in ms[: MAX_TICKERS // 2]]
    return out[:MAX_TICKERS]


# per-ticker book: {yes: {price_cents: size}, no: {price_cents: size}}
books = defaultdict(lambda: {"yes": {}, "no": {}})


def best_quote(tk):
    b = books[tk]
    yb = max(b["yes"]) if b["yes"] else None             # best YES bid (cents)
    nb = max(b["no"]) if b["no"] else None                # best NO bid (cents)
    ya = (100 - nb) if nb is not None else None           # YES ask = 100 - best NO bid
    yb_sz = b["yes"].get(yb) if yb is not None else None
    ya_sz = b["no"].get(nb) if nb is not None else None
    return yb, ya, yb_sz, ya_sz


def apply_snapshot(tk, msg):
    b = {"yes": {}, "no": {}}
    for side, key in (("yes", "yes_dollars_fp"), ("no", "no_dollars_fp")):
        for lvl in msg.get(key, []) or []:
            p, s = _c(lvl[0]), _f(lvl[1])
            if p is not None and s > 0:
                b[side][p] = s
    books[tk] = b


def apply_delta(tk, msg):
    side = msg.get("side"); p = _c(msg.get("price_dollars")); d = _f(msg.get("delta_fp"))
    if side not in ("yes", "no") or p is None:
        return
    lv = books[tk][side]
    lv[p] = lv.get(p, 0.0) + d
    if lv[p] <= 0:
        lv.pop(p, None)


def init_db(path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS ticks(
        recv_ms INTEGER, exch_ms INTEGER, ticker TEXT, mtype TEXT,
        yes_bid INTEGER, yes_ask INTEGER, yb_sz REAL, ya_sz REAL,
        trade_price INTEGER, taker_side TEXT, trade_count REAL, seq INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tk_time ON ticks(ticker, recv_ms)")
    c.commit()
    return c


async def main():
    if not (KEY_ID and os.path.exists(KEY_PATH)):
        print(f"missing auth: KEY_ID={bool(KEY_ID)} KEY_PATH={KEY_PATH}"); sys.exit(1)
    tickers = get_liquid_markets()
    if not tickers:
        print("no liquid markets found"); sys.exit(1)
    print(f"[ws-rec] recording {len(tickers)} markets -> {OUT} for {DUR}s; sample={tickers[:4]}")
    db = init_db(OUT)
    buf = []; seq = 0; nmsg = 0; ntrade = 0
    end_at = time.time() + DUR
    while time.time() < end_at:
        ts_ms = int(time.time() * 1000)
        headers = {"KALSHI-ACCESS-KEY": KEY_ID,
                   "KALSHI-ACCESS-SIGNATURE": sign(ts_ms, "GET", "/trade-api/ws/v2"),
                   "KALSHI-ACCESS-TIMESTAMP": str(ts_ms)}
        try:
            async with websockets.connect(WS_URL, additional_headers=headers, open_timeout=15) as ws:
                await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {
                    "channels": ["orderbook_delta", "trade", "ticker"],
                    "market_tickers": tickers}}))
                print("[ws-rec] subscribed; listening...")
                while time.time() < end_at:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(30.0, max(1, end_at - time.time())))
                    except asyncio.TimeoutError:
                        continue
                    recv = int(time.time() * 1000); seq += 1; nmsg += 1
                    m = json.loads(raw); mt = m.get("type", "?"); body = m.get("msg", {}) or {}
                    tk = body.get("market_ticker")
                    exch = body.get("ts_ms")
                    tp = tsd = tc = None
                    if mt == "orderbook_snapshot":
                        apply_snapshot(tk, body)
                    elif mt == "orderbook_delta":
                        apply_delta(tk, body)
                    elif mt == "trade":
                        tp = _c(body.get("yes_price_dollars")); tsd = body.get("taker_side"); tc = _f(body.get("count_fp")); ntrade += 1
                    if tk:
                        yb, ya, ybs, yas = best_quote(tk)
                        buf.append((recv, exch, tk, mt, yb, ya, ybs, yas, tp, tsd, tc, seq))
                    if len(buf) >= 500:
                        db.executemany("INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf); db.commit(); buf.clear()
        except Exception as e:
            print(f"[ws-rec] conn error: {e}; reconnect 5s")
            await asyncio.sleep(5)
    if buf:
        db.executemany("INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf); db.commit()
    print(f"[ws-rec] done: {nmsg} msgs ({ntrade} trades) -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
