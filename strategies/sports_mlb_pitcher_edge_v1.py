"""MLB starting-pitcher ERA edge (paper-only Phase 0/1).

Built 2026-05-26 after backtest revealed that games with ≥2.0 ERA
differential between starting pitchers see the better-pitcher team win
substantially more than market-implied probability.

Backtest 2026-05-26 (n=358 over 30d, MLB Stats API current-season ERA):
  ERA diff 0.0-0.5: WR 58%, EV +$0.003/$1
  ERA diff 0.5-1.0: WR 48%, EV -$0.115/$1  (worst — close-but-not-equal)
  ERA diff 1.0-1.5: WR 55%, EV -$0.041/$1
  ERA diff 1.5-2.0: WR 59%, EV -$0.023/$1
  ERA diff 2.0-3.0: WR 71%, EV +$0.087/$1  ← +EV starts here
  ERA diff 3.0+:    WR 77%, EV +$0.137/$1

GATE: ERA diff ≥ 2.0 → buy YES on better-pitcher team. n≈130/30d, WR 74%.

THESIS: Kalshi MLB markets are roughly efficient on team-level signals,
but starting-pitcher ERA is a strong individual-game predictor that the
market may not fully discount, especially for late-announced matchups or
on days with multiple lineup changes.

LOOK-AHEAD CAVEAT: backtest used current-season ERA (includes data after
each predicted game). Forward paper performance is the honest signal —
expect WR shrinkage. Paper-only at launch.

PROMOTION GATE (paper→LIVE):
  - Forward 14d n ≥ 30
  - Forward WR ≥ 65% (down from sim 74%, allowing for look-ahead shrinkage)
  - Forward net positive

Risk profile: 4-5 fires/day, moderate edge, regime-independent (MLB
pitchers vs Kalshi pricing — no BTC/crypto correlation).
"""
import logging
import os
import sqlite3
import time
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_mlb_pitcher_edge_v1")

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsMlbPitcherEdgeV1Strategy(SportsTraderStrategy):
    """BUY YES on the team with significantly better starting pitcher."""

    TRADED_TICKERS_FILE = "data/sports_mlb_pitcher_edge_v1_tickers.json"
    STRATEGY_NAME = "sports_mlb_pitcher_edge_v1"
    BADGE_LABEL = "MLB-P"
    BADGE_COLOR = "#0ea5e9"

    # MLB-only. Override v31 family's MLB block.
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC",
                                  "ATP", "WTA"})
    # No playoff special handling for MLB.
    PLAYOFF_BLOCKLIST = frozenset()
    PRE_GAME_ONLY = True

    # Minimum ERA diff to fire. Backtest +EV cliff at 2.0.
    MIN_ERA_DIFF = 2.0

    # Reject games where either pitcher has unreliable ERA. ERA<0.5 usually
    # means insufficient IP (e.g. SP just recalled from AAA, only 1 inning of
    # MLB this season). ERA>10 same issue (single bad outing inflates).
    MIN_PITCHER_ERA = 0.50
    MAX_PITCHER_ERA = 8.00

    # Kalshi YES price band — only fire when the better-pitcher team is
    # priced as moderate-favorite-or-less. If Kalshi already prices them
    # ≥0.80, the edge is gone (market already reflects pitcher quality).
    MIN_YES_PRICE = 0.40
    MAX_YES_PRICE = 0.75

    # 2026-06-05 PAIR-FIRE DEDUP (correctness fix). Probable starters can change
    # between a >24h-early fire and game day, flipping better_team; without this
    # the strategy can fire opposite sides of the same game across scan cycles
    # (observed: MINCWS 5/28 MIN-YES then CWS-YES). Keeps the first side, blocks
    # the later opposite-side fire. NOTE: v1 reads mlb_pitcher_observations
    # (current-season ERA), which carries NO innings-pitched column, so the
    # MIN_PITCHER_IP small-sample guard added to v2/v2.1 cannot apply here — v1
    # remains the IP-blind, look-ahead-baseline variant for A/B only.
    PAIR_FIRE_DEDUP = True

    # Small paper size — strategy hasn't been LIVE-validated.
    BASE_SIZE_USDC = 4.0

    # In-memory pitcher data cache. Refreshed every CACHE_TTL_S sec.
    _pitcher_cache: dict = {}
    _pitcher_cache_ts: float = 0.0
    CACHE_TTL_S = 120

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.sizing_bankroll(self.STRATEGY_NAME) * 0.02)

    def _load_pitcher_cache(self):
        """Refresh ticker→(era_diff, better_team) cache from DB."""
        now = time.time()
        if now - self._pitcher_cache_ts < self.CACHE_TTL_S:
            return
        try:
            conn = sqlite3.connect(DB)
            conn.execute("PRAGMA query_only = 1")
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.cursor()
            # For each ticker, take the most recent observation.
            cur.execute("""
                SELECT ticker_better_yes, era_diff, better_team,
                       away_era, home_era, away_pitcher_name, home_pitcher_name
                FROM mlb_pitcher_observations
                WHERE ticker_better_yes IS NOT NULL AND era_diff IS NOT NULL
                  AND id IN (
                      SELECT MAX(id) FROM mlb_pitcher_observations
                      WHERE ticker_better_yes IS NOT NULL
                      GROUP BY ticker_better_yes
                  )
            """)
            cache = {}
            for row in cur.fetchall():
                ticker, diff, team, ae, he, ap, hp = row
                cache[ticker] = {"era_diff": diff, "better_team": team,
                                 "away_era": ae, "home_era": he,
                                 "away_pp": ap, "home_pp": hp}
            conn.close()
            self.__class__._pitcher_cache = cache
            self.__class__._pitcher_cache_ts = now
        except Exception as e:
            logger.warning(f"pitcher cache refresh failed: {e}")

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire YES on tickers where this team has ≥2.0 ERA edge per
        the pitcher_observations table.
        """
        self._load_pitcher_cache()
        info = self._pitcher_cache.get(ticker)
        if info is None:
            # This ticker either isn't MLB or pitcher data not available.
            return None
        if info["era_diff"] < self.MIN_ERA_DIFF:
            return None
        ae, he = info.get("away_era"), info.get("home_era")
        if ae is None or he is None:
            return None
        if not (self.MIN_PITCHER_ERA <= ae <= self.MAX_PITCHER_ERA):
            return None
        if not (self.MIN_PITCHER_ERA <= he <= self.MAX_PITCHER_ERA):
            return None
        if kalshi_yes is None:
            return None
        if not (self.MIN_YES_PRICE <= kalshi_yes <= self.MAX_YES_PRICE):
            return None
        return {
            "side": "yes", "price": kalshi_yes,
            "edge": info["era_diff"],
            "strategy": "mlb_pitcher_edge",
            "ticker": ticker,
        }
