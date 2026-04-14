"""Sports Midshot Strategy — buy 5-35c contracts with 2.5x+ payoff."""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_midshot")


class SportsMidshotStrategy(SportsTraderStrategy):
    """Buy cheap-to-mid contracts with moderate payoff potential.

    Wider than Longshot (5-25c, 3x+) to collect more samples for V3
    comparison. Overlap with Longshot is intentional — same ticker may
    be recorded by both strategies, enabling A/B comparison on identical
    outcomes.
    """

    TRADED_TICKERS_FILE = "data/sports_midshot_tickers.json"
    STRATEGY_NAME = "sports_midshot"
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
        """Buy contracts 0.05 <= price <= 0.35 with payoff >= 2.5x.

        Fee-adjusted effective upper bound is ~0.33 (0.35 at 2.5x threshold
        requires payoff >= 2.5, but 1/(0.35+0.07) = 2.38 so 0.33-0.35 gets
        filtered by payoff).
        """
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        best_signal = None
        best_payoff = 0.0

        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (0.05 <= price <= 0.35):
                continue
            fee_pct = min(fee, price, 1 - price) / price
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 2.5 and payoff > best_payoff:
                best_signal = {
                    "side": side,
                    "price": price,
                    "edge": payoff,
                    "strategy": "midshot",
                }
                best_payoff = payoff

        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        """Small fixed size for portfolio approach — same as longshot."""
        return min(3.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        """Buy at ask price (aggressive fill)."""
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        payoff = signal.get("edge", 0)
        return (f"[MIDSHOT] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} payoff={payoff:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
