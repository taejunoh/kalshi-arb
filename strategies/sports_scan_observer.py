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
    SPORTS_SERIES,
    ODDS_TO_KALSHI_SHORT,
    SportsTraderStrategy,
    _commence_time_to_minutes,
    _match_individual_sport_to_odds,
)

logger = logging.getLogger("sports_scan_observer")


SPORT_MAP = {
    "KXNBAGAME": "basketball_nba",
    "KXWNBAGAME": "basketball_wnba",
    "KXMLBGAME": "baseball_mlb",
    "KXNHLGAME": "icehockey_nhl",
    "KXNPBGAME": "baseball_npb",
    "KXKBOGAME": "baseball_kbo",
    "KXMLSGAME": "soccer_usa_mls",
    "KXEPLGAME": "soccer_epl",
    "KXLALIGAGAME": "soccer_spain_la_liga",
    "KXUCLGAME": "soccer_uefa_champs_league",
    "KXBUNDESLIGAGAME": "soccer_germany_bundesliga",
    # 2026-05-11: Individual-sport observers. Tennis slug rotates per
    # currently-active tournament (manually updated; rotation cron in Task 9
    # alerts when active slug returns 0 events). Verified slugs from Task 0:
    "KXATPMATCH": "tennis_atp_italian_open",  # active 5/11; rotate per tour calendar
    "KXWTAMATCH": "tennis_wta_italian_open",  # active 5/11; rotate per tour calendar
    "KXUFCFIGHT": "mma_mixed_martial_arts",   # year-round single endpoint
}
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

INDIVIDUAL_SPORT_SERIES = frozenset({"KXATPMATCH", "KXWTAMATCH", "KXUFCFIGHT"})


def _matcher_for_series(series: str):
    """Return the matcher function for `series`, or None for team-sport
    series (which use the instance method self._match_kalshi_to_odds).
    Team-sport matcher cannot be returned because it's a method that
    needs `self`; this dispatch sentinel keeps the call site simple."""
    if series in INDIVIDUAL_SPORT_SERIES:
        return _match_individual_sport_to_odds
    return None


def _name_overlap_score(yes_lc: str, name_lc: str) -> float:
    """How well does `yes_lc` match `name_lc`?

    Returns a score in [0.0, 1.0]:
      1.0   = exact (case-insensitive) match
      0.5-0.99 = token-set subset match (one is a subset of the other);
                 higher when token-count difference is smaller
      0.0   = no overlap

    Used by `_individual_yes_is_home` to pick the better side when both
    home and away token-overlap with `yes_sub_title` (e.g., "Diaz vs Diaz").
    """
    if not yes_lc or not name_lc:
        return 0.0
    if yes_lc == name_lc:
        return 1.0
    yes_tok = set(yes_lc.split())
    name_tok = set(name_lc.split())
    if not yes_tok or not name_tok:
        return 0.0
    if not (yes_tok.issubset(name_tok) or name_tok.issubset(yes_tok)):
        return 0.0
    # Subset match — score by token-count ratio (closer = better)
    smaller, larger = min(len(yes_tok), len(name_tok)), max(len(yes_tok), len(name_tok))
    return 0.5 + 0.5 * (smaller / larger)


def _individual_yes_is_home(yes_sub_title: str, home_lc: str, away_lc: str) -> Optional[bool]:
    """Decide which side (home vs away) of the matched game `yes_sub_title`
    refers to. Returns None if neither side overlaps (defensive — by the
    time we reach here, the matcher already picked this game).

    For collisions (e.g., "Diaz vs Diaz" where both fighters share a token),
    pick the side with the higher overlap score. Equal scores → home wins
    (mirrors the existing soccer fallback).
    """
    home_score = _name_overlap_score(yes_sub_title, home_lc)
    away_score = _name_overlap_score(yes_sub_title, away_lc)
    if home_score == 0.0 and away_score == 0.0:
        return None
    return home_score >= away_score


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
        # this cycle are cache hits; same pattern as scan_and_execute.
        all_odds: dict[str, list[dict]] = {}
        for series, sport_key in SPORT_MAP.items():
            all_odds[series] = await self._fetch_odds(sport_key)

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
            sport_key = SPORT_MAP[series]
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
                    )
                if not matched_game:
                    continue

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
                    )
                except Exception as e:
                    logger.warning(
                        f"[OBSERVER] record failed for {ticker}: {e}"
                    )
