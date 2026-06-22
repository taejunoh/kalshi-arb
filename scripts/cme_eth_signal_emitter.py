#!/usr/bin/env python3
"""Lightweight standalone KXETH-only cme_basis signal emitter (2026-05-31).

Re-emits `cme_basis_fade` observer signals for KXETH ONLY, decoupled from the
frozen main crypto observer, so the `cme_basis_follow_v1` paper twin has fresh
signals to act on WITHOUT reversing the full 2026-05-27 crypto freeze
([[crypto_full_freeze_2026-05-27]], [[cme_basis_follow_paper_twin_2026-05-31]]).

Faithful: reuses `_build_leg` + `CmeBasisFadeObserver.evaluate()` from
strategies.crypto_observer — IDENTICAL gates, so emitted signals match what the
full observer would have produced for KXETH. Lightweight: ETH-only, REST spot
(no Binance WS feed), run on a low-frequency systemd timer (not the in-bot 1 Hz
loop). Per-market cooldown via DB so a 5-min timer doesn't spam duplicates.

Settlement backfill: the existing crypto_observer_settlement_backfill cron fills
actual_outcome for ANY row in crypto_observer_signals by ticker, so these rows
settle the same way the observer's did.

  python scripts/cme_eth_signal_emitter.py --dry     # print, no DB write
  python scripts/cme_eth_signal_emitter.py           # emit (insert) signals
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.kalshi_client import KalshiClient
from core.database import Database
from core.market_extras import get_cme_basis
from strategies.crypto_observer import (
    _build_leg, _event_key, _close_time_secs, EventContext, CmeBasisFadeObserver,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cme_eth_emitter")

# KXETH = bucketed B-leg series (the +EV cohort). "KXETH-" prefix excludes the
# KXETHD daily series automatically.
SERIES = "KXETH"
ASSET = "ETH"
OBSERVER = "cme_basis_fade"
# Per-market re-emit cooldown — at a ~5min timer cadence, skip markets that
# already got a signal recently so the table / settlement backfill stay clean.
COOLDOWN_S = 270.0


def fetch_eth_spot():
    """ETH spot via public REST (Coinbase primary, Kraken fallback). A few
    seconds / a few dollars of staleness is immaterial vs ~$10-20 bucket widths
    for the strike>=spot comparison the cme_basis gate uses."""
    sources = [
        ("coinbase", "https://api.coinbase.com/v2/prices/ETH-USD/spot",
         lambda d: float(d["data"]["amount"])),
        ("kraken", "https://api.kraken.com/0/public/Ticker?pair=ETHUSD",
         lambda d: float(list(d["result"].values())[0]["c"][0])),
    ]
    for name, url, pick in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return pick(json.loads(r.read().decode()))
        except Exception as e:
            logger.warning(f"spot fetch failed ({name}): {e}")
    return None


async def _recent_signal_exists(db, market_id: str, within_s: float) -> bool:
    import aiosqlite
    cutoff = datetime.now(timezone.utc).timestamp() - within_s
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        cur = await conn.execute(
            "SELECT ts FROM crypto_observer_signals "
            "WHERE observer = ? AND market_id = ? ORDER BY ts DESC LIMIT 1",
            (OBSERVER, market_id),
        )
        row = await cur.fetchone()
    if not row or not row[0]:
        return False
    try:
        last = datetime.fromisoformat(row[0])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last.timestamp() >= cutoff
    except Exception:
        return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="print signals, no DB write")
    args = ap.parse_args()

    cfg = Config()
    kc = KalshiClient(cfg)
    db = Database()
    obs = CmeBasisFadeObserver()

    spot = fetch_eth_spot()
    if spot is None or spot <= 0:
        logger.error("no ETH spot available — abort")
        return
    cme_basis = get_cme_basis(ASSET)
    if cme_basis is None:
        logger.error("no cme_basis (yfinance) available — abort")
        return

    try:
        markets = [m for m in kc.get_active_markets()
                   if str(m.get("ticker", "")).startswith(SERIES + "-")]
    except Exception as e:
        logger.error(f"get_active_markets failed: {e}")
        return

    now = datetime.now(timezone.utc)
    by_event: dict = {}
    legs_built = 0
    for m in markets:
        leg = _build_leg(m)
        if leg is None or leg.kind != "bucketed":
            continue
        ev = _event_key(str(m.get("ticker", "")))
        if not ev:
            continue
        by_event.setdefault(ev, []).append(leg)
        legs_built += 1

    candidates = 0
    emitted = 0
    skipped_cooldown = 0
    for ev, legs in by_event.items():
        b_legs = sorted(legs, key=lambda l: l.threshold)
        close_s = _close_time_secs(b_legs[0].raw, now)
        ctx = EventContext(
            series=SERIES, asset=ASSET, event_key=ev, now=now,
            spot=spot, spot_60s_ago=None, deribit_iv=None,
            b_legs=b_legs, t_legs=[], open_time_secs=None,
            close_time_secs=close_s, cme_basis=cme_basis,
        )
        for sig in obs.evaluate(ctx):
            candidates += 1
            if not args.dry and await _recent_signal_exists(db, sig.market_id, COOLDOWN_S):
                skipped_cooldown += 1
                continue
            line = (f"EMIT {sig.market_id} pred={sig.predicted_outcome} "
                    f"ya={sig.features.get('ya')} na={sig.features.get('no_ask')} "
                    f"strike={sig.features.get('strike')} spot={spot:.2f} "
                    f"basis={cme_basis:+.5f} mts_s={sig.features.get('mts_s')}")
            if args.dry:
                print("DRY " + line)
                emitted += 1
            else:
                try:
                    await db.record_crypto_observer_signal(
                        observer=sig.observer, series=sig.series,
                        event_key=sig.event_key, market_id=sig.market_id,
                        predicted_outcome=sig.predicted_outcome,
                        features_json=json.dumps(sig.features),
                    )
                    logger.info(line)
                    emitted += 1
                except Exception as e:
                    logger.error(f"insert failed {sig.market_id}: {e}")

    logger.info(
        f"spot={spot:.2f} cme_basis={cme_basis:+.5f} kxeth_markets={len(markets)} "
        f"legs={legs_built} events={len(by_event)} candidates={candidates} "
        f"emitted={emitted} skipped_cooldown={skipped_cooldown} dry={args.dry}"
    )


if __name__ == "__main__":
    asyncio.run(main())
