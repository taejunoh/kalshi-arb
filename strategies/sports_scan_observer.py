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

3-way support (soccer): each game has 3 binary Kalshi legs (HOME/AWAY/TIE).
The TIE leg has yes_sub_title='Tie' and stores draw_prob (3-way-normalized)
as pinnacle_yes_prob; HOME stores home_prob; AWAY stores away_prob. The
is_yes_draw flag distinguishes TIE rows from AWAY rows (both have is_yes_home=0).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.starter_fetcher import get_starters
from strategies.sports_trader import (
    INDIVIDUAL_SPORT_SERIES,
    SPORTS_SERIES,
    build_opponent_sub_titles,
    SPORT_ODDS_KEYS,
    ODDS_TO_KALSHI_SHORT,
    SportsTraderStrategy,
    _commence_time_to_minutes,
    _individual_yes_is_home,
    _match_individual_sport_to_odds,
    _name_overlap_score,
    measure_match_delta,
)

logger = logging.getLogger("sports_scan_observer")


# Legacy aliases — kept so `tennis_endpoint_rotation.py` and existing tests
# (`test_observer_sport_map_tennis_mma.py`) keep importing from here. Both
# names now resolve to the shared `SPORT_ODDS_KEYS` defined in sports_trader.
SPORT_MAP = SPORT_ODDS_KEYS

SPORT_SHORT = {
    "KXNBAGAME": "NBA",
    "KXWNBAGAME": "WNBA",
    "KXMLBGAME": "MLB",
    "KXNHLGAME": "NHL",
    "KXNPBGAME": "NPB",
    "KXKBOGAME": "KBO",
    "KXMLSGAME": "MLS",
    "KXEPLGAME": "EPL",
    "KXLALIGAGAME": "LAL",
    "KXUCLGAME": "UCL",
    "KXBUNDESLIGAGAME": "BUN",
    "KXATPMATCH": "ATP",
    "KXWTAMATCH": "WTA",
    "KXUFCFIGHT": "UFC",
}


def _matcher_for_series(series: str):
    """Return the matcher function for `series`, or None for team-sport
    series (which use the instance method self._match_kalshi_to_odds).
    Team-sport matcher cannot be returned because it's a method that
    needs `self`; this dispatch sentinel keeps the call site simple."""
    if series in INDIVIDUAL_SPORT_SERIES:
        return _match_individual_sport_to_odds
    return None


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

        # Prefetch odds per sport once per cycle so repeated matches within
        # this cycle are cache hits; same pattern as scan_and_execute. Tennis
        # series fan out across multiple concurrent tournaments — each game
        # gets `_tournament_slug` injected by _fetch_odds_multi so the DB
        # write below records the correct per-game slug.
        all_odds: dict[str, list[dict]] = {}
        for series, sport_value in SPORT_MAP.items():
            all_odds[series] = await self._fetch_odds_multi(sport_value)

        loop = asyncio.get_event_loop()
        for series in SPORTS_SERIES:
            if series not in SPORT_MAP:
                continue
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
            # Opponent lookup for the title-format fallback; built once per
            # series per cycle from the markets we already fetched.
            opponent_subs = build_opponent_sub_titles(markets)
            sport_value = SPORT_MAP[series]
            # For single-slug team sports `sport_value` is a str; for tennis
            # it is a tuple of concurrent tournament slugs. The per-game
            # `_tournament_slug` tag (set by _fetch_odds_multi) overrides
            # this default for the DB write below.
            default_sport_key = (
                sport_value if isinstance(sport_value, str)
                else (sport_value[0] if sport_value else "")
            )
            sport_short = SPORT_SHORT[series]

            for market in markets:
                ticker = market.get("ticker") or ""
                if not ticker:
                    continue

                title = market.get("title", "")

                def _f(key):
                    v = market.get(key)
                    if v is None or v == "":
                        return None
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None

                ya = _f("yes_ask_dollars") or 0.0
                na = _f("no_ask_dollars") or 0.0
                yb = _f("yes_bid_dollars")
                nb = _f("no_bid_dollars")
                yas = _f("yes_ask_size_fp")
                ybs = _f("yes_bid_size_fp")
                nas = _f("no_ask_size_fp")
                nbs = _f("no_bid_size_fp")
                vol24 = _f("volume_24h_fp")
                oi = _f("open_interest_fp")

                ind_matcher = _matcher_for_series(series)
                if ind_matcher is not None:
                    yes_sub_for_match = market.get("yes_sub_title") or ""
                    matched_game = ind_matcher(
                        yes_sub_for_match, odds_games, kalshi_ticker=ticker,
                    )
                else:
                    matched_game = self._match_kalshi_to_odds(
                        title, odds_games, kalshi_ticker=ticker,
                        yes_sub_title=market.get("yes_sub_title"),
                        opponent_sub_title=opponent_subs.get(ticker),
                    )
                if not matched_game:
                    continue

                # Per-game sport_key — tennis fan-out tags each game with its
                # source tournament; team sports fall back to the series-level
                # default. The slug also drives the Pinnacle-age cache key.
                sport_key = matched_game.get("_tournament_slug") or default_sport_key

                # Probability dicts (with draw_prob populated for 3-way soccer).
                us_dict, eu_dict, pin_dict, pin_age_s = (
                    await self._dual_region_consensus_dicts(sport_key, matched_game)
                )

                home_team = matched_game.get("home_team")
                away_team = matched_game.get("away_team")
                yes_sub_title = (market.get("yes_sub_title") or "").lower()

                is_yes_home: Optional[bool] = None
                is_yes_draw: Optional[bool] = None

                if series in INDIVIDUAL_SPORT_SERIES:
                    # The matcher already picked the right game; this block
                    # decides which side (home vs away) yes_sub_title is on.
                    # Score both sides so common-token collisions (e.g.,
                    # "Diaz" present in both fighter names) resolve to the
                    # tighter match rather than defaulting to home.
                    is_yes_home = _individual_yes_is_home(
                        yes_sub_title,
                        (matched_game.get("home_team") or "").lower(),
                        (matched_game.get("away_team") or "").lower(),
                    )
                    is_yes_draw = False
                else:
                    # Three-way leg detection. TIE leg appears in soccer with
                    # yes_sub_title='tie'; otherwise standard home/away.
                    if yes_sub_title == "tie":
                        is_yes_home = False
                        is_yes_draw = True
                    elif home_team and away_team:
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
                        # 3-way sport but neither home/away nor tie matched —
                        # mark as draw if any draw_prob present so downstream
                        # never falsely attributes draw_prob to the away leg.
                        if is_yes_home is None and (eu_dict and "draw_prob" in eu_dict):
                            # Unknown leg — leave both flags None so the row
                            # records yes_sub_title for manual inspection but
                            # does not corrupt the home/away/draw partition.
                            pass

                def _yes_side(probs: Optional[dict]) -> Optional[float]:
                    """Select YES-side probability based on detected leg.

                    Returns None when leg unknown or probs missing. For 3-way
                    sports, TIE leg uses draw_prob; HOME uses home_prob; AWAY
                    uses (1 - home_prob - draw_prob) where draw_prob exists,
                    else (1 - home_prob).
                    """
                    if probs is None:
                        return None
                    if is_yes_draw:
                        return probs.get("draw_prob")
                    if is_yes_home is True:
                        return probs.get("home_prob")
                    if is_yes_home is False:
                        return probs.get("away_prob")
                    return None

                us_yes = _yes_side(us_dict)
                eu_yes = _yes_side(eu_dict)
                pin_yes = _yes_side(pin_dict)

                # Coverage signal — same eu_yes_prob means very different things
                # at n_books=2 (NHL afternoon games) vs 23 (UCL).
                n_books_eu = eu_dict.get("n_books") if eu_dict else None
                n_books_us = us_dict.get("n_books") if us_dict else None
                # has_pinnacle: True iff Pinnacle was present in this game's
                # eu response. Distinguishes "Pinnacle absent for this game"
                # from "leg-detection failed" (both leave pinnacle_yes_prob NULL).
                has_pinnacle = pin_dict is not None

                mts = _commence_time_to_minutes(matched_game.get("commence_time"))
                phase = "pre" if (mts is not None and mts > 0) else "live"
                # Phase-1 instrumentation: how far the matched game is from
                # this ticker's own scheduled start. This is the full-
                # population sample, so it is what quantifies the `phase`
                # label contamination. warn=False — one row per ticker per
                # scan would flood journalctl at the observed ~31% rate; the
                # aggregate comes from the DB column instead.
                match_delta_s = measure_match_delta(
                    ticker, matched_game, "observer", warn=False,
                )

                # Capture starting pitcher/goalie for MLB/NHL (None for other sports).
                # Uses the game's commence_time date in UTC (game day in API's calendar).
                starter_home_name = None
                starter_away_name = None
                if sport_short in ("MLB", "NHL") and home_team and away_team:
                    commence = matched_game.get("commence_time")
                    if commence:
                        date_str = commence[:10]
                        try:
                            starter_home_name, starter_away_name = get_starters(
                                sport_short, date_str, home_team, away_team,
                            )
                        except Exception as e:
                            logger.warning(f"[OBSERVER] starter fetch failed for {ticker}: {e}")

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
                        is_yes_draw=is_yes_draw,
                        kalshi_yes_ask=ya if ya > 0 else None,
                        kalshi_no_ask=na if na > 0 else None,
                        us_yes_prob=us_yes,
                        eu_yes_prob=eu_yes,
                        pinnacle_yes_prob=pin_yes,
                        pinnacle_age_s=pin_age_s,
                        n_books_eu=n_books_eu,
                        n_books_us=n_books_us,
                        has_pinnacle=has_pinnacle,
                        kalshi_yes_bid=yb,
                        kalshi_no_bid=nb,
                        kalshi_yes_bid_size=ybs,
                        kalshi_yes_ask_size=yas,
                        kalshi_no_bid_size=nbs,
                        kalshi_no_ask_size=nas,
                        kalshi_volume_24h=vol24,
                        kalshi_open_interest=oi,
                        tournament_slug=sport_key,
                        starter_home=starter_home_name,
                        starter_away=starter_away_name,
                        match_delta_s=match_delta_s,
                    )
                except Exception as e:
                    logger.warning(
                        f"[OBSERVER] record failed for {ticker}: {e}"
                    )
