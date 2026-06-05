"""LaLiga (LAL) Pinnacle sharp-FOLLOW v1 — paper-only POC.

Built 2026-05-26 from cross-sport Pinnacle-move backtest finding:

Drill-down 60d LAL sharp-follow (PIT):
  All LAL |move|≥3pp:                     n=42, WR 64.3%, EV +$0.056/$1
  |move|∈[3,5]pp (sweet spot):            n=31, WR 67.7%, EV +$0.094/$1 ⭐
  |move|∈[3,8]pp + ya∈[0.30,0.70]:        n=27, WR 63.0%, EV +$0.082
  ya 0.5-0.7:                              n=6, WR 83.3%, EV +$0.203 (n small)

THESIS: LaLiga is a heavily-modeled soccer league where Pinnacle absorbs
information faster than Kalshi (less specialized soccer book). Small
Pinnacle moves (3-5pp) in pre-game reflect genuine info absorption that
Kalshi will gradually mirror but hasn't yet — following captures the
lag at entry.

KEY EXCLUSIONS (data-driven):
  - |move| > 5pp: drops to -$0.070 in 5-8pp (n=8), -$0.003 in 8-12pp (n=3).
    Beyond 5pp the move is either over-reaction OR the move has already
    been absorbed by Kalshi (no residual edge).
  - ya extremes (<0.30 or >0.70): cohort too thin (n=11 + n=2) for
    confident inclusion. Mid-range only.

CAVEATS:
  - **LaLiga 2025-26 season ends late May / June 2026**. Window is narrow.
    Forward eval will lap into 2026-27 preseason (gap July). Promotion
    decision may need to wait until 2026-27 regular season.
  - Edge per trade modest (+$0.094). Variance high (soccer 3-way market).

PROMOTION GATE (paper → LIVE):
  - 21d forward, n ≥ 20 (lower than NPB due to season-ending), WR ≥ 60%
  - Cross-season validation: re-confirm in 2026-27 regular season
    (start ~mid-August 2026)
"""
import logging
import os
import sqlite3
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_lal_sharp_follow_v1")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsLalSharpFollowV1Strategy(SportsTraderStrategy):
    """FOLLOW Pinnacle line moves on mid-priced LaLiga matches."""

    TRADED_TICKERS_FILE = "data/sports_lal_sharp_follow_v1_tickers.json"
    STRATEGY_NAME = "sports_lal_sharp_follow_v1"
    BADGE_LABEL = "LAL-SF"
    BADGE_COLOR = "#ea580c"

    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"LAL"}
    )
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "BUN", "UCL", "UFC",
                                  "ATP", "WTA"})
    PLAYOFF_BLOCKLIST = frozenset()
    PRE_GAME_ONLY = True

    SCAN_INTERVAL_OVERRIDE = 60.0

    # Tighter move band than NPB — 3-5pp is LAL's sweet spot.
    MIN_MOVE_PP = 0.03
    MAX_MOVE_PP = 0.05
    MIN_YA = 0.30
    MAX_YA = 0.70
    OPEN_MIN_MTS = 240
    BASE_SIZE_USDC = 4.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    def _open_pinnacle(self, ticker: str) -> Optional[float]:
        try:
            conn = sqlite3.connect(DB)
            conn.execute("PRAGMA query_only = 1")
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute("""
                SELECT pinnacle_yes_prob FROM odds_observations
                WHERE kalshi_ticker = ?
                  AND minutes_to_start >= ?
                  AND pinnacle_yes_prob IS NOT NULL
                ORDER BY minutes_to_start DESC
                LIMIT 1
            """, (ticker, self.OPEN_MIN_MTS))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.debug(f"open pinnacle fetch {ticker}: {e}")
            return None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        close_pin = getattr(self, "_last_pinnacle_yes_prob", None)
        if close_pin is None:
            return None
        if kalshi_yes is None or not (self.MIN_YA <= kalshi_yes <= self.MAX_YA):
            return None
        open_pin = self._open_pinnacle(ticker)
        if open_pin is None:
            return None
        move = close_pin - open_pin
        abs_move = abs(move)
        if not (self.MIN_MOVE_PP <= abs_move <= self.MAX_MOVE_PP):
            return None

        if move > 0:
            side = "yes"
            price = kalshi_yes
        else:
            side = "no"
            price = kalshi_no if kalshi_no is not None else (1.0 - kalshi_yes)

        return {
            "side": side,
            "price": price,
            "edge": abs_move,
            "strategy": "lal_sharp_follow_v1",
            "ticker": ticker,
        }
