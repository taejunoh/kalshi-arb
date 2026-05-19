"""Cross-Venue Polymarket Trader (B1) — Polymarket-anchored Kalshi single-side.

Reads Polymarket gamma-api quotes (cached via 1-min cron in
polymarket_quotes table). For each manual mapping entry, compares
Polymarket implied prob to Kalshi orderbook -> fires single-side
directional Kalshi trade when |deviation| >= MIN_DEVIATION and EV > 0.

Runs in main bot's _strategy_loop (60-sec cadence) — Polymarket
political markets are slow-moving, no separate fast-scan task needed.

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("xvenue_polymarket")


class XVenuePolymarketStrategy:
    STRATEGY_NAME = "xvenue_polymarket"

    def __init__(
        self, config, order_manager, market_scanner, kalshi_client,
        *, mapping,
    ):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._mapping = mapping
        self._recently_fired: dict[str, float] = {}

    def _kalshi_markets_for_prefix(self, prefix: str) -> list[dict]:
        """Return open Kalshi markets whose ticker startswith `prefix`."""
        return [
            m for m in self._scanner.get_all_markets()
            if m.get("ticker", "").startswith(prefix)
        ]

    @staticmethod
    def _apply_outcome_match(yes_prob: float, outcome_match: str) -> float:
        """For 'reversed' mapping, invert the prob to align Kalshi YES semantics."""
        if outcome_match == "reversed":
            return 1.0 - yes_prob
        return yes_prob

    def _is_quote_fresh(
        self, quote_row: dict, now: Optional[datetime] = None,
    ) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        fetched_at_iso = quote_row.get("fetched_at", "")
        if not fetched_at_iso:
            return False
        try:
            fetched_at = datetime.fromisoformat(
                fetched_at_iso.replace("Z", "+00:00")
            )
        except Exception:
            return False
        age_min = (now - fetched_at).total_seconds() / 60.0
        return age_min <= self.config.XVENUE_QUOTE_STALE_MINS

    def _is_quote_volume_ok(self, quote_row: dict) -> bool:
        vol = quote_row.get("volume_24h")
        if vol is None:
            return False
        return float(vol) >= self.config.XVENUE_MIN_POLYMARKET_VOLUME

    def _compute_edge(
        self, *,
        polymarket_yes: float,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
    ) -> Optional[dict]:
        """Returns trade signal dict or None.

        Signal dict: {'side': 'yes'|'no', 'deviation': float (signed),
                      'entry_price': float, 'ev_per_contract': float}
        polymarket_yes is the Kalshi-aligned probability AFTER applying
        outcome_match — caller is responsible for inversion if needed.
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = polymarket_yes - kalshi_yes_mid
        if abs(deviation) < cfg.XVENUE_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            ev = polymarket_yes - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            ev = (1.0 - polymarket_yes) - entry_price - fee
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
        base = cfg.XVENUE_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.XVENUE_MIN_DEVIATION))
        return min(base * scale, cfg.XVENUE_MAX_POSITION_PER_TICKER)

    def _fetch_book_sync(self, ticker: str):
        # Combined sync probe: dispatched via asyncio.to_thread so the two
        # httpx.Client.get calls don't block the event loop. Returns None
        # when the market has no price.
        yp, np_ = self._client.get_market_prices(ticker)
        if yp is None or np_ is None:
            return None
        depths = self._client.get_order_book_depth(ticker)
        return yp, np_, depths

    async def scan_and_execute(self):
        """Single iteration — called by main bot's _strategy_loop (60s)."""
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        db = getattr(self._order_manager, "_db", None)
        if db is None:
            logger.warning("OrderManager has no _db — xvenue_polymarket disabled")
            return

        # Hot-reload mapping each scan so JSON edits take effect without restart
        self._mapping.reload()

        now = datetime.now(timezone.utc)
        for slug in self._mapping.all_slugs():
            quote_row = await db.fetch_polymarket_quote(slug)
            if quote_row is None:
                continue
            # Gate 1: quote freshness
            if not self._is_quote_fresh(quote_row, now=now):
                continue
            # Gate 2: volume floor
            if not self._is_quote_volume_ok(quote_row):
                continue
            poly_yes_raw = float(quote_row["yes_price"])
            for entry in self._mapping.entries_for_slug(slug):
                prefix = entry["kalshi_ticker_prefix"]
                outcome_match = entry["outcome_match"]
                poly_yes = self._apply_outcome_match(poly_yes_raw, outcome_match)

                # Gate 3: mapping resolves to open Kalshi markets
                kalshi_markets = self._kalshi_markets_for_prefix(prefix)
                for m in kalshi_markets:
                    await self._handle_market(
                        m, slug=slug, poly_yes=poly_yes, db=db, now=now,
                    )

    async def _handle_market(
        self, market: dict, *, slug: str, poly_yes: float,
        db, now: datetime,
    ):
        cfg = self.config
        ticker = market.get("ticker", "")

        # Fetch Kalshi book in a worker thread. The two sync httpx.Client
        # calls used to run inline in this async method; with ~N slugs ×
        # M entries × K markets they produced 211-234 s event-loop blocks
        # per scan (SLOW_STRAT evidence), starving sports scan_loops and
        # producing the Odds API ConnectTimeout flood. Companion fixes in
        # ladder_fed.py and ladder_crypto.py.
        book = await asyncio.to_thread(self._fetch_book_sync, ticker)
        if book is None:
            return
        yes_price, no_price, depths = book
        yes_depth, no_depth = depths if depths else (0.0, 0.0)
        # Derive true 2-sided book (yes_bid ~= 1 - no_ask)
        yes_ask = yes_price
        no_ask = no_price
        yes_bid = max(0.0, 1.0 - no_ask)
        no_bid = max(0.0, 1.0 - yes_ask)

        # Gates 4 + 5: deviation + EV (via _compute_edge)
        edge = self._compute_edge(
            polymarket_yes=poly_yes,
            kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
            kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
        )
        if not edge:
            return

        # Gate 6: depth (side we're entering)
        depth_for_side = (yes_depth or 0.0) if edge["side"] == "yes" else (no_depth or 0.0)
        if depth_for_side < cfg.XVENUE_MIN_DEPTH_USDC:
            return

        # Gate 7: per-strategy DB dedup enforced by OrderManager (xvenue_* prefix).
        # Gate 8: cross-strategy exposure cap.
        size = self._size_for_edge(edge["deviation"])
        existing = await db.total_open_exposure(ticker)
        if existing + size > cfg.XVENUE_MAX_TICKER_TOTAL_EXPOSURE:
            return

        # Gate 9: recently_fired guard
        last = self._recently_fired.get(ticker)
        if last is not None and (time.time() - last) < cfg.XVENUE_RECENTLY_FIRED_SECS:
            return

        logger.info(
            f"[XVENUE] FIRE {ticker} {edge['side']} size=${size:.2f} "
            f"slug={slug} poly={poly_yes:.4f} kalshi_mid="
            f"{(yes_ask + yes_bid) / 2:.4f} dev={edge['deviation']:+.4f} "
            f"ev={edge['ev_per_contract']:+.4f}"
        )
        await self._order_manager.market_order(
            ticker=ticker, side=edge["side"], size_usdc=size,
            price=edge["entry_price"], strategy=self.STRATEGY_NAME,
            market_id=ticker,
        )
        self._recently_fired[ticker] = time.time()
