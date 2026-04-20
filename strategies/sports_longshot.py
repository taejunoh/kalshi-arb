"""Sports Longshot Strategy — buy cheap contracts (< 25c) with 3x+ payoff."""
import json
import logging
import os
import time
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_longshot")


class SportsLongshotStrategy(SportsTraderStrategy):
    """Buy cheap contracts with high payoff potential. Portfolio approach."""

    TRADED_TICKERS_FILE = "data/sports_longshot_tickers.json"
    STRATEGY_NAME = "sports_longshot"
    REGION_OVERRIDE = "us"  # V3 baseline stays on US retail books for continuity
    SCAN_INTERVAL_OVERRIDE = 120.0  # us retail books tick 60-90s; 120s scan captures movement

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        # Re-load with our own file
        self._traded_tickers = {}
        self._load_traded_tickers()

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Buy contracts < 25c with 3x+ payoff ratio."""
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        best_signal = None
        best_payoff = 0.0

        # Check YES side
        if 0.05 <= kalshi_yes <= 0.25:
            fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes
            cost = kalshi_yes * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 3.0 and payoff > best_payoff:
                best_signal = {"side": "yes", "price": kalshi_yes,
                               "edge": payoff, "strategy": "longshot"}
                best_payoff = payoff

        # Check NO side
        if 0.05 <= kalshi_no <= 0.25:
            fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
            cost = kalshi_no * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 3.0 and payoff > best_payoff:
                best_signal = {"side": "no", "price": kalshi_no,
                               "edge": payoff, "strategy": "longshot"}

        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        """Small fixed size for portfolio approach. Raised 3→4 on 2026-04-20
        post dead-zone rollout — non-sub-20¢ subset is positive-EV so the
        scale-up is on a validated band, not an unproven strategy."""
        return min(4.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        """Buy at ask price (aggressive fill)."""
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        payoff = signal.get("edge", 0)
        return (f"[LONGSHOT] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} payoff={payoff:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
