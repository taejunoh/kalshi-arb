"""Ladder Crypto — KXBTC, KXETH price-bucket ladders.

A KXBTC/KXETH event publishes two market types side-by-side under the
same event key:

- `T<float>` cumulative markets — "$X or above" / "$X or below". Sparse
  (typically 2 per event). yes_ask is monotone in threshold.
- `B<int>`   bucketed markets   — "$X to $X+99.99" single $100 (BTC) or
  $40 (ETH) range. Dense (50+ per event). yes_ask peaks around ATM, NOT
  monotone — adjacent buckets on the low-prob side naturally have
  yes_ask values that rise toward ATM (e.g. 0.02 → 0.05 → 0.15).

Treating B-markets under the cumulative-ladder fade detector reads that
natural slope as a mispricing and buys YES on the lower-threshold leg,
which is deep OTM and loses ~100% of the time. Paper trial 2026-05-09→12
hit 35 settled / 5.7% WR / -83.6% ROI before this fix.

This module partitions legs by suffix prefix and routes each subset
through the framework with its correct ladder_type. Bucketed math
additionally requires a roughly-complete bucket set (sum of yes_mid
near 1.0) before any signal fires.
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
ATM_WINDOW_N = 2  # cumulative path only — ATM ±N buckets

# Bucketed math (sum-to-1 arb, implied-prob fade) is only sound when the
# observed legs cover most of the probability mass. Below this window we
# treat the ladder as incomplete and skip.
BUCKETED_SUM_MID_MIN = 0.90
BUCKETED_SUM_MID_MAX = 1.10


class LadderCryptoStrategy:
    STRATEGY_NAME = "ladder_crypto"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _parse_suffix(suffix: str) -> Optional[float]:
        """Parse strike from suffix (B<int> or T<float>)."""
        if len(suffix) < 2:
            return None
        i = 0
        while i < len(suffix) and not (suffix[i].isdigit() or suffix[i] in ".-"):
            i += 1
        if i >= len(suffix):
            return None
        try:
            return float(suffix[i:])
        except ValueError:
            return None

    @staticmethod
    def _ladder_kind(suffix: str) -> Optional[str]:
        """Map suffix prefix to ladder mechanic.

        B → bucketed (mutually exclusive single-range outcome).
        T → cumulative (threshold above/below).
        """
        if not suffix:
            return None
        head = suffix[0]
        if head == "B":
            return "bucketed"
        if head == "T":
            return "cumulative"
        return None

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        """Event key from ticker.

        Handles both formats:
          KXBTC-26MAY09-1500-B70250 → '26MAY09-1500'  (legacy 4-part)
          KXBTC-26MAY1017-T89749.99 → '26MAY1017'    (current 3-part: date+time concat)
        """
        parts = m.get("ticker", "").split("-")
        return "-".join(parts[1:-1]) if len(parts) >= 3 else ""

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @classmethod
    def _atm_proxy(cls, legs: list[dict]) -> Optional[float]:
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
                suf = m["ticker"].rsplit("-", 1)[-1]
                thr = cls._parse_suffix(suf)
                if thr is None:
                    continue
                best_dist = d
                best = thr
        return best

    @classmethod
    def _atm_window_filter(cls, legs: list[dict], atm_price: float, window_n: int,
                           bucket_size: float) -> list[dict]:
        lo = atm_price - window_n * bucket_size
        hi = atm_price + window_n * bucket_size
        out = []
        for m in legs:
            suf = m["ticker"].rsplit("-", 1)[-1]
            thr = cls._parse_suffix(suf)
            if thr is None:
                continue
            if lo <= thr <= hi:
                out.append(m)
        return out

    def _enrich(self, ms: list[dict]) -> list[dict]:
        out = []
        for m in ms:
            yes_price, no_price = self._client.get_market_prices(m["ticker"])
            if yes_price is None or no_price is None:
                continue
            yes_depth, no_depth = self._client.get_order_book_depth(m["ticker"])
            out.append({
                **m,
                "yes_ask": yes_price, "yes_bid": yes_price,
                "no_ask": no_price, "no_bid": no_price,
                "yes_depth": yes_depth or 0.0, "no_depth": no_depth or 0.0,
            })
        return out

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_unlocked_markets()
                   if self._is_target_market(m)]
        if not markets:
            return

        # Partition by (series, ladder_kind). B and T share the event key
        # but require different ladder mechanics.
        by_series_kind: dict[tuple[str, str], list[dict]] = {}
        for m in markets:
            series = m["ticker"].split("-")[0]
            suf = m["ticker"].rsplit("-", 1)[-1]
            kind = self._ladder_kind(suf)
            if kind is None:
                continue
            by_series_kind.setdefault((series, kind), []).append(m)

        for (series, kind), ms in by_series_kind.items():
            enriched = self._enrich(ms)
            if not enriched:
                continue
            if kind == "cumulative":
                atm = self._atm_proxy(enriched)
                if atm is None:
                    continue
                bucket_size = BUCKET_SIZE_BY_SERIES.get(series, 100.0)
                legs = self._atm_window_filter(enriched, atm, ATM_WINDOW_N, bucket_size)
            else:
                # Bucketed sum-to-1 math needs the full observed bucket set;
                # the ATM window applied to bucketed legs destroys the signal.
                legs = enriched
            if not legs:
                continue
            groups = group_legs_into_ladders(
                legs, suffix_parser=self._parse_suffix,
                ladder_type=kind,
                event_key_extractor=self._extract_event_key,
                direction=self.DIRECTION,
            )
            for g in groups:
                await self._handle_group(g)

    async def _handle_group(self, group):
        cfg = self.config
        if group.ladder_type == "bucketed":
            sum_mid = sum((l.yes_ask + l.yes_bid) / 2.0 for l in group.legs)
            if not (BUCKETED_SUM_MID_MIN <= sum_mid <= BUCKETED_SUM_MID_MAX):
                return
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
