"""Ladder Fed — KXFEDDECISION bucketed mutually-exclusive outcome ladder.

Each FOMC meeting becomes one bucketed group: cuts (C25, C50, ...), holds
(H0), hikes (H25, ...). sum_yes ≈ 1 invariant. 8 meetings/yr → mostly
idle; primary fire window is the 2 weeks pre-FOMC reprice.

Conflict with C1 (FOMC window sub-project, separate spec): if both
strategies fire on the same ticker, ladder_fed has dedup priority via
the standard DB has_existing_trade gate (extended to ladder_* in Task 7).

2026-07-28 — two containment gates. Between 05-11 and 07-23 this booked 63
entries and settled NONE of them: Kalshi lists FOMC contracts out to 28JAN, so
it kept buying meetings 6-18 months away. No leg could ever return a result,
the strategy stayed unevaluable, and $750 of paper capital sat locked (a third
of the whole paper tracker, which is what froze paper research on 07-28). So:
  1. horizon — only the nearest `LADDER_FED_HORIZON_MEETINGS` still-open
     meetings are tradeable, and the filter runs BEFORE enrichment (2 Kalshi
     calls per leg — see `_enrich_markets_sync`);
  2. cap — `LADDER_FED_MAX_OPEN_EXPOSURE_USDC` bounds open capital across
     those meetings. It deliberately scopes to in-horizon tickers only: the
     legacy long-dated legs can't settle for years, so counting them would
     mothball the strategy rather than bound it.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)

logger = logging.getLogger("ladder_fed")

TARGET_SERIES = frozenset({"KXFEDDECISION"})

# Throttle for the cap-blocked log line — this strategy scans on the shared
# loop, and an unthrottled INFO here reproduces the "Insufficient bankroll"
# flood that made journalctl unreadable on 07-28.
_CAP_LOG_INTERVAL_S = 600.0


class LadderFedStrategy:
    STRATEGY_NAME = "ladder_fed"
    LADDER_TYPE = "bucketed"
    DIRECTION = "asc"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._last_cap_log = 0.0

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

    @staticmethod
    def _close_dt(m: dict) -> Optional[datetime]:
        """Parse a leg's `close_time` (Kalshi ISO-8601 Z). None if unusable —
        such a leg is dropped rather than assumed near-dated."""
        raw = m.get("close_time")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _near_dated_markets(self, markets: list[dict], now: datetime) -> list[dict]:
        """Legs from the nearest N still-open FOMC meetings, N =
        `LADDER_FED_HORIZON_MEETINGS`.

        A meeting whose close_time has already passed is dropped outright and
        does NOT consume one of the N slots — otherwise the front slot would
        rot after each FOMC and the strategy would quietly go idle.
        """
        close_by_event: dict[str, datetime] = {}
        for m in markets:
            close = self._close_dt(m)
            if close is None or close <= now:
                continue
            key = self._extract_event_key(m)
            if not key:
                continue
            prev = close_by_event.get(key)
            if prev is None or close < prev:
                close_by_event[key] = close

        horizon = max(0, int(self.config.LADDER_FED_HORIZON_MEETINGS))
        allowed = {
            k for k, _ in sorted(close_by_event.items(), key=lambda kv: kv[1])[:horizon]
        }
        return [
            m for m in markets
            if self._extract_event_key(m) in allowed and self._close_dt(m) is not None
        ]

    @staticmethod
    def _enrich_markets_sync(markets: list[dict], client) -> list[dict]:
        # 2026-05-19 evening: this used to run inline in scan_and_execute,
        # making 48 sync httpx.Client.get() calls (24 markets × 2 endpoints)
        # while pretending to be async. Under degraded Kalshi latency this
        # blocked the event loop for 670-715 s per iter (observed), starving
        # all sports scan_loops on the same loop — the actual root cause of
        # the ~22/min ConnectTimeout flood originally blamed on the Odds API
        # httpx pool. Moved to a worker thread via asyncio.to_thread() so
        # the loop stays responsive.
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

        # Gate 1: horizon. Runs before enrichment so out-of-horizon meetings
        # cost zero Kalshi calls (was 2 per leg × ~65 legs every scan).
        markets = self._near_dated_markets(markets, datetime.now(timezone.utc))
        if not markets:
            return

        # Gate 2: open-exposure cap, scoped to the in-horizon tickers.
        headroom = await self._exposure_headroom([m["ticker"] for m in markets])
        if headroom <= 0:
            self._log_cap_blocked()
            return

        enriched = await asyncio.to_thread(self._enrich_markets_sync, markets, self._client)

        groups = group_legs_into_ladders(
            enriched, suffix_parser=self._parse_suffix,
            ladder_type=self.LADDER_TYPE,
            event_key_extractor=self._extract_event_key,
            direction=self.DIRECTION,
        )
        for g in groups:
            # Headroom is shared across groups within one scan, so a multi-group
            # slate can't collectively blow past the cap before the next re-read.
            headroom -= await self._handle_group(g, headroom)
            if headroom <= 0:
                break

    async def _exposure_headroom(self, tickers: list[str]) -> float:
        """Remaining open-capital budget across the in-horizon meetings.

        Falls back to the full cap when the OrderManager has no DB (unit
        harnesses) — the strategy stays functional rather than going dark.
        """
        cap = float(self.config.LADDER_FED_MAX_OPEN_EXPOSURE_USDC)
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            return cap
        used = await db.strategy_open_exposure(self.STRATEGY_NAME, tickers)
        return cap - used

    def _log_cap_blocked(self) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._last_cap_log < _CAP_LOG_INTERVAL_S:
            return
        self._last_cap_log = now
        logger.info(
            f"[ladder_fed] open exposure at cap "
            f"${self.config.LADDER_FED_MAX_OPEN_EXPOSURE_USDC:.2f} across the "
            f"nearest {self.config.LADDER_FED_HORIZON_MEETINGS} FOMC meetings — "
            f"no new entries until legs settle"
        )

    async def _handle_group(self, group, headroom: float) -> float:
        """Trade one meeting's ladder. Returns the notional committed, so the
        caller can decrement the shared headroom."""
        cfg = self.config
        arb = find_arb_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
            min_depth_usdc=cfg.LADDER_MIN_DEPTH_USDC,
            max_position_usdc=min(cfg.LADDER_MAX_POSITION_PER_GROUP, headroom),
        )
        if arb:
            spent = 0.0
            for leg in arb[0].legs:
                await self._order_manager.market_order(
                    ticker=leg.ticker, side=leg.side, size_usdc=leg.size_usdc,
                    price=leg.price, strategy=self.STRATEGY_NAME, market_id=leg.ticker,
                )
                spent += leg.size_usdc
            return spent
        fade = find_fade_opportunities(
            group, fee=cfg.KALSHI_FEE_PER_CONTRACT,
            min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
            max_size_usdc=min(cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5, headroom),
        )
        if fade:
            t = fade[0]
            await self._order_manager.market_order(
                ticker=t.ticker, side=t.side, size_usdc=t.size_usdc,
                price=t.price, strategy=self.STRATEGY_NAME, market_id=t.ticker,
            )
            return t.size_usdc
        return 0.0
