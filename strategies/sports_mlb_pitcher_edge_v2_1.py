"""MLB pitcher edge v2.1 — adds book-depth gate to v2.

Derived from 2026-05-26 7-signal × 127-combo backtest. v2 (era≥2.5 only)
backtests at n=20 +$0.076/$1; v2.1 (era≥2.0 + book-depth ≥10) backtests at
n=47 +$0.041/$1. Same direction (bet better-ERA team YES); trades sharper
per-trade edge for 2.4× sample size → better statistical power for the
6/02 paper eval.

Backtest finding: BOOKS_DEEP alone is -$0.043 (deep-consensus games are
efficiently priced). ERA_DIFF_2 alone is +$0.001 (signal too weak). Their
interaction is +$0.041 — the ERA edge is only exploitable in deep-book
markets where idiosyncratic noise is filtered out.

Coexists with v1 (look-ahead biased baseline) and v2 (era≥2.5) for 3-way
A/B during paper observation.

See [[mlb_signal_combo_backtest_2026-05-26]].
"""
import logging
import os
import sqlite3
from typing import Optional

from strategies.sports_mlb_pitcher_edge_v2 import SportsMlbPitcherEdgeV2Strategy

logger = logging.getLogger("sports_mlb_pitcher_edge_v2_1")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsMlbPitcherEdgeV21Strategy(SportsMlbPitcherEdgeV2Strategy):
    """v2 + book-depth gate (n_books_eu + n_books_us ≥ MIN_BOOKS_TOTAL)."""

    TRADED_TICKERS_FILE = "data/sports_mlb_pitcher_edge_v2_1_tickers.json"
    STRATEGY_NAME = "sports_mlb_pitcher_edge_v2_1"
    BADGE_LABEL = "MLB-P21"
    BADGE_COLOR = "#0369a1"

    # Looser ERA gate than v2's 2.5 — combo backtest sweet spot is ≥2.0
    # WHEN paired with BOOKS_DEEP.
    MIN_ERA_DIFF = 2.0

    # NEW: minimum total bookmaker count across regions in latest pre-game
    # snapshot. 10 = the threshold tested in combo backtest.
    MIN_BOOKS_TOTAL = 10

    # 2026-08-19 ASK FLOOR 0.40 → 0.45. Same by-band audit as
    # sports_mlb_pitcher_edge_v1 (see the MIN_YES_PRICE comment there for the
    # full cell table and the caveats). v2's `evaluate` gates on
    # `MIN_KALSHI_PRICE <= kalshi_yes <= MAX_KALSHI_PRICE`, i.e. the ask —
    # the same quantity the audit measured.
    #
    # DELIBERATELY ON v2_1, NOT ON THE PARENT. Same reasoning as the
    # `_get_booking_price` override below: the parent runs as its own
    # PAPER-ONLY strategy (`sports_mlb_pitcher_edge_v2`) and is not in
    # LIVE_STRATEGIES. Leaving it at 0.40 keeps the 35-45c band firing on
    # paper, which is what turns v2 into the control group for this cut —
    # without it there is no forward data to falsify the decision with.
    MIN_KALSHI_PRICE = 0.45

    # 2026-05-26 stake-sizing-by-edge upgrade. Backtest split by era_diff:
    #   era 2.0-2.5: EV -$0.058 (n=16) ← keep but de-emphasized
    #   era 2.5-3.0: EV +$0.102 (n=14)
    #   era 3.0-3.5: EV ~+$0.04  (n=10)
    #   era 3.5+   : EV +$0.31  (n=7)
    # Scaling stake by tier improves total EV/$1 from +$0.041 → +$0.059
    # (STEP scheme) without changing fire frequency.
    SIZE_TIER_LOW = 4.0      # era_diff [2.0, 2.5)
    SIZE_TIER_MID = 6.0      # era_diff [2.5, 3.5)
    SIZE_TIER_HIGH = 8.0     # era_diff ≥ 3.5

    def _get_trade_size(self, signal: dict) -> float:
        """Tier-based stake by era_diff (from `edge` field in signal).

        Backtest finding: era_diff 2.0-2.5 is -EV cohort that drags v2.1 down.
        Don't skip (preserves sample n for forward eval) but de-emphasize via
        smaller stake; concentrate capital on era_diff ≥ 3.5 high-conviction tier.
        Bankroll cap (2%) preserved as safety upper bound.
        """
        edge = signal.get("edge", 0.0) or 0.0
        if edge >= 3.5:
            base = self.SIZE_TIER_HIGH
        elif edge >= 2.5:
            base = self.SIZE_TIER_MID
        else:
            base = self.SIZE_TIER_LOW
        return min(base, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    # BOOKKEEPING (2026-07-22) — same fair-price artifact as tennis_live_fade.
    # The signal comes from `super().evaluate()` (v2), which is unconditionally
    # side="yes" with `"price": kalshi_yes` = `yes_ask_dollars` and is gated on
    # the ASK (`MIN_KALSHI_PRICE <= kalshi_yes <= MAX_KALSHI_PRICE`, 0.40-0.75);
    # v2_1 only adds a book-depth veto and relabels, it never rewrites `price`.
    # So the ask IS the honest taker price. The inherited `_get_limit_price`
    # limits at consensus fair, which the paper twin was booking as an instant
    # fill — a price the book never showed. As with v1 the edge is an ERA
    # differential, not a price-vs-fair gap, so the artifact is bidirectional.
    #
    # DELIBERATELY ON v2_1, NOT ON THE PARENT `SportsMlbPitcherEdgeV2Strategy`.
    # The parent runs as its own strategy (`sports_mlb_pitcher_edge_v2`,
    # instantiated + scan-looped in main.py) and is PAPER-ONLY — it is not in
    # LIVE_STRATEGIES and not in PAPER_TWIN_STRATEGIES. Overriding on the
    # parent would silently re-base a fifth ledger that is outside the approved
    # scope of this fix and would put an unannotated structural break in its
    # paper series. v2 has the same artifact and should get the same override
    # in a follow-up, with its own cutoff annotation.
    #
    # ONLY the booking price changes here. `_get_limit_price` stays inherited —
    # this is a LIVE strategy and that method sets the real Kalshi limit and
    # feeds the entry-price gates. DO NOT convert this into a
    # `_get_limit_price` override without an explicit live-behavior decision.
    def _get_booking_price(self, signal: dict, fair_prob: float,
                           limit_price: float) -> float:
        return signal["price"]

    def _check_books_deep(self, ticker: str) -> bool:
        """True iff latest odds_observations row for ticker has
        n_books_eu + n_books_us ≥ MIN_BOOKS_TOTAL.

        Fail-closed on DB error — safer than firing on missing data.
        """
        try:
            conn = sqlite3.connect(DB)
            conn.execute("PRAGMA query_only = 1")
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute("""
                SELECT n_books_eu, n_books_us FROM odds_observations
                WHERE kalshi_ticker = ?
                  AND minutes_to_start > 0
                ORDER BY timestamp DESC
                LIMIT 1
            """, (ticker,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return False
            nb_eu = row[0] or 0
            nb_us = row[1] or 0
            return (nb_eu + nb_us) >= self.MIN_BOOKS_TOTAL
        except Exception as e:
            logger.warning(f"books_deep check failed for {ticker}: {e}")
            return False

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        signal = super().evaluate(
            kalshi_yes, kalshi_no, fair_prob,
            line_movement=line_movement, home_adj=home_adj, ticker=ticker,
        )
        if signal is None:
            return None
        if not self._check_books_deep(ticker):
            return None
        signal["strategy"] = "mlb_pitcher_edge_v2_1"
        signal["ticker"] = ticker
        return signal
