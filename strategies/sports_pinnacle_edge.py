"""Sports Pinnacle Edge Strategy — bet when Kalshi diverges from Pinnacle by 5%+."""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_pinnacle_edge")


class SportsPinnacleEdgeStrategy(SportsTraderStrategy):
    """Edge-based bet: `edge = pin_prob - kalshi_ask × (1 + fee_pct) >= 0.05`.

    No fallback to consensus — only fires when Pinnacle is present in the
    Odds API response (requires regions=eu). This isolates Pinnacle's
    predictive power from the wider bookmaker consensus.
    """

    TRADED_TICKERS_FILE = "data/sports_pinnacle_edge_tickers.json"
    STRATEGY_NAME = "sports_pinnacle_edge"
    MIN_EDGE = 0.05

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()
        self._last_pinnacle_yes_prob: Optional[float] = None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        pin_yes = self._last_pinnacle_yes_prob
        if pin_yes is None:
            return None
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        yes_fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes if kalshi_yes > 0 else 0
        no_fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no if kalshi_no > 0 else 0
        yes_edge = pin_yes - kalshi_yes * (1 + yes_fee_pct)
        no_edge = (1 - pin_yes) - kalshi_no * (1 + no_fee_pct)

        best_side, best_edge, best_price = None, -1.0, 0.0
        if yes_edge >= self.MIN_EDGE and yes_edge > best_edge:
            best_side, best_edge, best_price = "yes", yes_edge, kalshi_yes
        if no_edge >= self.MIN_EDGE and no_edge > best_edge:
            best_side, best_edge, best_price = "no", no_edge, kalshi_no
        if best_side is None:
            return None

        size = self._get_trade_size({"side": best_side, "price": best_price})
        if not self._has_sufficient_depth(ticker, best_side, size):
            return None

        return {"side": best_side, "price": best_price, "edge": best_edge,
                "strategy": "pinnacle_edge"}

    def _get_trade_size(self, signal: dict) -> float:
        return min(3.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        pin = self._last_pinnacle_yes_prob
        return (f"[PIN-EDGE] {ticker}: {title[:40]} "
                f"pin_yes={pin:.2f} ask={ya:.2f}/{na:.2f} edge={signal['edge']:.3f} "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
