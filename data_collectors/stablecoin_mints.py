"""C1 — Stablecoin mint/burn flow collector (5/26 design [[2026-05-26-c1-stablecoin-flow-collector.md]]).

Polls public ETH JSON-RPC for USDT + USDC `Transfer` events involving zero
address (mint = Transfer FROM 0x0; burn = Transfer TO 0x0). These are
issuer-controlled supply changes — Tether/Circle mint when CEXes deposit
fiat, burn when CEXes withdraw fiat.

Hypothesis (literature + practitioner-known):
  - Large net mint over rolling 4h window → bullish bias 6-24h
  - Large net burn → bearish bias 6-24h

This collector accumulates raw events. After 14d of forward data, run
backtest against Kalshi crypto market settlements to verify +EV signal
before building Phase 1 trader.

Cadence: every 10 min.

No API key required — uses Cloudflare's public ETH JSON-RPC endpoint with
fallback to LlamaRPC. Both have generous free tiers.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("stablecoin_mints_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

# Public ETH JSON-RPC endpoints (no key required). Try in order until one succeeds.
# Probed working 2026-05-26 from EC2 us-east-2: publicnode, merkle, 1rpc, blastapi.
# Cloudflare/Llama/Ankr now require auth or block AWS IPs.
RPC_ENDPOINTS = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.merkle.io",
    "https://eth-mainnet.public.blastapi.io",
    "https://1rpc.io/eth",
]

# ERC-20 Transfer event topic0
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS_TOPIC = "0x0000000000000000000000000000000000000000000000000000000000000000"

# Stablecoin contract addresses + decimals
STABLECOINS = {
    "USDT": {
        "contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "decimals": 6,
    },
    "USDC": {
        "contract": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "decimals": 6,
    },
}

# Threshold for "large" mint/burn (USD)
MIN_AMOUNT_USD = 1_000_000  # $1M filter — focus on significant moves

SCHEMA = """
CREATE TABLE IF NOT EXISTS stablecoin_mint_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    asset TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    block_ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount_token REAL NOT NULL,
    amount_usd REAL NOT NULL,
    counter_address TEXT,
    detected_at TEXT NOT NULL,
    UNIQUE(chain, tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_smo_detected ON stablecoin_mint_observations(detected_at);
CREATE INDEX IF NOT EXISTS idx_smo_direction_amount ON stablecoin_mint_observations(direction, amount_usd DESC);
CREATE INDEX IF NOT EXISTS idx_smo_asset_block ON stablecoin_mint_observations(asset, block_number DESC);
"""

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS stablecoin_collector_state (
    chain TEXT NOT NULL,
    asset TEXT NOT NULL,
    last_block INTEGER NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (chain, asset)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hex_to_int(s: str) -> int:
    if not s:
        return 0
    if isinstance(s, int):
        return s
    return int(s, 16)


def _rpc_call(method: str, params: list, timeout: float = 15.0) -> dict:
    """Try each RPC endpoint until one returns success."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    }).encode()
    last_err = None
    for url in RPC_ENDPOINTS:
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "kalshi-arb/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
            if "error" in result:
                last_err = f"{url}: {result['error']}"
                logger.warning(f"RPC error from {url}: {result['error']}")
                continue
            return result
        except Exception as e:
            last_err = f"{url}: {e}"
            logger.warning(f"RPC failure {url}: {e}")
            continue
    raise RuntimeError(f"All RPC endpoints failed; last: {last_err}")


def _get_latest_block() -> int:
    r = _rpc_call("eth_blockNumber", [])
    return _hex_to_int(r["result"])


def _get_block_timestamp(block_number: int) -> int:
    """Returns unix timestamp of given block."""
    r = _rpc_call("eth_getBlockByNumber", [hex(block_number), False])
    return _hex_to_int(r["result"]["timestamp"])


def _get_logs(contract: str, topic1: str | None, topic2: str | None,
              from_block: int, to_block: int) -> list:
    """eth_getLogs with optional topic1/topic2 filters."""
    topics: list = [TRANSFER_TOPIC]
    if topic1 is not None:
        topics.append(topic1)
    else:
        topics.append(None)
    if topic2 is not None:
        if len(topics) == 1:
            topics.append(None)
        topics.append(topic2)
    # Strip trailing None entries to keep query well-formed
    while topics and topics[-1] is None:
        topics.pop()

    filter_obj = {
        "address": contract,
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "topics": topics,
    }
    r = _rpc_call("eth_getLogs", [filter_obj], timeout=30.0)
    return r.get("result") or []


def _get_last_block(c: sqlite3.Connection, chain: str, asset: str) -> int:
    row = c.execute(
        "SELECT last_block FROM stablecoin_collector_state WHERE chain=? AND asset=?",
        (chain, asset),
    ).fetchone()
    return row[0] if row else 0


def _set_last_block(c: sqlite3.Connection, chain: str, asset: str, block: int):
    c.execute(
        """INSERT INTO stablecoin_collector_state(chain, asset, last_block, last_updated)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(chain, asset) DO UPDATE
           SET last_block = excluded.last_block, last_updated = excluded.last_updated""",
        (chain, asset, block, _now_iso()),
    )


def _parse_topic_to_address(topic: str) -> str:
    if not topic or len(topic) < 42:
        return ""
    return "0x" + topic[-40:].lower()


def _process_logs(c: sqlite3.Connection, chain: str, asset: str, logs: list,
                  decimals: int, direction: str) -> int:
    """Insert new mint/burn rows; resolve block timestamps. Returns insert count."""
    inserted = 0
    detected_at = _now_iso()
    factor = 10 ** decimals
    # Cache block timestamps to avoid duplicate RPC calls
    block_ts_cache: dict = {}

    for log in logs:
        try:
            tx_hash = log["transactionHash"]
            log_index = _hex_to_int(log["logIndex"])
            block_number = _hex_to_int(log["blockNumber"])
            value_raw = _hex_to_int(log["data"])
            amount_token = value_raw / factor
            amount_usd = amount_token

            if amount_usd < MIN_AMOUNT_USD:
                continue

            if block_number not in block_ts_cache:
                block_ts_cache[block_number] = _get_block_timestamp(block_number)
                time.sleep(0.1)  # gentle pacing
            block_ts_unix = block_ts_cache[block_number]
            block_ts = datetime.fromtimestamp(
                block_ts_unix, tz=timezone.utc
            ).isoformat(timespec="seconds")

            topics = log.get("topics", [])
            if direction == "mint":
                counter = _parse_topic_to_address(topics[2] if len(topics) > 2 else "")
            else:
                counter = _parse_topic_to_address(topics[1] if len(topics) > 1 else "")

            cur = c.execute(
                """INSERT OR IGNORE INTO stablecoin_mint_observations
                   (chain, asset, tx_hash, log_index, block_number, block_ts,
                    direction, amount_token, amount_usd, counter_address, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chain, asset, tx_hash, log_index, block_number, block_ts,
                 direction, amount_token, amount_usd, counter, detected_at),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.warning(f"Failed to parse log: {e}")
            continue
    return inserted


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    c = sqlite3.connect(DB, timeout=30.0)
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(SCHEMA + STATE_SCHEMA)

    try:
        latest_block = _get_latest_block()
    except Exception as e:
        logger.error(f"Failed to fetch latest block: {e}")
        c.close()
        return

    logger.info(f"Latest ETH block: {latest_block}")

    for asset, meta in STABLECOINS.items():
        try:
            last_block = _get_last_block(c, "eth", asset)
            if last_block == 0:
                # First run: start from ~1h ago (~300 blocks at 12s/block)
                last_block = latest_block - 300
                logger.info(f"[{asset}] first-run init, starting from block {last_block}")

            from_block = last_block + 1
            to_block = latest_block

            # Cap to 5000 blocks per run (~17h at 12s/block) to avoid timeouts
            if to_block - from_block > 5000:
                to_block = from_block + 5000
                logger.warning(f"[{asset}] block range capped to {from_block}-{to_block}")

            if from_block > to_block:
                logger.info(f"[{asset}] no new blocks (last={last_block}, latest={latest_block})")
                continue

            # Mints: Transfer with topic1 = zero address
            mint_logs = _get_logs(
                contract=meta["contract"],
                topic1=ZERO_ADDRESS_TOPIC,
                topic2=None,
                from_block=from_block,
                to_block=to_block,
            )
            time.sleep(0.5)

            # Burns: Transfer with topic2 = zero address
            burn_logs = _get_logs(
                contract=meta["contract"],
                topic1=None,
                topic2=ZERO_ADDRESS_TOPIC,
                from_block=from_block,
                to_block=to_block,
            )
            time.sleep(0.5)

            n_mint = _process_logs(c, "eth", asset, mint_logs, meta["decimals"], "mint")
            n_burn = _process_logs(c, "eth", asset, burn_logs, meta["decimals"], "burn")

            _set_last_block(c, "eth", asset, to_block)
            c.commit()

            logger.info(
                f"[{asset}] blocks {from_block}-{to_block}: "
                f"{len(mint_logs)} mint logs ({n_mint} >= ${MIN_AMOUNT_USD/1e6:.0f}M), "
                f"{len(burn_logs)} burn logs ({n_burn} >= ${MIN_AMOUNT_USD/1e6:.0f}M)"
            )
        except Exception as e:
            logger.error(f"[{asset}] processing error: {e}", exc_info=True)
            continue

    c.close()


if __name__ == "__main__":
    main()
