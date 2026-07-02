"""MLB live-phase FOLLOW-Pinnacle mid-band (paper-only Phase 0/1).

Built 2026-07-02 from the honest ask-fill replay that exposed the
tennis_live_fade paper-PnL artifact (fair-price instant-fill bookkeeping,
see memory tennis_live_fade_paper_pnl_artifact_2026-07-02). This is the
INVERSE axis: in-play, when Pinnacle's implied prob EXCEEDS a side's Kalshi
ask by >= 12pp on a mid-priced side, BUY that side at the ask — follow the
sharp instead of fading it.

30d first-crossing replay at the OBSERVED ASK (fee = 0.07*p*(1-p),
one fire per game, mirror-dedup):

  MLB live 0.50-0.75, gap>=0.12:  n=47  WR 63.8%  +$3.06/contract-$  ROI +11.7%
  gap monotonicity (same band):   0.05 -> -7.6%, 0.08 -> +5.2%, 0.12 -> +11.7%
  half-month splits:              6A +0.37 / 6B +2.86 / 7A -0.29 (n=2)
  adjacent 0.30-0.50 gap 0.12:    +1.7% (n=171) — positive but thin; NOT
  included at launch (corner-cell discipline: only the monotone cell ships).

Reliability ★2-3 (backtest-only, n<50) -> PAPER-ONLY. Purpose is to upgrade
the tier via an honest forward ledger, not to trade real money. Promotion
bar: n>=50 paper-settled AND half-splits consistent AND pooled Wilson-LB > BE
(BE-WR ~= 60.5% at the band's ~0.575 avg price incl. fee).

BOOKKEEPING (the live_fade lesson): _get_limit_price is overridden to return
signal["price"] — the ask the signal actually saw. The parent default books
fair-based prices that never existed on the book for follow/fade-style
entries (root cause of the live_fade +$193/30d paper artifact). DO NOT
remove this override.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_mlb_live_pin_follow_v1")


class SportsMlbLivePinFollowV1Strategy(SportsTraderStrategy):
    """BUY the MLB in-play side whose ask sits >=12pp under fresh Pinnacle,
    mid-band (0.50-0.75) only."""

    TRADED_TICKERS_FILE = "data/sports_mlb_live_pin_follow_v1_tickers.json"
    STRATEGY_NAME = "sports_mlb_live_pin_follow_v1"
    BADGE_LABEL = "MPF"
    BADGE_COLOR = "#22c55e"

    # Cell matrix: MLB live only — subsumes PRE_GAME_ONLY / SPORT_BLOCKLIST.
    ALLOWED_CELLS = frozenset({("MLB", "live")})
    # Band whitelist on signal price: replay alpha lives in 0.50-0.75; the
    # 0.30-0.50 neighbour was +1.7% (n=171) — re-eval for a sister strategy
    # once this cell validates forward (sister, NOT a gate widen).
    CELL_PRICE_BANDS = {("MLB", "live"): [(0.50, 0.75)]}

    # One fire per game (mirror legs are one bet economically).
    PAIR_FIRE_DEDUP = True

    # Replay-tested gate: gap 0.12 is where ROI turns clearly positive and
    # the gap-monotonicity holds. 0.05/0.08 are NOT +EV — don't loosen.
    MIN_PIN_GAP = 0.12
    # Match the replay's freshness filter (same as tennis_live_fade).
    MAX_PIN_AGE_S = 600

    def _get_trade_size(self, signal: dict) -> float:
        # Paper-only launch tier — same $4 cap convention as other Phase 0/1
        # paper strategies. Sizing decisions happen at promotion, not here.
        return min(4.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.01)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        # Honest booking: paper twin must record the ask the signal saw.
        # Parent default (fair-based) fabricates fills for follow/fade
        # entries — see module docstring. DO NOT remove.
        return signal["price"]

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire when fresh Pinnacle prices a side >=12pp ABOVE its Kalshi ask.

        Follow-the-sharp: the replay shows Kalshi mid-band in-play prices
        lag Pinnacle when they disagree hard — opposite of the (refuted)
        live_fade thesis at the >=0.85 chalk band. Band gating (0.50-0.75)
        is enforced by CELL_PRICE_BANDS in scan_and_execute.
        """
        pin = self._last_pinnacle_yes_prob
        pin_age = self._last_pinnacle_age_s

        if pin is None or pin_age is None or pin_age > self.MAX_PIN_AGE_S:
            return None

        candidates = []
        if 0 < kalshi_yes < 1:
            gap = pin - kalshi_yes
            if gap >= self.MIN_PIN_GAP:
                candidates.append({"side": "yes", "price": kalshi_yes,
                                   "edge": gap, "strategy": "mlb_live_pin_follow",
                                   "ticker": ticker})
        if 0 < kalshi_no < 1:
            gap = (1.0 - pin) - kalshi_no
            if gap >= self.MIN_PIN_GAP:
                candidates.append({"side": "no", "price": kalshi_no,
                                   "edge": gap, "strategy": "mlb_live_pin_follow",
                                   "ticker": ticker})

        if not candidates:
            return None
        return max(candidates, key=lambda c: c["edge"])
