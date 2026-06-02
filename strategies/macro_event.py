"""Macro Event Trader (C1) — pre-event consensus deviation single-fire trader.

Targets KXCPI + KXFEDDECISION buckets. Compares Kalshi implied prob to
external consensus (CME FedWatch + Cleveland Fed Nowcast) cached via
core/macro_consensus.py and the cron in scripts/macro_consensus_cron.py.
Fires single edge-scaled directional trade when |deviation| >= MIN_DEVIATION
and EV after fee is positive, gated by 9 risk checks including
cross-strategy exposure cap with ladder_fed.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("macro_event")

TARGET_SERIES = frozenset({"KXCPI", "KXFEDDECISION"})

# event_key prefix maps from the Kalshi series. Used by
# core/macro_consensus.py.fetch_macro_consensus(event_key, bucket).
SERIES_TO_EVENT_PREFIX = {
    "KXCPI": "CPI",
    "KXFEDDECISION": "FOMC",
}


def _cpi_reference_to_release_month(ref_month: str) -> str:
    """'26MAY' -> '26JUN'. CPI for reference month M is released in month M+1.

    Kalshi names KXCPI markets by the data's REFERENCE month (KXCPI-26MAY =
    May CPI), but scripts/macro_consensus_cron.py.event_key_cpi keys
    macro_consensus rows by the RELEASE date (the following month). Shift +1
    month so fetch_macro_consensus() finds the stored 'CPI-26JUN' rows.
    Returns '' if the month token is unparseable."""
    try:
        dt = datetime.strptime(ref_month, "%y%b")
    except ValueError:
        return ""
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return dt.replace(year=year, month=month).strftime("%y%b").upper()


class MacroEventStrategy:
    STRATEGY_NAME = "macro_event"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client

    @staticmethod
    def _is_target_market(m: dict) -> bool:
        return m.get("ticker", "").split("-")[0] in TARGET_SERIES

    @staticmethod
    def _extract_event_key(m: dict) -> str:
        """KXFEDDECISION-26JUN-C25 -> 'FOMC-26JUN'.
        KXCPI-26MAY-T0.3 -> 'CPI-26JUN' (reference month shifted to release
        month to match the consensus cron — see _cpi_reference_to_release_month).
        FOMC tickers already use the meeting month, so no shift."""
        parts = m.get("ticker", "").split("-")
        if len(parts) < 3:
            return ""
        series = parts[0]
        date_part = parts[1]
        prefix = SERIES_TO_EVENT_PREFIX.get(series)
        if not prefix:
            return ""
        if series == "KXCPI":
            date_part = _cpi_reference_to_release_month(date_part)
            if not date_part:
                return ""
        return f"{prefix}-{date_part}"

    @staticmethod
    def _extract_bucket(m: dict) -> str:
        return m.get("ticker", "").rsplit("-", 1)[-1]

    def _inside_window(self, m: dict, now: Optional[datetime] = None) -> bool:
        """True if MIN_MINUTES <= time_to_close <= MAX_HOURS."""
        if now is None:
            now = datetime.now(timezone.utc)
        close_iso = m.get("close_time", "")
        if not close_iso:
            return False
        try:
            close_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except Exception:
            return False
        delta = close_dt - now
        delta_min = delta.total_seconds() / 60.0
        delta_hr = delta.total_seconds() / 3600.0
        cfg = self.config
        return (delta_min >= cfg.MACRO_EVENT_WINDOW_MIN_MINUTES
                and delta_hr <= cfg.MACRO_EVENT_WINDOW_MAX_HOURS)

    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        consensus: float,
    ) -> Optional[dict]:
        """Returns trade signal dict or None.

        Signal dict: {'side': 'yes'|'no', 'deviation': float (signed),
                      'entry_price': float, 'ev_per_contract': float}
        Caller still needs to apply window + dedup + depth + exposure gates.
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = consensus - kalshi_yes_mid  # positive ⇒ Kalshi underprices YES
        if abs(deviation) < cfg.MACRO_EVENT_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            # EV = consensus·(1 - entry - fee) + (1-consensus)·(-entry - fee)
            #    = consensus - entry - fee
            ev = consensus - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            # NO wins prob = 1 - consensus
            ev = (1.0 - consensus) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        """Edge-scaled fire size capped by per-ticker maximum."""
        cfg = self.config
        base = cfg.MACRO_EVENT_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.MACRO_EVENT_MIN_DEVIATION))
        return min(base * scale, cfg.MACRO_EVENT_MAX_POSITION_PER_TICKER)

    async def scan_and_execute(self):
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        markets = [m for m in self._scanner.get_all_markets()
                   if self._is_target_market(m)]
        if not markets:
            return
        now = datetime.now(timezone.utc)
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db attribute — skipping scan")
            return
        for m in markets:
            await self._handle_market(m, db=db, now=now)

    async def _handle_market(self, market: dict, *, db, now: datetime):
        cfg = self.config
        ticker = market.get("ticker", "")

        # Gate 1: window
        if not self._inside_window(market, now=now):
            return

        # Fetch latest book in a worker thread — keeps the event loop free
        # while the two sync httpx.Client calls run. Mirrors ladder_fed /
        # ladder_crypto / xvenue_polymarket / sports_trader fixes.
        def _fetch_book_sync(client, t):
            yp, np_ = client.get_market_prices(t)
            if yp is None or np_ is None:
                return None
            yd, nd = client.get_order_book_depth(t)
            return yp, np_, yd or 0.0, nd or 0.0

        book = await asyncio.to_thread(_fetch_book_sync, self._client, ticker)
        if book is None:
            return
        yes_price, no_price, yes_depth, no_depth = book

        # Bid/ask: prefer market-dict fields (in cents → divide by 100) so that
        # the mid is computed from the spread rather than collapsing to ask.
        # Fall back to client price as both ask and bid when dict fields absent
        # (bid-ask collapse limitation documented in spec section 3.7 for v0).
        raw_yes_ask = market.get("yes_ask")
        raw_yes_bid = market.get("yes_bid")
        raw_no_ask = market.get("no_ask")
        raw_no_bid = market.get("no_bid")
        if raw_yes_ask is not None and raw_yes_bid is not None:
            yes_ask = raw_yes_ask / 100.0
            yes_bid = raw_yes_bid / 100.0
        else:
            yes_ask = yes_bid = yes_price
        if raw_no_ask is not None and raw_no_bid is not None:
            no_ask = raw_no_ask / 100.0
            no_bid = raw_no_bid / 100.0
        else:
            no_ask = no_bid = no_price

        # Resolve event_key + bucket + consensus
        event_key = self._extract_event_key(market)
        bucket = self._extract_bucket(market)
        if not event_key or not bucket:
            return
        consensus_row = await db.fetch_macro_consensus(event_key, bucket)
        if not consensus_row:
            return
        consensus = consensus_row["prob"]

        # Gate 2: consensus staleness
        try:
            fetched_at = datetime.fromisoformat(
                consensus_row["fetched_at"].replace("Z", "+00:00")
            )
        except Exception:
            return
        age_hours = (now - fetched_at).total_seconds() / 3600.0
        if age_hours > cfg.MACRO_CONSENSUS_STALE_HOURS:
            logger.warning(f"[MACRO] consensus stale for {event_key}/{bucket} "
                           f"({age_hours:.1f}h)")
            return

        # Gates 3 + 4: deviation + EV (handled by _compute_edge)
        edge = self._compute_edge(
            kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
            kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
            consensus=consensus,
        )
        if not edge:
            return

        # Gate 5: depth (check the side we're entering)
        depth_for_side = yes_depth if edge["side"] == "yes" else no_depth
        if depth_for_side < cfg.MACRO_EVENT_MIN_DEPTH_USDC:
            return

        # Gate 6: per-strategy dedup is enforced by OrderManager's
        # has_existing_trade guard — extended to macro_* in Task 6.

        # Gate 7: cross-strategy exposure cap
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE:
            logger.info(f"[MACRO] exposure cap on {ticker}: "
                        f"existing={existing} + size={size} > "
                        f"{cfg.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE}")
            return

        # Fire
        logger.info(f"[MACRO] FIRE {ticker} {edge['side']} size=${size:.2f} "
                    f"dev={edge['deviation']:+.4f} ev={edge['ev_per_contract']:+.4f}")
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
