"""Tennis Pinnacle FADE v1 — paper-only POC.

Built 2026-05-26 from cross-sport Pinnacle-move backtest finding: in ATP+WTA,
following Pinnacle line movement is strongly -EV (≈-$0.21/$1), so fading
the move flips to +EV after subtracting 2× Kalshi fee.

Drill-down 60d backtest (PIT, FADE direction):

  All ATP+WTA (|move|≥3pp):                  n=124, WR 54.8%, EV +$0.068/$1
  Tighter (|move|∈[3,8]pp, ya∈[0.30,0.70]):  n=64,  WR 70.3%, EV +$0.146/$1
  Tightest (|move|∈[3,8]pp, ya∈[0.40,0.70]): n=48,  WR ~81%,  EV ~+$0.24/$1
    - WTA subset n=22 WR 86.4% EV +$0.321 (strong but n=22 — variance risk)
    - ATP subset n=42 WR 61.9% EV +$0.054

KEY EXCLUSIONS:
  - |move| > 8pp: FADE inverts to -EV (8-12pp band: ATP -$0.088, WTA -$0.026).
    Large moves carry real information (lineup leak, withdrawal, late tour
    news) — Pinnacle correctly adjusted, fading loses.
  - ya < 0.30 or > 0.70: market priced at extremes already; fade signal
    washes out in noise/structural mispricing of long-tail prices.

THESIS: Pinnacle moves in ATP/WTA mid-priced matches are dominated by
recreational money chasing public narratives (top-N seed beat earlier,
"upset alert", live-stream chatter) rather than sharp information. Kalshi
(less sophisticated tennis book) follows but with lag and noise; the FADE
captures the over-reaction once both books re-equilibrate to ground truth.

CAVEATS:
  - WTA edge concentrated in n=22 (Roland Garros windowed) — could be
    tournament-specific. Forward eval will reveal.
  - n=48 over 60d ≈ 0.8 fires/day; at $4 paper that's ~$3/day notional.
  - Strategy requires historical pinnacle snapshots (open ≥4h pre-game,
    close ≤4h pre-game) — fires only after both windows have data.

PROMOTION GATE (paper → LIVE):
  - 21d forward, n ≥ 25, net positive, WR ≥ 65%
  - Both ATP and WTA must contribute (not driven by one tour alone)
"""
import logging
import os
import sqlite3
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_tennis_pinnacle_fade_v1")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsTennisPinnacleFadeV1Strategy(SportsTraderStrategy):
    """FADE Pinnacle line moves on mid-priced ATP/WTA matches."""

    TRADED_TICKERS_FILE = "data/sports_tennis_pinnacle_fade_v1_tickers.json"
    STRATEGY_NAME = "sports_tennis_pinnacle_fade_v1"
    BADGE_LABEL = "TPF"
    BADGE_COLOR = "#7c3aed"

    INDIVIDUAL_SPORTS_OPT_IN = True
    # Subtract ATP/WTA from parent's OBSERVATION_ONLY_SPORTS so evaluate() runs.
    OBSERVATION_ONLY_SPORTS = (
        SportsTraderStrategy.OBSERVATION_ONLY_SPORTS - {"ATP", "WTA"}
    )
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC"})
    PLAYOFF_BLOCKLIST = frozenset()
    PRE_GAME_ONLY = True

    # Faster cadence than 300s parent default — tennis match windows are
    # short and Pinnacle move can complete in 1-2h.
    SCAN_INTERVAL_OVERRIDE = 60.0

    # Backtest gate boundaries.
    MIN_MOVE_PP = 0.03   # Pinnacle move magnitude (open → close) lower bound
    MAX_MOVE_PP = 0.08   # upper bound — beyond this, FADE inverts to -EV
    MIN_YA = 0.40
    MAX_YA = 0.70

    # Open-snapshot threshold: how far pre-game the "open" Pinnacle is read.
    # Backtest used ≥4h. Match anchors here so live behaviour mirrors.
    OPEN_MIN_MTS = 240

    BASE_SIZE_USDC = 4.0

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.bankroll * 0.02)

    def _open_pinnacle(self, ticker: str) -> Optional[float]:
        """Earliest pre-game odds_observations pinnacle_yes_prob with
        minutes_to_start >= OPEN_MIN_MTS. Returns None if no qualifying row."""
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
        # Use parent-stashed close pinnacle (current snapshot's pinnacle_yes_prob).
        close_pin = getattr(self, "_last_pinnacle_yes_prob", None)
        if close_pin is None:
            return None
        # ya gate
        if kalshi_yes is None or not (self.MIN_YA <= kalshi_yes <= self.MAX_YA):
            return None
        # Open snapshot
        open_pin = self._open_pinnacle(ticker)
        if open_pin is None:
            return None
        move = close_pin - open_pin
        abs_move = abs(move)
        # Magnitude gate: only the [3pp, 8pp] sweet spot
        if not (self.MIN_MOVE_PP <= abs_move <= self.MAX_MOVE_PP):
            return None

        # FADE direction
        if move > 0:
            # Pinnacle moved UP → market thinks YES more likely → fade by betting NO
            side = "no"
            price = kalshi_no if kalshi_no is not None else (1.0 - kalshi_yes)
        else:
            side = "yes"
            price = kalshi_yes

        return {
            "side": side,
            "price": price,
            "edge": abs_move,
            "strategy": "tennis_pinnacle_fade_v1",
            "ticker": ticker,
        }
