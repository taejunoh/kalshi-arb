"""Sports Midshot V3.1 — V3 midshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_midshot_v31")


class SportsMidshotV31Strategy(SportsMidshotStrategy):
    """Same midshot logic (5-35c, 2.5x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v31_tickers.json"
    STRATEGY_NAME = "sports_midshot_v31"
    REGION_OVERRIDE = "eu"
    # Depth gate scaled to base 16: peak stake ~$6.80 → 3× buffer = $20.
    MIN_DEPTH_USDC = 20.0
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
        # Raised 16→24 on 2026-05-02 (1.5x). Live n=193, ROI 25.8% on $832
        # volume — best capital efficiency in the v31 trio. Bigger relative
        # bump than underdog (1.25x) because $/$ return is 1.47x higher.
        return min(24.0, self._order_manager.bankroll * 0.02)
