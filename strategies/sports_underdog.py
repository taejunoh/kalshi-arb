"""Sports Underdog Strategy — bet against consensus favorite."""
import json
import logging
import os
import time
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_underdog")


class SportsUnderdogStrategy(SportsTraderStrategy):
    """Bet on the underdog when consensus favorite is strong. Exploits
    systematic overpricing of favorites in prediction markets."""

    TRADED_TICKERS_FILE = "data/sports_underdog_tickers.json"
    STRATEGY_NAME = "sports_underdog"
    BADGE_LABEL = "V3"
    BADGE_COLOR = "#a855f7"
    REGION_OVERRIDE = "us"  # V3 baseline stays on US retail books for continuity
    SCAN_INTERVAL_OVERRIDE = 120.0  # us retail books tick 60-90s; 120s scan captures movement
    # 2026-05-11: Thin-book early-fire gate. Drops trades placed >=12h
    # before game with book depth <$500 USD. Empirical 4/15-5/11 across
    # underdog family: 12-24h × <500 ROI -100%, >24h × <500 ROI -250%.
    # Cascades to v31, v32, v34, v35, v36_*, v37, v38; only v31/v32/v34
    # actually drop trades (1-3 each) in current data; v36/v37/v38 inherit
    # but their universes have deeper books → 0 drops.
    THIN_BOOK_EARLY_GATE_MTS_MIN_MINUTES = 720.0  # 12 hours
    THIN_BOOK_EARLY_GATE_DEPTH_MAX_USDC = 500.0

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        # Re-load with our own file
        self._traded_tickers = {}
        self._load_traded_tickers()

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Bet against consensus favorite when payoff >= 2x."""
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        # Consensus says YES team is favorite (>60%), bet NO (underdog)
        if fair_prob > 0.60 and 0.05 <= kalshi_no <= 0.40:
            fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no
            payoff = 1.0 / (kalshi_no * (1 + fee_pct))
            if payoff >= 2.0:
                return {"side": "no", "price": kalshi_no,
                         "edge": payoff, "strategy": "underdog"}

        # Consensus says NO team is favorite (fair_prob < 40%), bet YES (underdog)
        elif fair_prob < 0.40 and 0.05 <= kalshi_yes <= 0.40:
            fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes
            payoff = 1.0 / (kalshi_yes * (1 + fee_pct))
            if payoff >= 2.0:
                return {"side": "yes", "price": kalshi_yes,
                         "edge": payoff, "strategy": "underdog"}

        return None

    def _get_trade_size(self, signal: dict) -> float:
        """Small fixed size for portfolio approach.
        Raised 3→4 on 2026-04-20 post dead-zone rollout."""
        return min(4.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        """Buy at ask price."""
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        payoff = signal.get("edge", 0)
        return (f"[UNDERDOG] {ticker}: {title[:40]} "
                f"fair={fair_prob:.2f} ask={ya:.2f}/{na:.2f} payoff={payoff:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
