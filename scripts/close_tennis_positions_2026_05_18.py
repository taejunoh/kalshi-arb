"""One-shot: close all open KXATPMATCH/KXWTAMATCH positions opened by the
2026-05-18 tennis multi-tournament regression.

Strategy: for each position, place a sell limit order at the current best
bid on the held side. Limit (not market) so we know the worst-case fill
price up front; bids near top-of-book typically fill instantly.
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Config
from core.kalshi_client import KalshiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("close_tennis_positions")


def _best_bid_cents(book_side: list) -> int:
    """Top-of-book bid in cents from a yes_dollars/no_dollars list.
    Each entry is [price_str, size_str]. Best bid = highest price."""
    best = 0.0
    for px_s, _sz in book_side or []:
        try:
            px = float(px_s)
        except (TypeError, ValueError):
            continue
        if px > best:
            best = px
    return int(round(best * 100))


def main():
    cfg = Config()
    k = KalshiClient(cfg)

    positions = k.get_positions()
    tennis = [
        p for p in positions
        if "KXATPMATCH" in p.get("ticker", "") or "KXWTAMATCH" in p.get("ticker", "")
        if float(p.get("position_fp", 0)) != 0
    ]
    if not tennis:
        logger.info("No open tennis positions.")
        return

    logger.info(f"Found {len(tennis)} tennis positions to close.")
    for p in tennis:
        ticker = p["ticker"]
        pos = float(p["position_fp"])
        side = "yes" if pos > 0 else "no"
        count = int(abs(pos))

        data = k._get(f"/trade-api/v2/markets/{ticker}/orderbook")
        book = data.get("orderbook_fp", {}) if data else {}
        if side == "yes":
            bid_cents = _best_bid_cents(book.get("yes_dollars", []))
        else:
            bid_cents = _best_bid_cents(book.get("no_dollars", []))

        if bid_cents <= 0:
            logger.warning(f"{ticker}: no {side}-side bid, leaving open")
            continue

        logger.info(
            f"{ticker}: closing {count}x {side} via limit sell @ {bid_cents}¢"
        )
        try:
            payload = {
                "ticker": ticker,
                "action": "sell",
                "side": side,
                "count": count,
                "type": "limit",
                "yes_price": bid_cents if side == "yes" else 100 - bid_cents,
            }
            resp = k._post("/trade-api/v2/portfolio/orders", payload)
            order = (resp or {}).get("order", {})
            logger.info(f"  → order_id={order.get('order_id')} status={order.get('status')}")
        except Exception as e:
            logger.error(f"  ✗ failed: {e}")
        # small spacing so we don't burst the API rate limit
        time.sleep(0.5)


if __name__ == "__main__":
    main()
