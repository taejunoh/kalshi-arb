"""Sports V4 Edge — V3 midshot price filter AND Pinnacle/consensus edge gate.

Cell + price band gates revised 2026-04-26 (live promotion 1-week eval):
  - Pre-fix global gate (PRICE_MIN/MAX/DEAD_ZONE) hid one alpha cell
    (MLB live 20-30¢ +25.2% n=30) under 4 losing cells, net -39.7% n=84.
  - Switched to per-cell ALLOWED_CELLS + CELL_PRICE_BANDS whitelist below.
  - NBA live blocked entirely (uniformly negative across all bands).
  - NHL live narrowed to small +ROI bands only (<10¢ and 30-35¢, both n=4).
  - <10¢ bands kept under observation (MLB pre-fix +280% n=7 → live -100%
    n=26; user opted to keep both NHL and MLB <10¢ for sample accumulation).
  - All pre-game cells kept broad (5-35¢) — fires <2/week, +131% n=6 paper.

Live cells removed 2026-07-28. The "alpha cell" that motivated the whole
2026-04-26 revision (MLB live 20-30¢, +25.2% on n=30) replays to -20.54% on
n=317 with its Wilson upper bound below breakeven. The NHL live cells, set on
n=4, have produced 3 signals lifetime. Real money agreed all along: the
live-cell slice is n=59 / -14.65%. Pre cells stand — they were not part of
this re-derivation. See ALLOWED_CELLS below for the full numbers.
"""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_v4_edge")


class SportsV4EdgeStrategy(SportsTraderStrategy):
    """Combines midshot's price/payoff filter (0.05-0.35, 2.5x+) with a
    3% edge gate. Falls back to consensus when Pinnacle is absent.
    """

    TRADED_TICKERS_FILE = "data/sports_v4_edge_tickers.json"
    STRATEGY_NAME = "sports_v4_edge"
    BADGE_LABEL = "V4"
    BADGE_COLOR = "#3b82f6"
    MIN_EDGE = 0.03
    PRICE_MIN = 0.05
    PRICE_MAX = 0.35
    PAYOFF_MIN = 2.5
    # Probability floor on the side we're betting. Pinnacle (or consensus
    # fallback) must give our chosen side >= 50% probability. Added 2026-04-26
    # after observer sub-cell analysis on MLB live 20-29¢ found:
    #   side_prob 0.30-0.40 → -44% ROI (n=18)
    #   side_prob 0.40-0.50 → -62% ROI (n=55)
    #   side_prob >= 0.50   → +14.8% ROI (n=35)  ← alpha concentrated here
    # Mechanism: at 20-29¢ entry, edge is meaningful only when Pinnacle says
    # our side is the favorite. Edge alone (3pp threshold) admits low-prob
    # underdog signals where fees overwhelm the EV. User accepted single-trade
    # dependency risk (top 3 winners drive the +14.8%) — eval at n>=70 in
    # 1-2 weeks before keeping/reverting.
    MIN_SIDE_PROB = 0.50
    # Live safety rail (matches v31). 5¢–35¢ range + 87.5% live-game entry
    # → thin books + volatile pricing. Reject signals with <$5 resting
    # liquidity on the signal side to bound slippage and partial-fill risk.
    MIN_DEPTH_USDC = 5.0
    # Pair-fire dedup (2026-05-15). Lifetime LIVE n=56/47 games shows 17%
    # of games saw both-sides fired across scans — small fraction by count
    # but heavily concentrated on losing trades. Game-level dedup sim lifts
    # ROI -42% → +3%. See cross_strategy_tier_2026-05-12 memory.
    PAIR_FIRE_DEDUP = True

    # Per-cell sport/phase + price band gates — supersedes the prior
    # global ENTRY_PRICE_DEAD_ZONE = (0.10, 0.20). PRICE_MIN/MAX above are
    # kept as a wide initial filter inside evaluate(); CELL_PRICE_BANDS
    # narrows per cell after ALLOWED_CELLS passes (in base scan_and_execute).
    # 2026-07-28: both live cells REMOVED. They were set on 2026-04-26 from a
    # one-week eval — MLB live 20-30¢ "+25.2% n=30", NHL live "both n=4". A
    # full-history replay of this exact gate stack, deduped to one signal per
    # game, says otherwise:
    #     MLB live 20-29¢  n=317  WR 22.4% vs breakeven 27.7%  ROI -20.54%
    #                             Wilson [18.2, 27.3] — upper bound BELOW
    #                             breakeven, i.e. decisively -EV at 10x the
    #                             sample the cell was created on
    #     MLB live 05-09¢  n=24   ROI -0.62% (breakeven, uninformative)
    #     NHL live 30-35¢  n=3    lifetime. NHL live 05-09¢: zero signals.
    # Real money agrees: the live-cell slice of this strategy's `settled` rows
    # is n=59 / -14.65%. Only the paper twin dissented (+4.22% n=168) and the
    # replay fires 2x as often, so paper sampled a luckier subset.
    # NOT a match-contamination artifact — the contaminated and clean replays
    # differ by 2pp (-18.27% vs -20.54%), because MLB live phase labels were
    # 86.9% correct. The cells were simply built on n=30 and n=4.
    # Pre cells are untouched: unaffected by that bug (pre labels ~99% correct)
    # and NOT re-derived here, so there is no evidence either way on them.
    ALLOWED_CELLS = frozenset({
        ("MLB", "pre"), ("NBA", "pre"), ("NHL", "pre"),
    })
    # Bands are inclusive on both ends. Kalshi prices come in integer-cent
    # ticks, so each band edge maps to specific cents:
    #   (0.05, 0.09) = cents 5,6,7,8,9 — the "<10¢" historic bucket
    #   (0.20, 0.29) = cents 20-29     — MLB live alpha bucket (30¢ is in losing 30-35¢ bucket)
    #   (0.30, 0.35) = cents 30-35     — NHL live small +ROI bucket
    #   (0.20, 0.35) = cents 20-35     — pre-game broad allow
    # 0.10 is intentionally NOT in any band (preserves original DEAD_ZONE).
    CELL_PRICE_BANDS = {
        ("MLB", "pre"):  [(0.05, 0.09), (0.20, 0.35)],
        ("NBA", "pre"):  [(0.05, 0.09), (0.20, 0.35)],
        ("NHL", "pre"):  [(0.05, 0.09), (0.20, 0.35)],
    }

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()
        self._last_pinnacle_yes_prob: Optional[float] = None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        prob_yes = self._last_pinnacle_yes_prob if self._last_pinnacle_yes_prob is not None else fair_prob

        best_signal = None
        best_edge = -1.0
        for side, price, side_prob in (
            ("yes", kalshi_yes, prob_yes),
            ("no", kalshi_no, 1 - prob_yes),
        ):
            if not (self.PRICE_MIN <= price <= self.PRICE_MAX):
                continue
            if side_prob < self.MIN_SIDE_PROB:
                continue
            fee_pct = self._gate_fee_pct(price) if price > 0 else 0
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff < self.PAYOFF_MIN:
                continue
            edge = side_prob - cost
            if edge < self.MIN_EDGE:
                continue
            if edge > best_edge:
                best_signal = {"side": side, "price": price, "edge": edge,
                               "strategy": "v4_edge"}
                best_edge = edge
        return best_signal

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 3→4 on 2026-04-20 alongside v31 base classes.
        return min(4.0, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        return signal["price"]

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        source = "pin" if self._last_pinnacle_yes_prob is not None else "cons"
        return (f"[V4-EDGE] {ticker}: {title[:40]} "
                f"src={source} ask={ya:.2f}/{na:.2f} edge={signal['edge']:.3f} "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
