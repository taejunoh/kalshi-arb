"""Ladder Shutdown — KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH cumulative ladders.

Smallest universe (4 + variable open) — designed primarily as the V1
shutdown_fade replacement that respects the DB dedup invariant. V1 had
121 paper_filled rows on KXNUMSHUTDOWNS-T2/T3 by skipping the dedup
guard; that guard now extends to ladder_* (see core/order_manager.py
comment block).
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_shutdown")

TARGET_SERIES = frozenset({"KXNUMSHUTDOWNS", "KXGOVTSHUTLENGTH"})


class LadderShutdownStrategy:
    STRATEGY_NAME = "ladder_shutdown"
    LADDER_TYPE = "cumulative"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if len(suffix) < 2:
            return None
        if suffix[0] not in ("T", "G"):
            return None
        try:
            return float(suffix[1:])
        except ValueError:
            return None

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        parts = m.get("ticker", "").split("-")
        # KXNUMSHUTDOWNS-27JAN01-T2 → 27JAN01
        return parts[1] if len(parts) >= 3 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        ticker = m.get("ticker", "")
        series = ticker.split("-")[0]
        return series in TARGET_SERIES

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        # Pull open universe — caller-side filter
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        # Enrich with depth (caller responsibility — sub-strategy fills in)
        enriched = []
        for m in markets:
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

        groups = group_legs_into_ladders(
            enriched, suffix_parser=self._parse_suffix,
            ladder_type=self.LADDER_TYPE,
            event_key_extractor=self._extract_event_key,
            direction=self.DIRECTION,
        )
        for g in groups:
            await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        arb_trades = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
        )
        if arb_trades:
            await self._fire_arb(arb_trades[0])
            return  # one fire per group per scan
        fade_trades = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
        )
        if fade_trades:
            await self._fire_fade(fade_trades[0])

    async def _fire_arb(self, trade):
        logger.info(
            f"[LADDER_SHUTDOWN] ARB {trade.group_key} "
            f"edge=${trade.expected_edge_usdc:.2f}"
        )
        for leg in trade.legs:
            await self._order_manager.market_order(
                ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                price=leg.price, strategy=self.STRATEGY_NAME,
                market_id=leg.ticker,
            )

    async def _fire_fade(self, trade):
        logger.info(
            f"[LADDER_SHUTDOWN] FADE {trade.ticker} {trade.side} "
            f"inv={trade.inversion_amount:.4f}"
        )
        await self._order_manager.market_order(
            ticker=trade.ticker, side=trade.side, size_usdc=trade.size_usdc,
            price=trade.price, strategy=self.STRATEGY_NAME,
            market_id=trade.ticker,
        )
