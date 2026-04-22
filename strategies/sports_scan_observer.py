"""Sports scan observer — records odds consensus for every live Kalshi sports
market each scan cycle, regardless of any strategy's signal filter. Paper-only
data collector: no trades, no entries, no gates.

Purpose: feed the 4D matrix (region × sport × phase × ticker-price) with
samples that exist independently of longshot/midshot/underdog/v4 price bands,
so analyses like the 2026-04-21 sport×region matrix can re-run on a complete
candidate universe (including below-edge candidates that never enter).

Cost: zero additional Odds API calls. Reuses the shared (region, sport) cache
via SportsTraderStrategy helpers. Cadence is bounded by the scan_and_execute
loop; DB writes are ~N_open_markets per cycle (tens of rows), trivial.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from strategies.sports_trader import (
    SPORTS_SERIES,
    ODDS_TO_KALSHI_SHORT,
    SportsTraderStrategy,
    _commence_time_to_minutes,
)

logger = logging.getLogger("sports_scan_observer")


class SportsScanObserver(SportsTraderStrategy):
    """Read-only observer that snapshots consensus + Kalshi prices per scan.

    Inherits from SportsTraderStrategy purely for the fetch/match/consensus
    helpers. Overrides scan_and_execute with a stripped loop: no signal, no
    gate, no entry. Writes one row per matched (ticker, scan) to
    `odds_observations` via order_manager._db.
    """

    STRATEGY_NAME = "sports_scan_observer"

    async def scan_and_execute(self):
        if not self._odds_api_key:
            return

        db = getattr(self._order_manager, "_db", None)
        if db is None or not hasattr(db, "record_odds_observation"):
            return

        sport_map = {
            "KXNBAGAME": "basketball_nba",
            "KXMLBGAME": "baseball_mlb",
            "KXNHLGAME": "icehockey_nhl",
        }
        short_map = {"KXNBAGAME": "NBA", "KXMLBGAME": "MLB", "KXNHLGAME": "NHL"}

        # Prefetch odds per sport once per cycle so repeated matches within
        # this cycle are cache hits; same pattern as scan_and_execute.
        all_odds: dict[str, list[dict]] = {}
        for series, sport_key in sport_map.items():
            all_odds[series] = await self._fetch_odds(sport_key)

        loop = asyncio.get_event_loop()
        for series in SPORTS_SERIES:
            try:
                data = await loop.run_in_executor(
                    None,
                    lambda s=series: self._client._get(
                        f"/trade-api/v2/markets?status=open&series_ticker={s}&limit=100"
                    ),
                )
                markets = data.get("markets", []) if data else []
            except Exception as e:
                logger.warning(f"[OBSERVER] Failed to fetch {series}: {e}")
                continue

            odds_games = all_odds.get(series, [])
            sport_key = sport_map[series]
            sport_short = short_map[series]

            for market in markets:
                ticker = market.get("ticker") or ""
                if not ticker:
                    continue

                title = market.get("title", "")
                try:
                    ya = float(market.get("yes_ask_dollars", "0") or 0)
                    na = float(market.get("no_ask_dollars", "0") or 0)
                except (TypeError, ValueError):
                    ya, na = 0.0, 0.0

                matched_game = self._match_kalshi_to_odds(
                    title, odds_games, kalshi_ticker=ticker,
                    kalshi_market=market,
                )
                if not matched_game:
                    continue

                # Dual-region consensus returns HOME-team-referenced probs.
                us_home, eu_home, pin_home, pin_age_s = (
                    await self._dual_region_consensus(sport_key, matched_game)
                )

                home_team = matched_game.get("home_team")
                away_team = matched_game.get("away_team")
                yes_sub_title = (market.get("yes_sub_title") or "").lower()

                # Reuse the same team-matching logic as scan_and_execute so
                # is_yes_home and the YES-referenced prob flip are consistent
                # with how trades are recorded.
                is_yes_home: Optional[bool] = None
                if home_team and away_team:
                    home_lc = home_team.lower()
                    away_lc = away_team.lower()
                    home_short = ODDS_TO_KALSHI_SHORT.get(home_lc, "")
                    away_short = ODDS_TO_KALSHI_SHORT.get(away_lc, "")
                    if home_short and home_short == yes_sub_title:
                        is_yes_home = True
                    elif away_short and away_short == yes_sub_title:
                        is_yes_home = False
                    else:
                        home_city = " ".join(home_lc.split()[:-1]) if len(home_lc.split()) > 1 else home_lc
                        away_city = " ".join(away_lc.split()[:-1]) if len(away_lc.split()) > 1 else away_lc
                        if home_city and (home_city in yes_sub_title or yes_sub_title in home_city):
                            is_yes_home = True
                        elif away_city and (away_city in yes_sub_title or yes_sub_title in away_city):
                            is_yes_home = False

                def _yes_side(home_prob: Optional[float]) -> Optional[float]:
                    if home_prob is None or is_yes_home is None:
                        return None
                    return home_prob if is_yes_home else (1.0 - home_prob)

                us_yes = _yes_side(us_home)
                eu_yes = _yes_side(eu_home)
                pin_yes = _yes_side(pin_home)

                mts = _commence_time_to_minutes(matched_game.get("commence_time"))
                phase = "pre" if (mts is not None and mts > 0) else "live"

                try:
                    await db.record_odds_observation(
                        kalshi_ticker=ticker,
                        series=series,
                        sport=sport_short,
                        phase=phase,
                        minutes_to_start=mts,
                        home_team=home_team,
                        away_team=away_team,
                        yes_sub_title=yes_sub_title or None,
                        is_yes_home=is_yes_home,
                        kalshi_yes_ask=ya if ya > 0 else None,
                        kalshi_no_ask=na if na > 0 else None,
                        us_yes_prob=us_yes,
                        eu_yes_prob=eu_yes,
                        pinnacle_yes_prob=pin_yes,
                        pinnacle_age_s=pin_age_s,
                    )
                except Exception as e:
                    logger.warning(
                        f"[OBSERVER] record failed for {ticker}: {e}"
                    )
