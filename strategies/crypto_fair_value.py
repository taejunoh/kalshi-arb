"""Crypto Fair-Value Trader (B2) — event-driven 1-sec fast-scan trader.

Targets KXBTC + KXETH + KXXRP + KXBTCD + KXETHD (binary "asset >= K at T"
markets). Compares Kalshi implied prob to lognormal model fair prob using
external spot (PriceFeed via Binance WS) + Deribit IV (BTC/ETH) or
realized vol (XRP fallback). Fires single edge-scaled directional trade
gated by 9 risk gates including 60-sec recently-fired guard and
cross-strategy exposure cap.

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

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
