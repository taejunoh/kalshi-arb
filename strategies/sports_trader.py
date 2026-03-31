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

# Team name normalization (Kalshi name → odds API name)
TEAM_ALIASES = {
    "Los Angeles D": "Los Angeles Dodgers", "Los Angeles L": "Los Angeles Lakers",
    "Los Angeles C": "Los Angeles Clippers", "New York Y": "New York Yankees",
    "New York M": "New York Mets", "New York K": "New York Knicks",
    "Chicago WS": "Chicago White Sox", "Chicago C": "Chicago Cubs",
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

    def _decimal_to_prob(self, decimal_odds: float) -> float:
        """Convert decimal odds to implied probability."""
        if decimal_odds <= 1:
            return 0.5
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
        checking if city/first word of BOTH teams appears in the title.
        """
        title_lower = kalshi_title.lower()
        for game in odds_games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            # City name is typically the first word(s) before the mascot
            # "Phoenix Suns" → "phoenix", "Los Angeles Lakers" → "los angeles"
            home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
            away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
            # Also try just the first word for short city names
            home_first = home.split()[0]
            away_first = away.split()[0]
            # Both cities must appear in title
            home_match = home_city in title_lower or home_first in title_lower
            away_match = away_city in title_lower or away_first in title_lower
            if home_match and away_match:
                return game
        return None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float) -> Optional[dict]:
        """Compare Kalshi price to fair probability from sportsbook consensus."""
        min_edge = 0.08  # 8% minimum edge for sports
        # YES side
        yes_edge = fair_prob - kalshi_yes
        # NO side
        no_edge = (1 - fair_prob) - kalshi_no

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
                logger.error(f"Failed to fetch {series}: {e}")
                continue

            odds_games = all_odds.get(series, [])
            for market in markets:
                ticker = market["ticker"]
                markets_scanned += 1
                if now - self._traded_tickers.get(ticker, 0) < 3600:
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

                # Determine which team YES refers to (first team in "X at Y" is away)
                home = consensus["home_team"].lower()
                title_lower = title.lower()
                home_short = home.split()[-1]
                if "at" in title_lower:
                    # "Away at Home" format — YES is typically the first team (away)
                    first_team_part = title_lower.split("at")[0]
                    is_home_first = home_short in first_team_part
                else:
                    is_home_first = home_short in title_lower
                fair_prob = consensus["home_prob"] if is_home_first else consensus["away_prob"]

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

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[SPORTS] Scanned {markets_scanned} game markets, "
                f"{trades_attempted} trades"
            )
            self._last_summary_time = now
