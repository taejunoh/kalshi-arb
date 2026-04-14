"""Sports Liquidity Filtered — midshot logic with orderbook depth gate."""
import logging
from typing import Optional

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_liquidity_filtered")


class SportsLiquidityFilteredStrategy(SportsMidshotStrategy):
    """Isolates the effect of the depth filter — identical midshot logic
    plus a hard requirement that the orderbook on the chosen side has at
    least `size_usdc` of resting liquidity.
    """

    TRADED_TICKERS_FILE = "data/sports_liquidity_filtered_tickers.json"
    STRATEGY_NAME = "sports_liquidity_filtered"
    REGION_OVERRIDE = "eu"  # V4 strategies all use EU sharp-book consensus

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        signal = super().evaluate(kalshi_yes, kalshi_no, fair_prob,
                                  line_movement=line_movement,
                                  home_adj=home_adj, ticker=ticker)
        if signal is None:
            return None
        size = self._get_trade_size(signal)
        if not self._has_sufficient_depth(ticker, signal["side"], size):
            return None
        signal["strategy"] = "liquidity_filtered"
        return signal

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        return (f"[LIQ-FLTR] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} payoff={signal['edge']:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
