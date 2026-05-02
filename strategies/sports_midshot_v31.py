"""Sports Midshot V3.1 — V3 midshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_midshot_v31")


class SportsMidshotV31Strategy(SportsMidshotStrategy):
    """Same midshot logic (5-35c, 2.5x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v31_tickers.json"
    STRATEGY_NAME = "sports_midshot_v31"
    REGION_OVERRIDE = "eu"
    # Depth gate scaled to size 36 (2026-05-02 second pass). At max midshot
    # entry price ~80¢, peak stake = 36 × $0.80 = $28.80 → $45 keeps the
    # ~1.5× cushion. Sized up because last-7d data showed $10.13/trade and
    # 69.8% ROI (well above 25.8% all-time baseline) — ride the winner.
    MIN_DEPTH_USDC = 45.0
    # Price bucket ROI (2026-04-20, settled paper+live):
    #   <10¢:    n=9,  0% win, ROI −100%
    #   10–15¢:  n=9,  0% win, ROI −100%
    #   15–20¢:  n=18, ROI −11%
    #   ≥20¢:    ROI positive (+27% at 20-25¢, +27% at 30-35¢)
    # 20¢ simple floor — sub-20¢ loses systematically, no sub-band exception.
    MIN_ENTRY_PRICE = 0.20
    # Pre-game-only (2026-04-21). Phase split shows live-game v31 entries
    # systematically lose (-6% ROI live, paper+live n=252) while pre-game
    # wins (+25% ROI, n=57). Same pattern confirmed on longshot (2026-04-20)
    # and underdog; extending across v31 trio.
    PRE_GAME_ONLY = True
    # NBA playoff suspend (2026-04-30). Game-level analysis shows NBA
    # playoff midshot bleeds across all v3X variants (-19% to -126% strategy
    # ROI). All NBA data post-2026-04-21 is playoff (regular season ended
    # ~2026-04-13). Auto-resumes when NBA regular season returns
    # (~2026-10). Cascades to v34, v35 via inheritance.
    PLAYOFF_BLOCKLIST = frozenset({"NBA"})

    def _get_trade_size(self, signal: dict) -> float:
        # Raised 24→36 on 2026-05-02 (second pass, 1.5x more, total 2.25x
        # vs original 16). Last-7d $10.13/trade and 69.8% ROI made this the
        # clearest winner across the live ladder; user opted to ride it
        # rather than wait for the 5/5 checkpoint. If the 5/5 fill-quality
        # check FLAGS slippage, revert to 24.
        return min(36.0, self._order_manager.bankroll * 0.02)
