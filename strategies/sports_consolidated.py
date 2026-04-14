"""Sports Consolidated — V3's three price-filter strategies merged into one.

Name rationale: V3 (longshot/midshot/underdog) has overlapping price ranges,
so the same market can "triple-fire" and drain 3× the bankroll on a single
edge that all three agree on. This strategy "consolidates" those into a
single filter (price 0.05–0.40, payoff ≥ 2x) that covers the union of
their qualifying conditions while firing at most once per market.

Implementation is a broad price/payoff filter rather than three separate
checks because the union of {longshot:5-25c + 3x, midshot:5-35c + 2.5x,
underdog:5-40c + 2x} is functionally equivalent to (5-40c + 2x) with the
tightest edge case (midshot at 35c requiring 2.5x) already implied by the
price ceiling. Simpler code, same coverage.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_consolidated")


class SportsConsolidatedStrategy(SportsTraderStrategy):
    TRADED_TICKERS_FILE = "data/sports_consolidated_tickers.json"
    STRATEGY_NAME = "sports_consolidated"
    PRICE_MIN = 0.05
    PRICE_MAX = 0.40
    PAYOFF_MIN = 2.0

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        best_signal = None
        best_payoff = 0.0
        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (self.PRICE_MIN <= price <= self.PRICE_MAX):
                continue
            fee_pct = min(fee, price, 1 - price) / price if price > 0 else 0
            payoff = 1.0 / (price * (1 + fee_pct))
            if payoff < self.PAYOFF_MIN:
                continue
            if payoff > best_payoff:
                best_signal = {"side": side, "price": price,
                               "edge": payoff, "strategy": "consolidated"}
                best_payoff = payoff
        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        return min(3.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        return (f"[CONSOL] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} payoff={signal['edge']:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
