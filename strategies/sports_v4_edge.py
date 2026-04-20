"""Sports V4 Edge — V3 midshot price filter AND Pinnacle/consensus edge gate."""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_v4_edge")


class SportsV4EdgeStrategy(SportsTraderStrategy):
    """Combines midshot's price/payoff filter (0.05-0.35, 2.5x+) with a
    3% edge gate. Falls back to consensus when Pinnacle is absent.
    """

    TRADED_TICKERS_FILE = "data/sports_v4_edge_tickers.json"
    STRATEGY_NAME = "sports_v4_edge"
    MIN_EDGE = 0.03
    PRICE_MIN = 0.05
    PRICE_MAX = 0.35
    PAYOFF_MIN = 2.5
    # Price bucket ROI (2026-04-20, paper only):
    #   <10¢:   n=7, ROI +280%   ← keep
    #   10–15¢: n=6, 0% win, ROI −100%
    #   15–20¢: n=6, 0% win, ROI −100%
    #   ≥20¢:   ROI positive (+16%/+83%/+59%)
    # Dead-zone (not floor) to preserve the <10¢ band.
    ENTRY_PRICE_DEAD_ZONE = (0.10, 0.20)

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()
        self._last_pinnacle_yes_prob: Optional[float] = None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        prob_yes = self._last_pinnacle_yes_prob if self._last_pinnacle_yes_prob is not None else fair_prob

        best_signal = None
        best_edge = -1.0
        for side, price, side_prob in (
            ("yes", kalshi_yes, prob_yes),
            ("no", kalshi_no, 1 - prob_yes),
        ):
            if not (self.PRICE_MIN <= price <= self.PRICE_MAX):
                continue
            fee_pct = min(fee, price, 1 - price) / price if price > 0 else 0
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff < self.PAYOFF_MIN:
                continue
            edge = side_prob - cost
            if edge < self.MIN_EDGE:
                continue
            if edge > best_edge:
                best_signal = {"side": side, "price": price, "edge": edge,
                               "strategy": "v4_edge"}
                best_edge = edge
        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        return min(3.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        source = "pin" if self._last_pinnacle_yes_prob is not None else "cons"
        return (f"[V4-EDGE] {ticker}: {title[:40]} "
                f"src={source} ask={ya:.2f}/{na:.2f} edge={signal['edge']:.3f} "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
