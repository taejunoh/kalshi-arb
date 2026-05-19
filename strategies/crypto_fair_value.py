"""Crypto Fair-Value Trader (B2) — event-driven 1-sec fast-scan trader.

Targets KXBTC + KXETH + KXXRP + KXBTCD + KXETHD (binary "asset >= K at T"
markets). Compares Kalshi implied prob to lognormal model fair prob using
external spot (PriceFeed via Binance WS) + Deribit IV (BTC/ETH) or
realized vol (XRP fallback). Fires single edge-scaled directional trade
gated by 9 risk gates including 60-sec recently-fired guard and
cross-strategy exposure cap.

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from core.fair_value_math import lognormal_cdf_above

logger = logging.getLogger("crypto_fair_value")

# Series → asset mapping (handles regular + daily variants)
SERIES_TO_ASSET = {
    "KXBTC": "BTC", "KXBTCD": "BTC",
    "KXETH": "ETH", "KXETHD": "ETH",
    "KXXRP": "XRP",
}
TARGET_SERIES = frozenset(SERIES_TO_ASSET.keys())

# Suffix: T<float> (current Kalshi format)
SUFFIX_RE = re.compile(r"^T(\d+(?:\.\d+)?)$")


class CryptoFairValueStrategy:
    STRATEGY_NAME = "crypto_fair_value"

    def __init__(
        self, config, order_manager, market_scanner, kalshi_client,
        *, price_feed,
    ):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._price_feed = price_feed
        self._recently_fired: dict[str, float] = {}  # ticker → epoch ts

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        series = m.get("ticker", "").split("-")[0]
        return series in TARGET_SERIES

    @staticmethod
    def _extract_strike(m: dict) -> Optional[float]:
        suffix = m.get("ticker", "").rsplit("-", 1)[-1]
        match = SUFFIX_RE.match(suffix)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _extract_asset(m: dict) -> Optional[str]:
        series = m.get("ticker", "").split("-")[0]
        return SERIES_TO_ASSET.get(series)

    @staticmethod
    def _filter_atm(markets: list[dict], *, spot: float, asset: str, n: int) -> list[dict]:
        """Keep N strikes immediately above and N immediately below spot.

        Sort all target markets by |strike - spot| and take the top 2N+1
        (one ATM + N above + N below).
        """
        # Filter to target asset only
        candidates = [
            (m, CryptoFairValueStrategy._extract_strike(m))
            for m in markets
            if CryptoFairValueStrategy._extract_asset(m) == asset
        ]
        candidates = [(m, s) for (m, s) in candidates if s is not None]
        # Sort by distance to spot, take 2n+1 closest
        candidates.sort(key=lambda t: abs(t[1] - spot))
        return [m for (m, _s) in candidates[: 2 * n + 1]]

    @staticmethod
    def _time_to_close_secs(m: dict, *, now: Optional[datetime] = None) -> float:
        if now is None:
            now = datetime.now(timezone.utc)
        close_iso = m.get("close_time", "")
        if not close_iso:
            return -1.0
        try:
            close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except Exception:
            return -1.0
        return (close_dt - now).total_seconds()

    async def _get_vol(self, asset: str, *, db) -> Optional[tuple[float, str]]:
        """Returns (vol, source) or None.

        Source priority:
          1. Deribit ATM IV (BTC/ETH only, fresh ≤ 6h)
          2. PriceFeed.get_volatility (XRP, or Deribit stale/missing)
        """
        DERIBIT_ASSETS = ("BTC", "ETH")
        DERIBIT_TTL_HOURS = 6.0
        if asset in DERIBIT_ASSETS:
            iv_row = await db.fetch_nearest_crypto_iv(asset)
            if iv_row:
                try:
                    fetched = datetime.fromisoformat(
                        iv_row["fetched_at"].replace("Z", "+00:00")
                    )
                    age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
                    if age_h <= DERIBIT_TTL_HOURS:
                        return float(iv_row["atm_iv"]), "deribit"
                except Exception:
                    pass
        rv = self._price_feed.get_volatility(asset)
        if rv:
            return float(rv), "realized"
        return None

    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        fair_prob: float,
    ) -> Optional[dict]:
        """Returns {'side', 'deviation', 'entry_price', 'ev_per_contract'} or None.

        Mirrors C1 macro_event._compute_edge math (same fee + cushion).
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = fair_prob - kalshi_yes_mid
        if abs(deviation) < cfg.CRYPTO_FAIR_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            ev = fair_prob - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            ev = (1.0 - fair_prob) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        cfg = self.config
        base = cfg.CRYPTO_FAIR_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.CRYPTO_FAIR_MIN_DEVIATION))
        return min(base * scale, cfg.CRYPTO_FAIR_MAX_POSITION_PER_TICKER)

    async def _handle_market(self, market: dict, *, db, cache, now: datetime):
        """9-gate pipeline for a single market — fires market_order if all gates pass."""
        cfg = self.config
        ticker = market.get("ticker", "")
        asset = self._extract_asset(market)
        if not asset:
            return

        # Gate 1: spot freshness
        if self._price_feed.is_stale(asset):
            return
        spot = self._price_feed.get_price(asset)
        if spot is None or spot <= 0:
            return

        # Gate 2: vol availability
        vol_pair = await self._get_vol(asset, db=db)
        if vol_pair is None:
            return
        vol, vol_source = vol_pair

        # Gate 3: window (close > MIN_TIME_TO_CLOSE_SECS)
        secs_to_close = self._time_to_close_secs(market, now=now)
        if secs_to_close < cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS:
            return
        tau_years = secs_to_close / (365.25 * 86400)

        # Compute fair prob
        strike = self._extract_strike(market)
        if strike is None:
            return
        fair = lognormal_cdf_above(spot=spot, strike=strike, vol=vol, tau_years=tau_years)
        if fair is None:
            return

        # Gate 4: depth (need cached orderbook)
        ob = cache.get(ticker)
        if ob is None:
            return
        # Compute edge
        edge = self._compute_edge(
            kalshi_yes_ask=ob.yes_ask, kalshi_yes_bid=ob.yes_bid,
            kalshi_no_ask=ob.no_ask, kalshi_no_bid=ob.no_bid,
            fair_prob=fair,
        )
        # Gates 5+6: deviation + EV (handled by _compute_edge)
        if edge is None:
            return
        depth_for_side = ob.yes_depth if edge["side"] == "yes" else ob.no_depth
        if depth_for_side < cfg.CRYPTO_FAIR_MIN_DEPTH_USDC:
            return

        # Gate 7: recently_fired (over-fire guard)
        last_fire = self._recently_fired.get(ticker)
        if last_fire is not None and (time.time() - last_fire) < cfg.CRYPTO_FAIR_RECENTLY_FIRED_SECS:
            return

        # Gate 8: cross-strategy exposure cap
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE:
            return

        # Gate 9: per-strategy DB dedup is enforced by OrderManager

        logger.info(
            f"[CRYPTO_FAIR] FIRE {ticker} {edge['side']} size=${size:.2f} "
            f"spot={spot:.2f} strike={strike:.2f} fair={fair:.4f} "
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
        """Main fast-scan loop — runs as separate asyncio task in main.py.

        cache: OrderbookCache instance (its refresh_loop should be running
        in parallel — caller's responsibility to schedule both tasks).
        """
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db — crypto_fair_value scan disabled")
            return
        logger.info("crypto_fair_value scan_loop started")
        _diag_iter = 0
        _diag_last_log = time.time()
        while True:
            try:
                # Re-check circuit breaker EVERY iteration (kill switch + daily
                # loss cap + consecutive loss cap must apply to fast-scan too).
                # macro_event re-checks per scan_and_execute call; we mirror it.
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

                # First pass: compute ATM ±N universe so refresh_loop can warm
                # the cache before _handle_market needs the books. Cold-start
                # fix from final-review I2.
                target_tickers: list[str] = []
                asset_markets: list[tuple[dict, str]] = []
                _diag_spots: dict[str, Optional[float]] = {}
                _diag_per_asset: dict[str, int] = {}
                for asset in ("BTC", "ETH", "XRP"):
                    spot = self._price_feed.get_price(asset)
                    _diag_spots[asset] = spot
                    if spot is None:
                        _diag_per_asset[asset] = -1
                        continue
                    atm_markets = self._filter_atm(
                        all_markets, spot=spot, asset=asset,
                        n=cfg.CRYPTO_FAIR_ATM_WINDOW_N,
                    )
                    _diag_per_asset[asset] = len(atm_markets)
                    for m in atm_markets:
                        target_tickers.append(m.get("ticker", ""))
                        asset_markets.append((m, asset))
                cache.set_target_tickers(target_tickers)

                _diag_iter += 1
                if time.time() - _diag_last_log >= 30.0:
                    cache_filled = sum(
                        1 for t in target_tickers if cache.get(t) is not None
                    )
                    logger.info(
                        f"crypto_fair_value diag: iter={_diag_iter} "
                        f"all_markets={len(all_markets)} "
                        f"atm_targets={len(target_tickers)} "
                        f"cache_hit={cache_filled} "
                        f"spots={_diag_spots} per_asset={_diag_per_asset}"
                    )
                    _diag_last_log = time.time()

                # Second pass: per-market 9-gate evaluation
                for m, _asset in asset_markets:
                    await self._handle_market(m, db=db, cache=cache, now=now)

                # Periodic eviction of stale recently_fired entries (>24h old).
                # Final-review I4 fix: prevents unbounded growth over weeks of
                # uptime as tickers settle and never fire again.
                cutoff = time.time() - 86400.0
                stale = [t for t, ts in self._recently_fired.items() if ts < cutoff]
                for t in stale:
                    del self._recently_fired[t]
            except Exception as e:
                logger.exception(f"crypto_fair_value scan error: {e}")
            await asyncio.sleep(scan_interval_secs)
