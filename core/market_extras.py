"""External market data fetchers for Phase 0 observers (2026-05-19).

Three signals via yfinance (free, no auth):

- ETF flow proxy: IBIT (BTC) / ETHA (ETH) daily volume z-score.
  High volume relative to 20-day average = strong creation/redemption
  activity. Combined with close-vs-prev-close direction = signed flow proxy.

- CME basis: BTC=F (CME BTC futures) close minus implied spot.
  Positive basis = contango = market expects upward drift. Negative =
  backwardation = bearish.

Each fetcher has internal TTL cache (10 min — daily-resolution data, no
need to hit yfinance more often than every scan-cycle's worth of scans).

yfinance dependency is optional — if not installed, fetchers return None
and the corresponding observer stays silent.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

logger = logging.getLogger("market_extras")

_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}
_TTL_S = 600.0   # 10 min — daily data refreshes once per US trading day


def _cached(key: tuple[str, str], fetcher) -> Optional[float]:
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and (now - hit[0]) < _TTL_S:
        return hit[1]
    val = fetcher()
    _CACHE[key] = (now, val)
    return val


def _import_yf():
    try:
        import yfinance as yf
        return yf
    except ImportError:
        logger.warning("yfinance not installed — external observers will be silent")
        return None


def _fetch_etf_flow_zscore(asset: str) -> Optional[float]:
    """Signed daily-volume z-score for the asset's flagship spot ETF.
    Asset → ETF: BTC→IBIT, ETH→ETHA."""
    sym = {"BTC": "IBIT", "ETH": "ETHA"}.get(asset)
    if sym is None:
        return None
    yf = _import_yf()
    if yf is None:
        return None
    try:
        import warnings
        warnings.filterwarnings("ignore")
        h = yf.Ticker(sym).history(period="30d", auto_adjust=False)
        if len(h) < 21:
            return None
        last_close = h["Close"].iloc[-1]
        prev_close = h["Close"].iloc[-2]
        last_volume = h["Volume"].iloc[-1]
        prior = h["Volume"].iloc[-21:-1]
        if prior.std() == 0:
            return None
        z = (last_volume - prior.mean()) / prior.std()
        sign = 1.0 if last_close > prev_close else -1.0 if last_close < prev_close else 0.0
        return float(sign * z)
    except Exception as e:
        logger.warning(f"ETF z-score fetch failed {sym}: {e}")
        return None


def _fetch_cme_basis(asset: str) -> Optional[float]:
    """CME futures basis = (futures_close - spot_proxy) / spot_proxy.
    Positive = contango. Spot proxy = same-day Open (free-data tradeoff)."""
    sym = {"BTC": "BTC=F", "ETH": "ETH=F"}.get(asset)
    if sym is None:
        return None
    yf = _import_yf()
    if yf is None:
        return None
    try:
        import warnings
        warnings.filterwarnings("ignore")
        h = yf.Ticker(sym).history(period="2d", auto_adjust=False)
        if len(h) < 1:
            return None
        last = h.iloc[-1]
        futures_close = float(last["Close"])
        spot_proxy = float(last["Open"])
        if spot_proxy <= 0:
            return None
        return (futures_close - spot_proxy) / spot_proxy
    except Exception as e:
        logger.warning(f"CME basis fetch failed {sym}: {e}")
        return None


def get_etf_flow_zscore(asset: str) -> Optional[float]:
    """TTL-cached signed-volume z-score for asset's spot ETF."""
    return _cached((asset, "etf_z"), lambda: _fetch_etf_flow_zscore(asset))


def get_cme_basis(asset: str) -> Optional[float]:
    """TTL-cached CME futures basis (signed decimal)."""
    return _cached((asset, "cme_basis"), lambda: _fetch_cme_basis(asset))
