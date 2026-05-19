"""Ladder Approval — KXTRUMPAPPROVAL*, KX538APPROVE* cumulative ladders.

Includes both asc ("≥X%" — KXTRUMPAPPROVALYEAR, KX538APPROVE*) and desc
("≤X%" — KXTRUMPAPPROVALBELOW). Sub-strategy partitions universe by series
prefix and runs framework once per direction. Approval prices move slowly
→ ladder violations are typically structural and persist long enough for
the fade fallback to converge.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_approval")

ASC_SERIES = frozenset({
    "KXTRUMPAPPROVALYEAR", "KX538APPROVE", "KX538APPROVEMAX",
    "KX538APPROVEMIN", "KX538APPROVEY",
})
DESC_SERIES = frozenset({"KXTRUMPAPPROVALBELOW"})
TARGET_SERIES = ASC_SERIES | DESC_SERIES


class LadderApprovalStrategy:
    STRATEGY_NAME = "ladder_approval"
    LADDER_TYPE = "cumulative"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        if not suffix.isdigit():
            return None
        return float(suffix)

    @staticmethod
    def _direction_for(series: str) -> str:
        if series in DESC_SERIES:
            return "desc"
        return "asc"

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        # KXTRUMPAPPROVALYEAR-26-47 → "26"
        # KX538APPROVE-26W19-47 → "26W19"
        # KXTRUMPAPPROVALBELOW-26MAY-37 → "26MAY"
        parts = m.get("ticker", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 3 else ""

    @staticmethod
    def _enrich_markets_sync(markets: list[dict], client) -> list[dict]:
        # Dispatched via asyncio.to_thread — mirrors ladder_fed pattern.
        out = []
        for m in markets:
            yp, np_ = client.get_market_prices(m["ticker"])
            if yp is None or np_ is None:
                continue
            yd, nd = client.get_order_book_depth(m["ticker"])
            out.append({
                **m,
                "yes_ask": yp, "yes_bid": yp,
                "no_ask": np_, "no_bid": np_,
                "yes_depth": yd or 0.0, "no_depth": nd or 0.0,
            })
        return out

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        # get_all_markets() = broad strategy pool from STRATEGY_SERIES.
        # get_unlocked_markets() is crypto-only and would return [].
        markets = [m for m in self._scanner.get_all_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        enriched = await asyncio.to_thread(self._enrich_markets_sync, markets, self._client)

        # Partition by direction (asc vs desc series)
        by_dir = defaultdict(list)
        for m in enriched:
            series = m["ticker"].split("-")[0]
            by_dir[self._direction_for(series)].append(m)

        cfg = self.config
        for direction, ms in by_dir.items():
            groups = group_legs_into_ladders(
                ms, suffix_parser=self._parse_suffix,
                ladder_type=self.LADDER_TYPE,
                event_key_extractor=self._extract_event_key,
                direction=direction,
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
