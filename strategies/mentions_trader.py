import json
import logging
import os
import re
import time
from typing import Optional

from core.external_data import FrequencyDataProvider

logger = logging.getLogger("mentions_trader")

# Patterns to match mentions markets
MENTION_PATTERNS = [
    re.compile(r"Will (.+?) (mention|say|reference|bring up) (.+?)[\?\.]", re.IGNORECASE),
    re.compile(r"Will (.+?) (mention|say) ['\"](.+?)['\"]", re.IGNORECASE),
]

# Ticker suffix → frequency data key mapping for KXFEDMENTION series
TICKER_KEYWORD_MAP = {
    "YIEL": "yield curve", "VOLA": "volatility", "UNCH": "unchanged", "UNCE": "uncertainty",
    "TRUM": "trump", "TRAD": "trade war", "TAX": "tax", "TARI": "tariff",
    "STAG": "stagflation", "SOFTL": "soft landing", "SOFT": "softening", "SLOW": "slowdown",
    "SHUT": "shutdown", "SHOC": "shock", "REST": "restrictive", "REPL": "replace",
    "RENO": "renovation", "RECE": "recession", "QT": "qt", "QE": "qe",
    "PROJ": "projection", "PROD": "productivity", "PROB": "probability", "PRES": "president",
    "PARD": "pardon", "PAND": "pandemic", "OIL": "oil", "NATI": "national debt",
    "MEDI": "median", "LAYO": "layoff", "KALS": "kalshi", "IRAN": "iran",
    "GOODA": "good afternoon", "GOOD": "goods inflation", "GOLD": "gold", "GAS": "gas",
    "EXPE": "expectation", "EGG": "egg", "DOT": "dot plot", "DOLL": "dollar",
    "DISS": "dissent", "CRED": "credit", "CONS": "consumer confidence", "CENT": "central bank",
    "BITC": "bitcoin", "BALA": "balance sheet", "ANCH": "anchor", "AI": "ai",
}


class MentionsTraderStrategy:
    """Trade mentions markets using historical speech frequency data."""

    TRADED_TICKERS_FILE = "data/mentions_traded_tickers.json"

    def __init__(self, config, order_manager, market_scanner, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._scanner = market_scanner
        self._client = kalshi_client
        self._data_provider = FrequencyDataProvider(config.MENTIONS_FREQUENCY_API_URL)
        self._evaluated: dict[str, float] = {}  # ticker -> last eval time
        self._traded_tickers: set[str] = set()
        self._last_summary_time = 0.0

        # Layer 1: Load from persistent file (survives API failures)
        self._load_traded_tickers_file()

        # Layer 2: Load from Kalshi API (positions + resting orders)
        try:
            positions = kalshi_client.get_positions()
            for p in positions:
                self._traded_tickers.add(p["ticker"])
        except Exception as e:
            logger.error(f"[MENTIONS] CRITICAL: Failed to load positions — duplicates possible: {e}")
        try:
            resting = kalshi_client.get_resting_orders()
            for o in resting:
                self._traded_tickers.add(o["ticker"])
        except Exception as e:
            logger.error(f"[MENTIONS] CRITICAL: Failed to load resting orders — duplicates possible: {e}")

        # Save merged set back to file
        self._save_traded_tickers_file()
        logger.info(f"[MENTIONS] Init: loaded {len(self._traded_tickers)} tickers (file + API)")

    def _load_traded_tickers_file(self):
        """Load traded tickers from persistent file."""
        try:
            if os.path.exists(self.TRADED_TICKERS_FILE):
                with open(self.TRADED_TICKERS_FILE) as f:
                    tickers = json.load(f)
                self._traded_tickers.update(tickers)
                logger.info(f"[MENTIONS] Loaded {len(tickers)} tickers from file")
        except Exception as e:
            logger.warning(f"[MENTIONS] Could not load traded tickers file: {e}")

    def _save_traded_tickers_file(self):
        """Persist traded tickers to file."""
        try:
            with open(self.TRADED_TICKERS_FILE, "w") as f:
                json.dump(sorted(self._traded_tickers), f)
        except Exception as e:
            logger.warning(f"[MENTIONS] Could not save traded tickers file: {e}")

    def parse_market_title(self, title: str) -> Optional[tuple[str, str]]:
        """Extract (person, keyword) from market title. Returns None if no match."""
        for pattern in MENTION_PATTERNS:
            match = pattern.search(title)
            if match:
                person = match.group(1).strip()
                keyword = match.group(3).strip().strip("'\"")
                return person, keyword
        return None

    def parse_ticker(self, ticker: str) -> Optional[tuple[str, str]]:
        """Extract (person, keyword) from ticker suffix. More reliable than title parsing."""
        if "KXFEDMENTION" in ticker:
            suffix = ticker.split("-")[-1]
            keyword = TICKER_KEYWORD_MAP.get(suffix)
            if keyword:
                return "powell", keyword
        return None

    def evaluate(self, yes_price: float, historical_freq: float) -> Optional[dict]:
        """Compare market price to historical frequency. Return trade signal if edge exists.

        Aggressive sizing: 30%+ edge → 10% bankroll, 50%+ edge → 15% bankroll.
        """
        min_edge = self.config.MENTIONS_MIN_EDGE
        edge = historical_freq - yes_price
        abs_edge = abs(edge)
        if abs_edge < min_edge:
            return None

        # Aggressive position sizing based on edge magnitude
        if abs_edge >= 0.50:
            size_pct = 0.15  # 15% bankroll for huge edge
        elif abs_edge >= 0.30:
            size_pct = 0.10  # 10% bankroll for strong edge
        else:
            size_pct = 0.05  # 5% bankroll for moderate edge

        if edge > 0:
            return {"side": "yes", "price": yes_price, "edge": edge, "size_pct": size_pct}
        else:
            no_price = 1.0 - yes_price
            return {"side": "no", "price": no_price, "edge": abs_edge, "size_pct": size_pct}

    async def scan_and_execute(self):
        if not self.config.MENTIONS_ENABLED:
            return
        if self._order_manager.is_circuit_breaker_active():
            return

        # Get all markets from the broad pool and filter by ticker prefix + title regex
        all_mkts = self._scanner.get_all_markets()
        markets = [m for m in all_mkts if m.get("ticker", "").startswith("KXFEDMENTION")]
        # Also add title-matched markets from other series
        title_markets = self._scanner.filter_markets(r"(?i)will .+ (mention|say)")
        seen = {m["ticker"] for m in markets}
        for m in title_markets:
            if m["ticker"] not in seen:
                markets.append(m)
                seen.add(m["ticker"])

        now = time.time()
        trades_attempted = 0
        skipped_cooldown = 0
        skipped_already_traded = 0
        skipped_no_parse = 0
        skipped_no_freq = 0
        skipped_no_price = 0
        skipped_no_edge = 0

        for market in markets:
            ticker = market["ticker"]
            if now - self._evaluated.get(ticker, 0) < 300:
                skipped_cooldown += 1
                continue
            if ticker in self._traded_tickers:
                skipped_already_traded += 1
                continue
            self._evaluated[ticker] = now

            try:
                # Try ticker-based parsing first (more reliable), fall back to title
                parsed = self.parse_ticker(ticker)
                if parsed is None:
                    title = market.get("title", "")
                    parsed = self.parse_market_title(title)
                if parsed is None:
                    skipped_no_parse += 1
                    continue
                person, keyword = parsed

                freq = await self._data_provider.get_mention_frequency(person, keyword)
                if freq is None:
                    skipped_no_freq += 1
                    continue

                yes_price, no_price = self._client.get_market_prices(ticker)
                if yes_price is None:
                    skipped_no_price += 1
                    continue

                signal = self.evaluate(yes_price, freq)
                if signal is None:
                    skipped_no_edge += 1
                    continue

                # Aggressive sizing: use edge-based percentage instead of Kelly
                size_pct = signal.get("size_pct", 0.05)
                size = self._order_manager.bankroll * size_pct
                if size < 1.0:
                    continue

                logger.info(
                    f"[MENTIONS] {ticker}: '{person}' + '{keyword}' "
                    f"freq={freq:.2f} vs price={yes_price:.2f} → {signal['side']} ${size:.2f} ({size_pct:.0%} bankroll)"
                )
                # Use limit order (Maker) for better pricing — place at fair value
                limit_price = round(freq if signal["side"] == "yes" else (1.0 - freq), 2)
                limit_price = max(0.01, min(0.99, limit_price))
                # Mark BEFORE order to prevent duplicates even if order/DB fails
                self._traded_tickers.add(ticker)
                self._save_traded_tickers_file()
                result = await self._order_manager.limit_order(
                    ticker=ticker, side=signal["side"], price=limit_price,
                    size_usdc=size, strategy="mentions_trader", market_id=ticker,
                )
                trades_attempted += 1
            except Exception as e:
                logger.error(f"Mentions trader error on {ticker}: {e}")

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[MENTIONS] Found {len(markets)} mention markets, "
                f"{trades_attempted} trades attempted "
                f"(skip: cooldown={skipped_cooldown}, already_traded={skipped_already_traded}, "
                f"no_parse={skipped_no_parse}, no_freq={skipped_no_freq}, "
                f"no_price={skipped_no_price}, no_edge={skipped_no_edge})"
            )
            self._last_summary_time = now
