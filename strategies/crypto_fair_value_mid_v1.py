"""Crypto Fair-Value Mid-Price Variant (paper-only A/B vs crypto_fair_value).

Parallel variant of crypto_fair_value that uses binance.us @bookTicker mid
((bid+ask)/2) as the spot input instead of @kline_1m close (last trade).

Hypothesis: last-trade can lag by seconds-to-minutes on low-volume pairs
(ETH/SOL/XRP often go 10+ s between trades on binance.us), so the lognormal
fair value computed against last-trade is stale relative to where the market
is actually trading. Mid is always live because it updates on every L1 book
change.

Vol source unchanged — still pulled from PriceFeed.get_volatility (kline
deque) or Deribit IV. Only spot differs between this variant and the base
strategy. Paper-only at launch; promotion to live after ≥1 week of paper
fires + WR/ROI comparison vs crypto_fair_value on shared market universe.

Spec parent: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from strategies.crypto_fair_value import (
    CryptoFairValueStrategy,
    SERIES_TO_ASSET,
)

logger = logging.getLogger("crypto_fair_value_mid_v1")


class CryptoFairValueMidV1Strategy(CryptoFairValueStrategy):
    STRATEGY_NAME = "crypto_fair_value_mid_v1"

    async def _handle_market(self, market: dict, *, db, cache, now: datetime):
        """Identical 9-gate pipeline as parent, except spot/staleness reads
        from bookTicker mid instead of kline-derived last-trade."""
        cfg = self.config
        ticker = market.get("ticker", "")
        asset = self._extract_asset(market)
        if not asset:
            return

        if self._price_feed.is_mid_stale(asset):
            return
        spot = self._price_feed.get_mid(asset)
        if spot is None or spot <= 0:
            return

        vol_pair = await self._get_vol(asset, db=db)
        if vol_pair is None:
            return
        vol, vol_source = vol_pair

        secs_to_close = self._time_to_close_secs(market, now=now)
        if secs_to_close < cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS:
            return
        tau_years = secs_to_close / (365.25 * 86400)

        strike = self._extract_strike(market)
        if strike is None:
            return
        from core.fair_value_math import lognormal_cdf_above
        fair = lognormal_cdf_above(spot=spot, strike=strike, vol=vol, tau_years=tau_years)
        if fair is None:
            return

        ob = cache.get(ticker)
        if ob is None:
            return
        edge = self._compute_edge(
            kalshi_yes_ask=ob.yes_ask, kalshi_yes_bid=ob.yes_bid,
            kalshi_no_ask=ob.no_ask, kalshi_no_bid=ob.no_bid,
            fair_prob=fair,
        )
        if edge is None:
            return

        size_proposed = self._size_for_edge(edge["deviation"])
        depth_for_side = ob.yes_depth if edge["side"] == "yes" else ob.no_depth
        await self._maybe_log_phase0_signal(
            db=db, ticker=ticker, market=market, side=edge["side"],
            spot=spot, strike=strike, fair=fair, impl=(ob.yes_ask + ob.yes_bid) / 2,
            deviation=edge["deviation"], vol=vol, vol_source=vol_source,
            size_proposed=size_proposed, depth_for_side=depth_for_side,
        )

        if depth_for_side < cfg.CRYPTO_FAIR_MIN_DEPTH_USDC:
            return

        last_fire = self._recently_fired.get(ticker)
        if last_fire is not None and (time.time() - last_fire) < cfg.CRYPTO_FAIR_RECENTLY_FIRED_SECS:
            return

        size = size_proposed
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE:
            return

        logger.info(
            f"[CRYPTO_FAIR_MID] FIRE {ticker} {edge['side']} size=${size:.2f} "
            f"spot_mid={spot:.2f} strike={strike:.2f} fair={fair:.4f} "
            f"impl={(ob.yes_ask + ob.yes_bid)/2:.4f} dev={edge['deviation']:+.4f} "
            f"vol={vol:.3f}({vol_source})"
        )
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
        self._recently_fired[ticker] = time.time()

    async def scan_loop(self, *, cache, scan_interval_secs: float = 1.0):
        """Same control flow as parent but uses get_mid for ATM windowing."""
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db — crypto_fair_value_mid_v1 scan disabled")
            return
        while True:
            try:
                if self._order_manager.is_circuit_breaker_active(
                    strategy=self.STRATEGY_NAME
                ):
                    await asyncio.sleep(scan_interval_secs)
                    continue

                now = datetime.now(timezone.utc)
                cfg = self.config
                all_markets = [
                    m for m in self._scanner.get_active_markets()
                    if self._is_target_market(m)
                ]

                target_tickers: list[str] = []
                asset_markets: list[tuple[dict, str]] = []
                for asset in ("BTC", "ETH", "XRP"):
                    spot = self._price_feed.get_mid(asset)
                    if spot is None:
                        continue
                    atm_markets = self._filter_atm(
                        all_markets, spot=spot, asset=asset,
                        n=cfg.CRYPTO_FAIR_ATM_WINDOW_N,
                    )
                    for m in atm_markets:
                        target_tickers.append(m.get("ticker", ""))
                        asset_markets.append((m, asset))
                cache.set_target_tickers(target_tickers)

                for m, _asset in asset_markets:
                    await self._handle_market(m, db=db, cache=cache, now=now)

                cutoff = time.time() - 86400.0
                stale = [t for t, ts in self._recently_fired.items() if ts < cutoff]
                for t in stale:
                    del self._recently_fired[t]
            except Exception as e:
                logger.exception(f"crypto_fair_value_mid_v1 scan error: {e}")
            await asyncio.sleep(scan_interval_secs)
