"""Sports Chalk Shadow — paper-only experimental strategy for live-game chalk buy.

Motivation (2026-04-24 observer analysis, n=88 settled tickers):
  MLB live 75¢+ YES ask: 16/16 wins (100%)
  MLB live 60-75¢ YES ask: 8/10 wins (80%)
  NHL live 60-75¢ YES ask: 5/5 wins (100%)
  NBA live 75¢+ YES ask: 3/3 wins (100%)

Hypothesis: in live-game Kalshi markets, prices that have moved above 60%
reflect near-decided game state. Observer-hypothetical ROI on "buy YES
at observed chalk price" shows +10-25% per win after fees. Meanwhile
the **same price band in pre-game** hypothetically loses 19-22% ROI
(Kalshi overprices favorites pre-game — this is what underdog_v31
already fades profitably).

**Live-only** is critical. Pre-game chalk is a trap.

Caveats (why paper-only, not live):
  - Small sample (MLB live 75¢+ n=16 is the largest bucket)
  - Selection bias concern: 75¢+ prices are observed when game is near-
    decided. Residual uncertainty (~5-10%) is exactly what Kalshi prices in.
  - Thin book depth at extreme prices → real slippage could eat the margin.

Paper shadow for 2-4 weeks to accumulate honest n and compare live
paper ROI vs observer hypothetical ROI before any live-promotion call.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_chalk_shadow")


class SportsChalkShadowStrategy(SportsTraderStrategy):
    """Paper-only live-game chalk buyer: enter YES (or NO) when ask is
    60-95¢ during live play. Tests observer-derived hypothesis that live
    chalk reflects near-decided games that Kalshi residually underprices.
    """

    TRADED_TICKERS_FILE = "data/sports_chalk_shadow_tickers.json"
    STRATEGY_NAME = "sports_chalk_shadow"

    PRICE_MIN = 0.60
    PRICE_MAX = 0.95

    # Live-only via cell matrix — pre-game at this price band actively loses
    # money (see observer pre-chalk analysis: MLB -19%, NHL -22% ROI).
    ALLOWED_CELLS = frozenset({
        ("MLB", "live"), ("NBA", "live"), ("NHL", "live"),
    })

    # Depth gate disabled (2026-04-24) so we can test the observer-derived
    # hypothesis on the FULL candidate set without a liquidity filter. Chalk
    # 75¢+ markets often have thin opposite-side depth (few want to sell a
    # near-sure winner), which would silently drop most signals if we gated
    # at 5 USDC. Post-hoc analysis can still filter by depth because the
    # base class _record_entry_clv snapshots kalshi_yes_depth/kalshi_no_depth
    # into clv_snapshots at every entry. Before any live promotion, switch
    # MIN_DEPTH_USDC to >=5 and measure what fraction of paper entries
    # would have been gated out.
    MIN_DEPTH_USDC = 0.0

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Pick whichever side is in the chalk band. Only one should qualify
        per market since YES + NO ≈ 1.0 — the side ≥0.60 is 'chalk', the
        other is 'dog' at ≤0.40. Defensive: if both are in range (rare,
        high vig), pick the higher-priced one."""
        best_signal = None
        best_price = -1.0
        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (self.PRICE_MIN <= price <= self.PRICE_MAX):
                continue
            if price > best_price:
                best_signal = {"side": side, "price": price,
                               "strategy": "chalk_shadow"}
                best_price = price
        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        """Same base sizing as v31/v34/v4_edge: $4 or 2% of bankroll."""
        return min(4.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        return (f"[CHALK-SHADOW] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} → {signal['side']} "
                f"${size:.2f} LIMIT@{limit_price:.2f}")
