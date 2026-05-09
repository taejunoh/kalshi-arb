"""Ladder Fed — KXFEDDECISION bucketed mutually-exclusive outcome ladder.

Each FOMC meeting becomes one bucketed group: cuts (C25, C50, ...), holds
(H0), hikes (H25, ...). sum_yes ≈ 1 invariant. 8 meetings/yr → mostly
idle; primary fire window is the 2 weeks pre-FOMC reprice.

Conflict with C1 (FOMC window sub-project, separate spec): if both
strategies fire on the same ticker, ladder_fed has dedup priority via
the standard DB has_existing_trade gate (extended to ladder_* in Task 7).
"""
import logging
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_fed")

TARGET_SERIES = frozenset({"KXFEDDECISION"})


class LadderFedStrategy:
    STRATEGY_NAME = "ladder_fed"
    LADDER_TYPE = "bucketed"
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
        head, tail = suffix[0], suffix[1:]
        if head not in ("C", "H"):
            return None
        try:
            mag = float(tail)
        except ValueError:
            return None
        return -mag if head == "C" else mag

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        parts = m.get("ticker", "").split("-")
        return parts[1] if len(parts) >= 3 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        # get_all_markets() = broad strategy pool from STRATEGY_SERIES.
        # get_unlocked_markets() is crypto-only and would return [].
        markets = [m for m in self._scanner.get_all_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

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
