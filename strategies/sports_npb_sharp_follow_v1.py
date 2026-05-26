"""NPB Pinnacle sharp-FOLLOW v1 — paper-only POC.

Built 2026-05-26 from cross-sport Pinnacle-move backtest finding:

Drill-down 60d NPB sharp-follow (PIT):
  All NPB |move|≥3pp:                          n=62, WR 64.5%, EV +$0.045/$1
  |move|∈[3,8]pp + ya∈[0.30,0.70] (FINAL):     n=33, WR 78.8%, EV +$0.175/$1 ⭐⭐
  Direction asymmetry:
    UP→YES (move up, bet favorite):  n=31 EV -$0.058 (LOSES)
    DN→NO (move down, bet underdog): n=31 EV +$0.149 (WINS)

THESIS: In NPB pre-game, when Pinnacle moves DOWN on a side's win prob,
that side is being correctly downgraded. Buying the OTHER side (NO on
the downgraded side = YES on the opponent at low ya converted to high
no_ask) captures the correction. UP moves on a side are over-reactions
to lineup announcements / public sentiment that don't pan out.

For v1 we run BOTH directions (n=33 combined +$0.175) — the asymmetry
is real but each direction's n is still small enough that one-direction-only
loses defensive coverage. Future v2 could split by direction if forward
data confirms.

KEY EXCLUSIONS (data-driven):
  - |move| > 8pp: inverts to -$0.171 (n=8) and -$0.298 (12pp+, n=6). Real
    information beyond 8pp; sharp-follow is too late.
  - ya > 0.70 OR ya < 0.30: extreme prices wash out the signal.

CAVEATS:
  - NPB Kalshi market liquidity historically thinner than MLB. Forward
    fills may face wider spreads.
  - Japanese baseball season runs ~March → October. We're mid-season now;
    promotion timing safe.

PROMOTION GATE (paper → LIVE):
  - 21d forward, n ≥ 25, net positive, WR ≥ 65%
  - Both directions (UP & DOWN) contribute non-trivially (each ≥30% of fires)
  - Kalshi NPB book depth check (avg n_books ≥ 8) before promotion
"""
import logging
import os
import sqlite3
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_npb_sharp_follow_v1")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsNpbSharpFollowV1Strategy(SportsTraderStrategy):
    """FOLLOW Pinnacle line moves on mid-priced NPB matches."""

    TRADED_TICKERS_FILE = "data/sports_npb_sharp_follow_v1_tickers.json"
    STRATEGY_NAME = "sports_npb_sharp_follow_v1"
    BADGE_LABEL = "NPB-SF"
    BADGE_COLOR = "#dc2626"

    # NPB is a TEAM sport — no individual-sport opt-in. But it's in parent's
    # OBSERVATION_ONLY_SPORTS by default — must subtract it to actually trade.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"NPB"}
    )
    # Block everything except NPB.
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC",
                                  "ATP", "WTA"})
    PLAYOFF_BLOCKLIST = frozenset()
    PRE_GAME_ONLY = True

    # Faster cadence — NPB games cluster in tight evening windows JST.
    SCAN_INTERVAL_OVERRIDE = 60.0

    # Signal gates (from drill-down).
    MIN_MOVE_PP = 0.03
    MAX_MOVE_PP = 0.08
    MIN_YA = 0.30
    MAX_YA = 0.70
    OPEN_MIN_MTS = 240
    BASE_SIZE_USDC = 4.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.bankroll * 0.02)

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

        # FOLLOW direction: move up → bet YES, move down → bet NO
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
            "strategy": "npb_sharp_follow_v1",
            "ticker": ticker,
        }
