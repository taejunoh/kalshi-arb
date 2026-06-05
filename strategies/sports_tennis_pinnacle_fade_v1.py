"""Tennis Pinnacle FADE v1 — paper-only POC.

Built 2026-05-26 from cross-sport Pinnacle-move backtest finding: in ATP+WTA,
following Pinnacle line movement is strongly -EV (≈-$0.21/$1), so fading
the move flips to +EV after subtracting 2× Kalshi fee.

⚠️ MIRROR-DEDUP CORRECTION (2026-05-26 EoD audit):
Original backtest counted each Kalshi ticker independently. In tennis,
each match has TWO mirror tickers (e.g. KXATPMATCH-...-SHA + ...-FAR),
both qualifying for the fade signal when |move| ≥ 3pp on either side.
Mirror pairs ALWAYS agree on win/loss (20/20 agreement in 60d data),
so n=48 in original drill is actually ~n=24 unique match events with
2× row inflation. Re-running with PAIR_FIRE_DEDUP semantics:

  Tightest (|move|∈[3,8]pp, ya∈[0.40,0.70]):
    Per-ticker (original):   n=55, WR 67.3%, EV +$0.108/$1
    Per-unique-event:        n=35, WR 62.9%, EV +$0.068/$1   ← truth
      ATP: n=22 WR 54.5% EV -$0.017   (≈ 0)
      WTA: n=13 WR 76.9% EV +$0.212   (all within last 14d = Roland Garros)

  → Strategy is essentially WTA-only edge, concentrated in current
    Roland Garros draw. ATP standalone is ≈ 0 EV. Outside RG, no WTA
    out-of-sample. **Live runs with PAIR_FIRE_DEDUP=True so each match
    is bet only once**.

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
  - WTA-only edge — ATP n=22 standalone is flat. If WTA RG-bias confirmed,
    consider WTA-only post-promotion.
  - n=35 unique events over 60d ≈ 0.6 unique-events/day; at $4 paper
    that's ~$2.4/day notional.
  - Strategy requires historical pinnacle snapshots (open ≥4h pre-game,
    close ≤4h pre-game) — fires only after both windows have data.

PROMOTION GATE (paper → LIVE):
  - 21d forward, n ≥ 25 unique events (NOT raw tickers), net positive, WR ≥ 60%
  - WTA EV must remain > +$0.10/$1; ATP can be flat
  - If WTA edge collapses after Roland Garros ends (~6/08), abandon
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

    # Tennis matches have 2 mirror tickers (-SHA + -FAR for a SHA-vs-FAR
    # match) that ALWAYS agree on win/loss. Without dedup, every signal
    # fires twice → 2× exposure per unique event, n inflation in eval.
    # Backtest re-analysis 2026-05-26: 20 of 35 unique events have both
    # mirrors qualify; per-ticker n=55 → unique-event n=35.
    PAIR_FIRE_DEDUP = True

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
        return min(self.BASE_SIZE_USDC, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

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
