import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("sports_trader")

# Kalshi sports series
SPORTS_SERIES = ["KXNBAGAME", "KXMLBGAME", "KXNHLGAME"]

# Odds API full name → Kalshi yes_sub_title for disambiguation
# Covers: ambiguous cities (NY, LA, Chicago) + NHL abbreviated format
ODDS_TO_KALSHI_SHORT = {
    # MLB
    "los angeles dodgers": "los angeles d",
    "los angeles angels": "los angeles a",
    "new york yankees": "new york y",
    "new york mets": "new york m",
    "chicago white sox": "chicago ws",
    "chicago cubs": "chicago c",
    # NBA
    "los angeles lakers": "los angeles l",
    "los angeles clippers": "los angeles c",
    "new york knicks": "new york k",
    "brooklyn nets": "brooklyn",
    "chicago bulls": "chicago b",
    # NHL — Kalshi uses "ABR Mascot" format (e.g., "SJ Sharks", "EDM Oilers")
    "san jose sharks": "sj sharks",
    "anaheim ducks": "ana ducks",
    "los angeles kings": "la kings",
    "st louis blues": "stl blues",
    "colorado avalanche": "col avalanche",
    "vancouver canucks": "van canucks",
    "edmonton oilers": "edm oilers",
    "seattle kraken": "sea kraken",
    "winnipeg jets": "wpg jets",
    "chicago blackhawks": "chi blackhawks",
    "new york rangers": "nyr rangers",
    "new york islanders": "nyi islanders",
    "carolina hurricanes": "car hurricanes",
    "columbus blue jackets": "cbj blue jackets",
    "calgary flames": "cgy flames",
    "vegas golden knights": "vgk golden knights",
    "nashville predators": "nsh predators",
    "toronto maple leafs": "tor maple leafs",
    "montreal canadiens": "mtl canadiens",
    "detroit red wings": "det red wings",
    "minnesota wild": "min wild",
    "buffalo sabres": "buf sabres",
    "ottawa senators": "ott senators",
    "dallas stars": "dal stars",
    "tampa bay lightning": "tb lightning",
    "florida panthers": "fla panthers",
    "pittsburgh penguins": "pit penguins",
    "philadelphia flyers": "phi flyers",
    "washington capitals": "wsh capitals",
    "boston bruins": "bos bruins",
    "new jersey devils": "nj devils",
}


class SportsTraderStrategy:
    """Trade sports game markets by comparing Kalshi prices to sportsbook odds."""

    TRADED_TICKERS_FILE = "data/sports_traded_tickers.json"

    def __init__(self, config, order_manager, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._client = kalshi_client
        self._odds_api_key = getattr(config, "ODDS_API_KEY", "")
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: dict[str, float] = {}
        self._odds_cache: dict[str, dict] = {}  # game_key -> {home_prob, away_prob, updated}
        self._http: Optional[httpx.AsyncClient] = None
        self.odds_api_usage: dict = {"used": 0, "remaining": 0, "last": 0}  # exposed to dashboard

        # Load persisted traded tickers to survive restarts
        self._load_traded_tickers()

    def _load_traded_tickers(self):
        try:
            if os.path.exists(self.TRADED_TICKERS_FILE):
                with open(self.TRADED_TICKERS_FILE) as f:
                    data = json.load(f)
                self._traded_tickers.update(data)
                logger.info(f"[SPORTS] Loaded {len(data)} traded tickers from file")
        except Exception as e:
            logger.warning(f"[SPORTS] Could not load traded tickers: {e}")

    def _save_traded_tickers(self):
        try:
            with open(self.TRADED_TICKERS_FILE, "w") as f:
                json.dump(self._traded_tickers, f)
        except Exception as e:
            logger.warning(f"[SPORTS] Could not save traded tickers: {e}")

    def _get_live_sports_exposure(self) -> float:
        """Get total exposure from Kalshi API for sports positions.
        Returns large value on failure to prevent trading when unknown."""
        try:
            positions = self._client.get_positions()
            return sum(
                float(p.get("market_exposure_dollars", 0))
                for p in positions
                if any(s in p.get("ticker", "") for s in ["NBAGAME", "MLBGAME", "NHLGAME"])
                and float(p.get("position_fp", 0)) != 0
            )
        except Exception as e:
            logger.error(f"[SPORTS] Failed to get exposure, blocking trades: {e}")
            return float("inf")  # Block trading when exposure unknown

    async def _fetch_odds(self, sport: str) -> list[dict]:
        """Fetch odds from The Odds API."""
        if not self._odds_api_key:
            return []
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10)
        try:
            resp = await self._http.get(
                f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                params={
                    "apiKey": self._odds_api_key,
                    "regions": "us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                },
            )
            resp.raise_for_status()
            # Track API usage from response headers
            self.odds_api_usage = {
                "used": int(resp.headers.get("x-requests-used", 0)),
                "remaining": int(resp.headers.get("x-requests-remaining", 0)),
                "last": int(resp.headers.get("x-requests-last", 0)),
            }
            return resp.json()
        except Exception as e:
            logger.error(f"Odds API error for {sport}: {e}")
            return []

    def _decimal_to_prob(self, decimal_odds: float) -> Optional[float]:
        """Convert decimal odds to implied probability. Returns None for invalid odds."""
        if decimal_odds <= 1.0:
            return None
        return 1.0 / decimal_odds

    def _consensus_probability(self, game: dict) -> Optional[dict]:
        """Calculate consensus probability from multiple bookmakers."""
        bookmakers = game.get("bookmakers", [])
        if not bookmakers:
            return None
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        home_probs = []
        away_probs = []
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    odds = outcome.get("price", 0)
                    prob = self._decimal_to_prob(odds)
                    if prob is None:
                        continue
                    if outcome.get("name") == home_team:
                        home_probs.append(prob)
                    elif outcome.get("name") == away_team:
                        away_probs.append(prob)
        if not home_probs or not away_probs:
            return None
        avg_home = sum(home_probs) / len(home_probs)
        avg_away = sum(away_probs) / len(away_probs)
        # Normalize to sum to 1.0 (remove vig)
        total = avg_home + avg_away
        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_prob": avg_home / total,
            "away_prob": avg_away / total,
        }

    def _match_kalshi_to_odds(self, kalshi_title: str, odds_games: list[dict]) -> Optional[dict]:
        """Match a Kalshi market title to odds API game data.

        Kalshi uses city names ("Phoenix at Charlotte"), while Odds API uses
        full team names ("Phoenix Suns" vs "Charlotte Hornets"). Match by
        checking if FULL city name of BOTH teams appears in the title.
        Single-word fallback only for single-word cities to avoid
        "San" matching both "San Diego" and "San Francisco".
        """
        title_lower = kalshi_title.lower()
        for game in odds_games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
            away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
            # Only use first-word fallback for single-word cities
            home_match = home_city in title_lower
            away_match = away_city in title_lower
            if not home_match and len(home_city.split()) == 1:
                home_match = home_city in title_lower
            if not away_match and len(away_city.split()) == 1:
                away_match = away_city in title_lower
            if home_match and away_match:
                return game
        return None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float) -> Optional[dict]:
        """Compare Kalshi price to fair probability from sportsbook consensus.
        Edge is calculated NET of Kalshi maker fees."""
        min_edge = 0.08  # 8% minimum edge AFTER fees
        fee_per_contract = self.config.KALSHI_FEE_PER_CONTRACT  # $0.07

        # YES side: buy at kalshi_yes, fee per contract capped at min(fee, price, 1-price)
        yes_fee_pct = min(fee_per_contract, kalshi_yes, 1 - kalshi_yes) / kalshi_yes if kalshi_yes > 0 else 0
        yes_edge = fair_prob - kalshi_yes * (1 + yes_fee_pct)

        # NO side: buy at kalshi_no, same fee logic
        no_fee_pct = min(fee_per_contract, kalshi_no, 1 - kalshi_no) / kalshi_no if kalshi_no > 0 else 0
        no_edge = (1 - fair_prob) - kalshi_no * (1 + no_fee_pct)

        if yes_edge > min_edge and yes_edge >= no_edge:
            return {"side": "yes", "price": kalshi_yes, "edge": yes_edge}
        elif no_edge > min_edge:
            return {"side": "no", "price": kalshi_no, "edge": no_edge}
        return None

    async def scan_and_execute(self):
        if not getattr(self.config, "SPORTS_ENABLED", False):
            return
        if self._order_manager.is_circuit_breaker_active():
            return
        now = time.time()
        scan_interval = getattr(self.config, "SPORTS_SCAN_INTERVAL", 300)
        if now - self._last_scan_time < scan_interval:
            return
        self._last_scan_time = now

        # Fetch odds from API
        sport_map = {
            "KXNBAGAME": "basketball_nba",
            "KXMLBGAME": "baseball_mlb",
            "KXNHLGAME": "icehockey_nhl",
        }

        all_odds = {}
        for series, sport_key in sport_map.items():
            games = await self._fetch_odds(sport_key)
            all_odds[series] = games

        trades_attempted = 0
        markets_scanned = 0
        max_trades_per_cycle = 3
        hit_limit = False

        # Check total exposure ONCE per cycle (not per market)
        total_exposure = self._get_live_sports_exposure()
        max_exposure = self._order_manager.bankroll * 0.30
        if total_exposure > max_exposure:
            logger.info(f"[SPORTS] Exposure ${total_exposure:.2f} > limit ${max_exposure:.2f}, skipping")
            return

        # Track game IDs already traded this cycle to avoid betting both sides
        traded_games: set[str] = set()

        loop = asyncio.get_event_loop()
        for series in SPORTS_SERIES:
            if hit_limit:
                break
            try:
                data = await loop.run_in_executor(
                    None,
                    lambda s=series: self._client._get(
                        f"/trade-api/v2/markets?status=open&series_ticker={s}&limit=100"
                    ),
                )
                markets = data.get("markets", []) if data else []
            except Exception as e:
                logger.error(f"Failed to fetch {series}: {e}")
                continue

            odds_games = all_odds.get(series, [])
            for market in markets:
                if trades_attempted >= max_trades_per_cycle:
                    hit_limit = True
                    break

                ticker = market["ticker"]
                markets_scanned += 1
                if now - self._traded_tickers.get(ticker, 0) < 3600:
                    continue

                # Skip if we already traded the other side of this game
                # Game ID = ticker minus the team suffix (e.g., KXNBAGAME-26APR01DENUTA)
                game_id = "-".join(ticker.split("-")[:-1])
                if game_id in traded_games:
                    continue

                title = market.get("title", "")
                ya = float(market.get("yes_ask_dollars", "0"))
                na = float(market.get("no_ask_dollars", "0"))
                if ya < 0.05 or na < 0.05:
                    continue

                # Match to odds data
                matched_game = self._match_kalshi_to_odds(title, odds_games)
                if not matched_game:
                    continue

                consensus = self._consensus_probability(matched_game)
                if not consensus:
                    continue

                # Determine which team YES refers to using yes_sub_title (most reliable)
                # e.g., ticker -MIN has yes_sub_title="Minnesota", -DET has "Detroit"
                yes_team = market.get("yes_sub_title", "").lower()
                home = consensus["home_team"].lower()
                away = consensus["away_team"].lower()

                # First try exact short-name match for ambiguous cities (NY, LA, Chicago)
                home_short = ODDS_TO_KALSHI_SHORT.get(home, "")
                away_short = ODDS_TO_KALSHI_SHORT.get(away, "")
                if home_short and home_short == yes_team:
                    fair_prob = consensus["home_prob"]
                elif away_short and away_short == yes_team:
                    fair_prob = consensus["away_prob"]
                else:
                    # Fallback: city name matching for non-ambiguous teams
                    home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
                    away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
                    if home_city in yes_team or yes_team in home_city:
                        fair_prob = consensus["home_prob"]
                    elif away_city in yes_team or yes_team in away_city:
                        fair_prob = consensus["away_prob"]
                    else:
                        logger.debug(f"[SPORTS] Can't match YES team '{yes_team}' to {home}/{away}")
                        continue

                signal = self.evaluate(ya, na, fair_prob)
                if not signal:
                    continue

                size_pct = 0.10 if signal["edge"] >= 0.15 else 0.05
                size = self._order_manager.bankroll * size_pct
                if size < 1.0:
                    continue

                limit_price = round(fair_prob if signal["side"] == "yes" else (1 - fair_prob), 2)
                limit_price = max(0.01, min(0.99, limit_price))

                logger.info(
                    f"[SPORTS] {ticker}: {title[:40]} fair={fair_prob:.2f} "
                    f"ask={ya:.2f}/{na:.2f} edge={signal['edge']:.2f} "
                    f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}"
                )
                # Mark BEFORE order to prevent duplicates even if order/DB fails
                self._traded_tickers[ticker] = now
                self._save_traded_tickers()
                await self._order_manager.limit_order(
                    ticker=ticker, side=signal["side"], price=limit_price,
                    size_usdc=size, strategy="sports_trader", market_id=ticker,
                )
                trades_attempted += 1
                traded_games.add(game_id)

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[SPORTS] Scanned {markets_scanned} game markets, "
                f"{trades_attempted} trades"
            )
            self._last_summary_time = now
