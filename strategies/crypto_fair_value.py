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

    async def _get_vol(self, asset: str, *, db) -> Optional[tuple[float, str]]:
        """Returns (vol, source) or None.

        Source priority:
          1. Deribit ATM IV (BTC/ETH only, fresh ≤ 6h)
          2. PriceFeed.get_volatility (XRP, or Deribit stale/missing)
        """
        DERIBIT_ASSETS = ("BTC", "ETH")
        DERIBIT_TTL_HOURS = 6.0
        if asset in DERIBIT_ASSETS:
            iv_row = await db.fetch_nearest_crypto_iv(asset)
            if iv_row:
                try:
                    fetched = datetime.fromisoformat(
                        iv_row["fetched_at"].replace("Z", "+00:00")
                    )
                    age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
                    if age_h <= DERIBIT_TTL_HOURS:
                        return float(iv_row["atm_iv"]), "deribit"
                except Exception:
                    pass
        rv = self._price_feed.get_volatility(asset)
        if rv:
            return float(rv), "realized"
        return None

    def _compute_edge(
        self, *,
        kalshi_yes_ask: float, kalshi_yes_bid: float,
        kalshi_no_ask: float, kalshi_no_bid: float,
        fair_prob: float,
    ) -> Optional[dict]:
        """Returns {'side', 'deviation', 'entry_price', 'ev_per_contract'} or None.

        Mirrors C1 macro_event._compute_edge math (same fee + cushion).
        """
        cfg = self.config
        kalshi_yes_mid = (kalshi_yes_ask + kalshi_yes_bid) / 2.0
        deviation = fair_prob - kalshi_yes_mid
        if abs(deviation) < cfg.CRYPTO_FAIR_MIN_DEVIATION:
            return None
        fee = cfg.KALSHI_FEE_PER_CONTRACT
        if deviation > 0:
            side = "yes"
            entry_price = kalshi_yes_ask
            ev = fair_prob - entry_price - fee
        else:
            side = "no"
            entry_price = kalshi_no_ask
            ev = (1.0 - fair_prob) - entry_price - fee
        if ev <= 0:
            return None
        return {
            "side": side,
            "deviation": deviation,
            "entry_price": entry_price,
            "ev_per_contract": ev,
        }

    def _size_for_edge(self, deviation: float) -> float:
        cfg = self.config
        base = cfg.CRYPTO_FAIR_BASE_SIZE_USDC
        scale = max(0.5, min(3.0, abs(deviation) / cfg.CRYPTO_FAIR_MIN_DEVIATION))
        return min(base * scale, cfg.CRYPTO_FAIR_MAX_POSITION_PER_TICKER)
