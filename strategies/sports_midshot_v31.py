"""Sports Midshot V3.1 — V3 midshot logic against EU sharp-book consensus."""
import logging

from strategies.sports_midshot import SportsMidshotStrategy

logger = logging.getLogger("sports_midshot_v31")


class SportsMidshotV31Strategy(SportsMidshotStrategy):
    """Same midshot logic (5-35c, 2.5x+ payoff) but `regions=eu`."""

    TRADED_TICKERS_FILE = "data/sports_midshot_v31_tickers.json"
    STRATEGY_NAME = "sports_midshot_v31"
    BADGE_LABEL = "V3.1"
    BADGE_COLOR = "#ec4899"
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
    # 25–30¢ dead zone (2026-05-12). 30d settled trades n=55, ROI −30.6%
    # (MLB regular −52%, NHL playoff −100%, NBA playoff −2.5% — all sports
    # negative). Time-series shows 4/20+ entries almost all −100%; 5/8
    # post-sizeup first $35.84 trade also −100%. Observer last-obs base rate
    # confirms negative alpha at this band (n=4, 0% win vs 27.5% expected).
    # Surrounding bands kept: 20-25¢ marginal positive (+8.5%), 30-35¢ strong
    # carrier (+35.6%). Re-eval 5/25 with bigger post-sizeup n.
    ENTRY_PRICE_DEAD_ZONE = (0.25, 0.30)
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
    # 2026-05-03 NHL added — n=35 LIVE -43% on NHL playoff. Midshot fires
    # mostly in 30-35c (-100%) and 35-40c (-43% on 11 trades) bands; NHL
    # playoff's 50/50 matchup distribution puts almost no underdogs in the
    # 5-30c sweet spot. Cascades to v34, v35.
    PLAYOFF_BLOCKLIST = frozenset({"NBA", "NHL"})
    # 2026-05-12 v31 family $2k-10k dead-zone (commit ca971d5 extended).
    # underdog_v31 n=13 −62%, underdog_v32 n=13 −62%, midshot n=6 −59%.
    # Combined family n=32 ROI −61% systematic. Cascade not automatic
    # (midshot doesn't inherit underdog_v31), so set explicitly here.
    DEPTH_DEAD_ZONE = (2000.0, 10000.0)
    # 2026-05-13 (WNBA expansion): unblock WNBA. Pair-fire dedup formerly
    # WNBA-only (an explicit evaluate() override), generalized 2026-05-15
    # to PAIR_FIRE_DEDUP=True below — base scan loop now blocks cross-scan
    # same-game both-sides fires for every sport.
    OBSERVATION_ONLY_SPORTS = (
        SportsMidshotStrategy.OBSERVATION_ONLY_SPORTS - {"WNBA"}
    )
    # Pair-fire dedup (2026-05-15). Lifetime LIVE analysis on n=221 settled
    # trades / 121 games: 99 games (82%) saw both-sides fired across separate
    # scan cycles, contributing -$76 of paired-bleed against +$192 from the
    # 22 single-fire games. Game-level dedup simulation lifts ROI 22%→72%.
    # See cross_strategy_tier_2026-05-12 memory for the per-strategy table.
    PAIR_FIRE_DEDUP = True

    def _get_trade_size(self, signal: dict) -> float:
        """Depth-tiered cap (2026-05-12).

        Cross-strategy tier ROI analysis (4/24+, MLB regular):
          $500-2k:  +135% (n=7) ⭐ sweet spot — cap raised to 60
          $2k-10k:  −59%  (n=6) — small sample, cap unchanged (no dead-zone
                                  unlike v31 underdog where n=13/13 stronger)
          $10k+:    +26%  (n=23) — cap raised to 60
          Other:    insufficient sample — cap unchanged at 36
        """
        ticker = signal.get("ticker", "")
        side = signal.get("side", "yes")
        depth = None
        if ticker:
            try:
                yd, nd = self._client.get_order_book_depth(ticker)
                depth = yd if side == "yes" else nd
            except Exception:
                pass
        if depth is None:
            cap = 36.0
        elif depth >= 10000:
            cap = 60.0        # thick book +26% (n=23)
        elif depth >= 2000:
            cap = 36.0        # UNREACHABLE — DEPTH_DEAD_ZONE skips $2k-10k
        elif depth >= 500:
            cap = 60.0        # SWEET SPOT +135% (n=7) — cap raised
        else:
            cap = 36.0
        return min(cap, self._order_manager.bankroll * 0.02)
