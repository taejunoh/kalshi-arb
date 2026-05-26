"""MLB pitcher edge v2 — point-in-time (bias-free) ERA strategy.

Designed 2026-05-26 after v1 look-ahead bias revealed:
  v1 baseline (current-season ERA): WR 82.5%, EV +$0.175/$1
  v1 point-in-time:                 WR 57.1%, EV -$0.073/$1  ← TRUE EV

Bias inflated WR by ~25pp. Real signal is much weaker but EXISTS at
specific thresholds + with directional asymmetry.

Backtest 2026-05-26 (point-in-time, ERA from gameLog cumulative):

  ERA diff ≥2.5 + AWAY_better → fade home (buy NO):
    n=20, WR 65%, EV +$0.076/$1  ⭐ strongest
  ERA diff ≥2.5 + HOME_better → buy home YES:
    n=20, WR 70%, EV +$0.042/$1  ✓
  ERA diff ≥3.0 + AWAY_better:
    n=12, WR 75%, EV +$0.188/$1  (small n, same direction)

KEY STRUCTURAL INSIGHT: Kalshi MLB markets overprice home advantage.
When AWAY pitcher is meaningfully better (≥2.5 ERA diff), the AWAY
team is systematically underpriced. Fading home (buying away) captures
this inefficiency.

Coexists with v1 for controlled A/B paper comparison (5/26-6/09).
v1 will likely converge to ~0 EV; v2 expected modest +EV.

Strategy specifics:
  - Reads from mlb_pitcher_pointtime_observations (filled by
    data_collectors/mlb_pitcher_pointtime.py daily)
  - Both directions fire (home_better → buy YES, away_better → buy NO)
  - Conservative gate: ERA diff ≥ 2.5
  - Paper-only at launch ($4 size)

Promotion gate (paper→LIVE):
  - 21d forward (longer than v1 due to thinner EV)
  - n ≥ 25
  - net positive
  - both directions consistent (not driven by one side alone)
"""
import logging
import os
import sqlite3
import time
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_mlb_pitcher_edge_v2")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


class SportsMlbPitcherEdgeV2Strategy(SportsTraderStrategy):
    """Bias-free MLB pitcher ERA strategy with home-advantage-overprice exploit."""

    TRADED_TICKERS_FILE = "data/sports_mlb_pitcher_edge_v2_tickers.json"
    STRATEGY_NAME = "sports_mlb_pitcher_edge_v2"
    BADGE_LABEL = "MLB-P2"
    BADGE_COLOR = "#0284c7"

    # MLB-only.
    SPORT_BLOCKLIST = frozenset({"NBA", "WNBA", "NHL", "NPB", "KBO",
                                  "EPL", "MLS", "LAL", "BUN", "UCL", "UFC",
                                  "ATP", "WTA"})
    PLAYOFF_BLOCKLIST = frozenset()
    PRE_GAME_ONLY = True

    # Faster scan cadence than the 300s parent default. Paper-only strategy
    # with a narrow candidate universe (era_diff ≥ 2.5 + both ERAs ∈ [1.0, 8.0])
    # — typically 3-6 firable games coexist, so max_trades_per_cycle=3 means
    # 1-2 cycles are needed to hit them all. With 5min interval, candidates
    # closer to game-start can wipe before cycle 2 lands. 60s catches them
    # while still leaving headroom for cache refresh and odds-API quota.
    # See [[mlb_pitcher_edge_v2_missed_fire_2026-05-26]].
    SCAN_INTERVAL_OVERRIDE = 60.0

    # Stricter ERA gate than v1 (concentrates +EV cohort)
    MIN_ERA_DIFF = 2.5

    # ERA validity range (filter unreliable extremes)
    MIN_PITCHER_ERA = 1.0
    MAX_PITCHER_ERA = 8.0

    # Kalshi price band
    MIN_KALSHI_PRICE = 0.40
    MAX_KALSHI_PRICE = 0.75

    # Both directions fire — away_better is the stronger signal per backtest
    BASE_SIZE_USDC = 4.0

    # In-memory cache of point-in-time pitcher data
    _pit_cache: dict = {}
    _pit_cache_ts: float = 0.0
    CACHE_TTL_S = 300  # 5 min — point-in-time data refreshes daily, cache tight

    def _get_trade_size(self, signal: dict) -> float:
        return min(self.BASE_SIZE_USDC, self._order_manager.bankroll * 0.02)

    def _load_pit_cache(self):
        """Refresh point-in-time pitcher cache from DB."""
        now = time.time()
        if now - self._pit_cache_ts < self.CACHE_TTL_S:
            return
        try:
            conn = sqlite3.connect(DB)
            conn.execute("PRAGMA query_only = 1")
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.cursor()
            # Latest row per game (UPSERT keeps single row, but defensive).
            cur.execute("""
                SELECT ticker_home_yes, ticker_away_yes,
                       era_diff_pit, better_team,
                       away_era_pit, home_era_pit, away_code, home_code,
                       away_pitcher_name, home_pitcher_name
                FROM mlb_pitcher_pointtime_observations
                WHERE era_diff_pit IS NOT NULL
            """)
            cache_h = {}  # by home YES ticker
            cache_a = {}  # by away YES ticker
            for row in cur.fetchall():
                th, ta, ediff, better, ae, he, ac, hc, an, hn = row
                info = {
                    "era_diff": ediff, "better_team": better,
                    "away_era": ae, "home_era": he,
                    "away_code": ac, "home_code": hc,
                    "away_pitcher": an, "home_pitcher": hn,
                }
                if th: cache_h[th] = info
                if ta: cache_a[ta] = info
            conn.close()
            self.__class__._pit_cache = {"home": cache_h, "away": cache_a}
            self.__class__._pit_cache_ts = now
        except Exception as e:
            logger.warning(f"pit cache refresh failed: {e}")

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """Fire based on point-in-time ERA differential.

        - If this ticker is the HOME team's YES ticker AND home_pitcher is
          meaningfully better → buy YES (home wins)
        - If this ticker is the AWAY team's YES ticker AND away_pitcher is
          meaningfully better → buy YES (away wins) — equivalent to buying
          home_NO on the home ticker
        """
        self._load_pit_cache()
        cache = self._pit_cache
        if not cache:
            return None

        # Check home_yes ticker → are we predicting home wins?
        info = cache.get("home", {}).get(ticker)
        is_home_ticker = info is not None
        if not is_home_ticker:
            # Check away_yes ticker → are we predicting away wins?
            info = cache.get("away", {}).get(ticker)
            if info is None:
                return None
            # On away_yes ticker, this team = away
            our_team = info["away_code"]
        else:
            our_team = info["home_code"]

        # Gate 1: ERA diff threshold
        if info["era_diff"] is None or info["era_diff"] < self.MIN_ERA_DIFF:
            return None
        # Gate 2: both pitchers' ERA in valid range
        ae, he = info["away_era"], info["home_era"]
        if ae is None or he is None:
            return None
        if not (self.MIN_PITCHER_ERA <= ae <= self.MAX_PITCHER_ERA):
            return None
        if not (self.MIN_PITCHER_ERA <= he <= self.MAX_PITCHER_ERA):
            return None
        # Gate 3: only fire if OUR team is the better team (per ERA)
        if our_team != info["better_team"]:
            return None
        # Gate 4: Kalshi price in band
        if kalshi_yes is None:
            return None
        if not (self.MIN_KALSHI_PRICE <= kalshi_yes <= self.MAX_KALSHI_PRICE):
            return None

        return {
            "side": "yes",
            "price": kalshi_yes,
            "edge": info["era_diff"],
            "strategy": "mlb_pitcher_edge_v2",
            "ticker": ticker,
        }
