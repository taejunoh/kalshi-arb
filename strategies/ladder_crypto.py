"""Ladder Crypto — KXBTC, KXETH cumulative price-bucket ladders.

KXBTC has $100 grid, KXETH has $40 grid → typically ~50 legs per close
event. ATM ±N filter narrows to the active leg cluster (far OTM/ITM
have wide spreads + thin depth). Spot anchor (Coinbase WS) is deferred
to follow-up — Open question 4 in spec.
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_crypto")

TARGET_SERIES = frozenset({"KXBTC", "KXETH"})
BUCKET_SIZE_BY_SERIES = {"KXBTC": 100.0, "KXETH": 40.0}
ATM_WINDOW_N = 2  # keep ATM ± N buckets only


class LadderCryptoStrategy:
    STRATEGY_NAME = "ladder_crypto"
    LADDER_TYPE = "cumulative"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if not suffix.startswith("B") or len(suffix) < 2:
            return None
        try:
            return float(suffix[1:])
        except ValueError:
            return None

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        # KXBTC-26MAY09-1500-B70250 → 26MAY09-1500
        parts = m.get("ticker", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 4 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _atm_proxy(legs: list[dict]) -> Optional[float]:
        """Use the leg whose yes_ask is closest to 0.50 as ATM proxy.

        This avoids needing a live Coinbase feed for the v0 cut. Spot
        anchor (real Coinbase WS) is a follow-up.
        """
        best = None
        best_dist = 1.0
        for m in legs:
            ya = float(m.get("yes_ask") or 0.0)
            d = abs(ya - 0.50)
            if d < best_dist:
                best_dist = d
                # Threshold from suffix
                suf = m["ticker"].rsplit("-", 1)[-1]
                try:
                    best = float(suf[1:])
                except ValueError:
                    continue
        return best

    @staticmethod
    def _atm_window_filter(legs: list[dict], atm_price: float, window_n: int,
                           bucket_size: float) -> list[dict]:
        lo = atm_price - window_n * bucket_size
        hi = atm_price + window_n * bucket_size
        out = []
        for m in legs:
            suf = m["ticker"].rsplit("-", 1)[-1]
            try:
                thr = float(suf[1:])
            except ValueError:
                continue
            if lo <= thr <= hi:
                out.append(m)
        return out

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        # Partition by series (each has its own bucket size)
        by_series: dict[str, list[dict]] = {}
        for m in markets:
            series = m["ticker"].split("-")[0]
            by_series.setdefault(series, []).append(m)

        for series, ms in by_series.items():
            enriched = []
            for m in ms:
                yes_price, no_price = self._client.get_market_prices(m["ticker"])
                if yes_price is None or no_price is None:
                    continue
                yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
                enriched.append({
                    **m,
                    "yes_ask": yes_price, "yes_bid": yes_price,
                    "no_ask": no_price, "no_bid": no_price,
                    "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
                })

            atm = self._atm_proxy(enriched)
            if atm is None:
                continue
            bucket_size = BUCKET_SIZE_BY_SERIES.get(series, 100.0)
            atm_legs = self._atm_window_filter(enriched, atm, ATM_WINDOW_N, bucket_size)
            if not atm_legs:
                continue

            groups = group_legs_into_ladders(
                atm_legs, suffix_parser=self._parse_suffix,
                ladder_type=self.LADDER_TYPE,
                event_key_extractor=self._extract_event_key,
                direction=self.DIRECTION,
            )
            for g in groups:
                await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb:
            for leg in arb[0].legs:
                await self._order_manager.market_order(
                    ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                    price=leg.price, strategy=self.STRATEGY_NAME, market_id=leg.ticker,
                )
            return
        fade = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade:
            t = fade[0]
            await self._order_manager.market_order(
                ticker=t.ticker, side=t.side, size_usdc=t.size_usdc,
                price=t.price, strategy=self.STRATEGY_NAME, market_id=t.ticker,
            )
