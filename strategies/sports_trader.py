import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger("sports_trader")

# Shared Odds API cache across all sports strategies to avoid blowing the
# monthly quota (10 strategies × 3 sports × 288 scans/day = 8640 calls/day
# uncached → ~260K/mo vs 20K allotted). Keyed by (region, sport).
_ODDS_CACHE: dict[tuple[str, str], tuple[float, list[dict], dict]] = {}
# Per-region TTL matched to upstream tick cadence (2026-04-14 freshness measurement):
# us retail (matchbook/betfair) ticks 63~98s → 60s TTL keeps every scan fresh.
# eu (Pinnacle dominant) upstream gap ~883s → 600s TTL avoids duplicate fetch.
# Default for unknown region falls back to the old 300s sweet spot.
_ODDS_CACHE_TTL_BY_REGION: dict[str, float] = {"us": 60.0, "eu": 600.0}
_ODDS_CACHE_TTL_DEFAULT = 300.0

# Quota thresholds for Odds API monthly allowance (20K calls/mo)
QUOTA_MONTHLY_ALLOWANCE = 20000
QUOTA_WARN_THRESHOLD = 5000    # 25%: daily warn
QUOTA_HALT_THRESHOLD = 1000    # 5%:  halt all sports strategies (one-time alert)
QUOTA_CRITICAL_THRESHOLD = 400 # 2%:  extra one-time alert after halt

from core.pinnacle_age import PinnacleAgeTracker
_PINNACLE_AGE = PinnacleAgeTracker()


def _compute_pinnacle_payload_hash(games: list[dict]) -> Optional[str]:
    """Deterministic hash of Pinnacle h2h outcomes across all games in a response.

    Returns None if no game has Pinnacle odds. Game order-independent via sort.
    """
    entries = []
    for g in games:
        gid = g.get("id") or g.get("commence_time") or ""
        for bm in g.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                outs = tuple(
                    (o.get("name"), o.get("price"))
                    for o in mkt.get("outcomes", [])
                )
                entries.append((gid, outs))
    if not entries:
        return None
    entries.sort()
    payload = json.dumps(entries, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _now_utc() -> datetime:
    """Indirection so tests can patch the clock."""
    return datetime.now(timezone.utc)


def _commence_time_to_minutes(commence_time: Optional[str]) -> Optional[float]:
    """Convert Odds API ISO-8601 commence_time to minutes-until-start from now.
    Positive = pre-game, zero/negative = live. Returns None if unparseable."""
    if not commence_time:
        return None
    try:
        iso = commence_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return (dt - _now_utc()).total_seconds() / 60.0


def _odds_cache_ttl(region: str) -> float:
    return _ODDS_CACHE_TTL_BY_REGION.get(region, _ODDS_CACHE_TTL_DEFAULT)


def _odds_cache_now() -> float:
    return time.time()


def _odds_cache_reset() -> None:
    _ODDS_CACHE.clear()

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
    STRATEGY_NAME = "sports_trader"
    REGION_OVERRIDE: Optional[str] = None  # subclass can pin a specific Odds API region
    SCAN_INTERVAL_OVERRIDE: Optional[float] = None  # subclass can override scan cadence (us retail books tick faster)
    MIN_DEPTH_USDC = 0.0  # Subclasses override to enforce orderbook depth before market order
    # Minimum entry price (0–1). Used by longshot variants whose paper-
    # settled data shows a structural dead zone in the 10–20¢ bucket
    # (ROI −40% to −63% in paper, even worse in live). Above the floor
    # signals are positive-EV; below it they are systematically bad
    # regardless of fee drag. Subclasses set to e.g. 0.20; None = no filter.
    MIN_ENTRY_PRICE: Optional[float] = None

    def __init__(self, config, order_manager, kalshi_client):
        self.config = config
        self._order_manager = order_manager
        self._client = kalshi_client
        self._odds_api_key = getattr(config, "ODDS_API_KEY", "")
        self._last_scan_time = 0.0
        self._last_summary_time = 0.0
        self._traded_tickers: dict[str, float] = {}
        self._http: Optional[httpx.AsyncClient] = None
        self.odds_api_usage: dict = {"used": 0, "remaining": 0, "last": 0}  # exposed to dashboard
        self._prev_odds: dict[str, dict] = {}  # game_id -> {home_prob, away_prob, time}
        self._last_pinnacle_yes_prob: Optional[float] = None  # set per-market during scan
        self._last_pinnacle_age_s: Optional[int] = None  # seconds since Pinnacle payload changed; subclasses gate on this
        # Background entry tasks — a v31 limit+TTL order can block up to 60s
        # polling for fills. Dispatching via asyncio.create_task lets the
        # scanner continue past the order while it resolves. The set is
        # self-cleaning via the done-callback registered at launch.
        self._pending_entry_tasks: set[asyncio.Task] = set()

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
        """Atomic write: tmp → fsync → replace. Crash mid-write leaves the
        previous (valid) file untouched rather than a truncated one that
        would lose the "already traded this ticker" guard on restart."""
        tmp_path = self.TRADED_TICKERS_FILE + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(self._traded_tickers, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.TRADED_TICKERS_FILE)
        except Exception as e:
            logger.warning(f"[SPORTS] Could not save traded tickers: {e}")
            # Best-effort cleanup of tmp so it doesn't accumulate on repeat failures
            try:
                os.remove(tmp_path)
            except OSError:
                pass

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

    def _get_scan_interval(self) -> float:
        """Per-strategy scan cadence: subclass SCAN_INTERVAL_OVERRIDE wins, else config default."""
        if self.SCAN_INTERVAL_OVERRIDE is not None:
            return float(self.SCAN_INTERVAL_OVERRIDE)
        return float(getattr(self.config, "SPORTS_SCAN_INTERVAL", 300))

    def _get_odds_region(self) -> str:
        """Per-strategy Odds API region: subclass REGION_OVERRIDE wins, else config default."""
        if self.REGION_OVERRIDE:
            return self.REGION_OVERRIDE
        return getattr(self.config, "ODDS_API_REGION", "eu")

    async def _check_quota(self, remaining: int):
        """Monitor Odds API quota with stepped milestone alerts.

        Thresholds (based on 20K/mo allowance):
        - <2% (CRITICAL): one-time extra alert on top of halt.
        - <5% (HALT): set _quota_exhausted, one-time halt alert. Sports scans stop.
        - 5-25% (middle band): flag untouched, once-per-day warn.
        - >=25% (WARN): clear flags (recovered). If transitioned from halted, recovery alert.
        """
        notifier = getattr(self._order_manager, "_notifier", None)
        om = self._order_manager
        if remaining < QUOTA_HALT_THRESHOLD:
            if not om._quota_exhausted:
                om._quota_exhausted = True
                logger.error(
                    f"[QUOTA] Odds API quota 5% threshold: {remaining} remaining. Sports strategies halted."
                )
                if notifier:
                    await notifier.send(
                        f"⚠️ <b>Odds API quota at 5%</b>\nOnly {remaining} calls remaining.\nSports strategies halted until quota resets.",
                        msg_type="quota_halt",
                    )
            if remaining < QUOTA_CRITICAL_THRESHOLD and not om._quota_critical_alerted:
                om._quota_critical_alerted = True
                logger.error(
                    f"[QUOTA] Odds API quota 2% threshold: {remaining} remaining."
                )
                if notifier:
                    await notifier.send(
                        f"🚨 <b>Odds API quota at 2%</b>\nOnly {remaining} calls remaining. Quota nearly depleted.",
                        msg_type="quota_critical",
                    )
        elif remaining >= QUOTA_WARN_THRESHOLD:
            # Full recovery — clear all milestone flags
            if om._quota_exhausted:
                om._quota_exhausted = False
                om._quota_critical_alerted = False
                logger.info(
                    f"[QUOTA] Odds API quota recovered: {remaining} remaining. Sports strategies resumed."
                )
                if notifier:
                    await notifier.send(
                        f"✅ Odds API quota recovered: {remaining} remaining.\nSports strategies resumed.",
                        msg_type="quota_recovery",
                    )
        else:
            # Middle band — flag untouched, warn once per day.
            # Notifier only has a 60s rate-limit per msg_type, so we dedupe here
            # by tracking the last warn date on OrderManager (shared across all
            # sports_* strategies and their fetches).
            import datetime
            today = datetime.date.today().isoformat()
            if om._quota_warn_date != today:
                om._quota_warn_date = today
                if notifier:
                    await notifier.send(
                        f"ℹ️ Odds API quota low: {remaining} remaining.",
                        msg_type=f"quota_warn_{today}",
                    )

    async def _dual_region_consensus(
        self, sport: str, game: dict
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[int]]:
        """Return (us_consensus, eu_consensus, pinnacle_prob, pinnacle_age_s).

        Pulls both regions (cached when possible). Pinnacle data is pulled
        from the eu response (eu regions include Pinnacle).

        NOTE: all three probabilities returned here are HOME-team referenced
        (scalar extracted from the home_prob field). The legacy
        `clv_snapshots.pinnacle_prob` column written by the caller is
        YES-side-adjusted. Analysis code must join with trades to resolve
        home↔YES/NO orientation before comparing columns.
        """
        current_region = self._get_odds_region()
        other_region = "eu" if current_region == "us" else "us"

        current_data = await self._fetch_odds(sport)
        self.REGION_OVERRIDE, saved = other_region, self.REGION_OVERRIDE
        try:
            other_data = await self._fetch_odds(sport)
        finally:
            self.REGION_OVERRIDE = saved

        us_data = current_data if current_region == "us" else other_data
        eu_data = current_data if current_region == "eu" else other_data

        us_game = next((g for g in us_data if g.get("id") == game.get("id")), None) if us_data else None
        eu_game = next((g for g in eu_data if g.get("id") == game.get("id")), None) if eu_data else None

        _us_cons = self._consensus_probability(us_game) if us_game else None
        _eu_cons = self._consensus_probability(eu_game) if eu_game else None
        _pin = self._pinnacle_probability(eu_game) if eu_game else None

        # Extract home_prob float for DB storage (single reference point).
        us_prob: Optional[float] = _us_cons["home_prob"] if _us_cons else None
        eu_prob: Optional[float] = _eu_cons["home_prob"] if _eu_cons else None
        pinnacle_prob: Optional[float] = _pin["home_prob"] if _pin else None

        eu_hash = _compute_pinnacle_payload_hash(eu_data) if eu_data else None
        pinnacle_age_s = _PINNACLE_AGE.observe("eu", sport, eu_hash)

        return us_prob, eu_prob, pinnacle_prob, pinnacle_age_s

    async def _fetch_odds(self, sport: str) -> list[dict]:
        """Fetch odds from The Odds API, shared-cached by (region, sport)."""
        if not self._odds_api_key:
            return []
        region = self._get_odds_region()
        cache_key = (region, sport)
        now = _odds_cache_now()
        hit = _ODDS_CACHE.get(cache_key)
        if hit is not None:
            cached_at, data, usage = hit
            if now - cached_at < _odds_cache_ttl(region):
                self.odds_api_usage = usage
                _PINNACLE_AGE.observe(region, sport, _compute_pinnacle_payload_hash(data))
                return data

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                    params={
                        "apiKey": self._odds_api_key,
                        "regions": region,
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                )
                resp.raise_for_status()
                usage = {
                    "used": int(resp.headers.get("x-requests-used", 0)),
                    "remaining": int(resp.headers.get("x-requests-remaining", 0)),
                    "last": int(resp.headers.get("x-requests-last", 0)),
                }
                data = resp.json()
                self.odds_api_usage = usage
                await self._check_quota(usage.get("remaining", 0))
                _ODDS_CACHE[cache_key] = (now, data, usage)
                pinnacle_hash = _compute_pinnacle_payload_hash(data)
                _PINNACLE_AGE.observe(region, sport, pinnacle_hash)
                return data
        except Exception as e:
            logger.error(f"Odds API error for {sport}: {e}")
            # Keep serving stale cache rather than failing all strategies
            if hit is not None:
                self.odds_api_usage = hit[2]
                return hit[1]
            return []

    # Home court/field advantage — NOT applied to consensus (already priced in by bookmakers)
    # Kept as reference only. Only used if Kalshi specifically misprices home/away.
    HOME_ADVANTAGE = {
        "basketball_nba": 0.0,
        "baseball_mlb": 0.0,
        "icehockey_nhl": 0.0,
    }

    def _detect_line_movement(self, game_id: str, home_prob: float, away_prob: float) -> float:
        """Detect sharp line movement since last scan.
        Returns movement magnitude (positive = home team getting stronger)."""
        prev = self._prev_odds.get(game_id)
        # Store current for next scan
        self._prev_odds[game_id] = {
            "home_prob": home_prob,
            "away_prob": away_prob,
            "time": time.time(),
        }
        if not prev:
            return 0.0
        elapsed = time.time() - prev["time"]
        if elapsed > 7200:  # ignore if > 2 hours old
            return 0.0
        movement = home_prob - prev["home_prob"]
        return movement

    def _home_court_adjustment(self, sport_key: str, is_home: bool) -> float:
        """Return probability adjustment for home court/field advantage."""
        adv = self.HOME_ADVANTAGE.get(sport_key, 0.0)
        return adv if is_home else -adv

    def _decimal_to_prob(self, decimal_odds: float) -> Optional[float]:
        """Convert decimal odds to implied probability. Returns None for invalid odds."""
        if decimal_odds <= 1.0:
            return None
        return 1.0 / decimal_odds

    async def _dispatch_entry(
        self,
        *,
        ticker: str,
        side: str,
        size: float,
        limit_price: float,
        minutes_to_start: Optional[float],
        matched_game: dict,
        sport: str,
        pinnacle_prob: Optional[float],
    ):
        """Place the order and record CLV in the background so the scanner
        loop isn't blocked by a v31 limit+TTL poll (can be up to 60s)."""
        try:
            order_result = await self._order_manager.market_order(
                ticker=ticker, side=side, size_usdc=size,
                price=limit_price, strategy=self.STRATEGY_NAME, market_id=ticker,
                minutes_to_start=minutes_to_start,
            )
            trade_id = (order_result or {}).get("trade_id")
            status = (order_result or {}).get("status")
            if trade_id is not None and status in ("paper_filled", "filled"):
                await self._record_entry_clv(
                    trade_id=trade_id,
                    ticker=ticker,
                    game=matched_game,
                    sport=sport,
                    pinnacle_prob=pinnacle_prob,
                )
        except Exception as e:
            logger.exception(
                f"[{self.STRATEGY_NAME}] entry dispatch failed for {ticker}: {e}"
            )

    async def _record_entry_clv(
        self,
        trade_id: int,
        ticker: str,
        game: dict,
        sport: str,
        pinnacle_prob: Optional[float],
    ):
        """Record entry snapshot with parallel us/eu consensus + freshness."""
        try:
            yes_ask, no_ask = self._client.get_market_prices(ticker)
        except Exception:
            yes_ask, no_ask = None, None
        kalshi_yes_bid, kalshi_yes_ask = None, None
        get_top = getattr(self._client, "get_top_of_book", None)
        if callable(get_top):
            try:
                tob = get_top(ticker)
                if tob and len(tob) >= 2:
                    kalshi_yes_bid, kalshi_yes_ask = tob[0], tob[1]
            except Exception:
                pass
        if kalshi_yes_ask is None:
            kalshi_yes_ask = yes_ask
        us_prob, eu_prob, _pin_dual, age_s = await self._dual_region_consensus(sport, game)
        try:
            await self._order_manager._db.record_clv_snapshot(
                trade_id=trade_id,
                snapshot_at="entry",
                yes_ask=yes_ask,
                no_ask=no_ask,
                pinnacle_prob=pinnacle_prob,
                us_consensus_prob=us_prob,
                eu_consensus_prob=eu_prob,
                pinnacle_age_s=age_s,
                kalshi_yes_bid=kalshi_yes_bid,
                kalshi_yes_ask=kalshi_yes_ask,
            )
        except Exception as e:
            logger.warning(f"[SPORTS] CLV entry snapshot failed for trade {trade_id}: {e}")

    def _has_sufficient_depth(self, ticker: str, side: str, size_usdc: float) -> bool:
        """True iff the ticker's orderbook on `side` has at least size_usdc of resting liquidity."""
        yes_depth, no_depth = self._client.get_order_book_depth(ticker)
        available = yes_depth if side == "yes" else no_depth
        return available >= size_usdc

    def _pinnacle_probability(self, game: dict) -> Optional[dict]:
        """Pinnacle-only implied probability (vig removed). Returns None if Pinnacle absent."""
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        for bm in game.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            home_prob = None
            away_prob = None
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    prob = self._decimal_to_prob(outcome.get("price", 0))
                    if prob is None:
                        continue
                    if outcome.get("name") == home_team:
                        home_prob = prob
                    elif outcome.get("name") == away_team:
                        away_prob = prob
            if home_prob is None or away_prob is None:
                return None
            total = home_prob + away_prob
            return {
                "home_team": home_team,
                "away_team": away_team,
                "home_prob": home_prob / total,
                "away_prob": away_prob / total,
            }
        return None

    def _consensus_probability(self, game: dict) -> Optional[dict]:
        """Vig-adjusted average implied probability across ALL bookmakers
        present in the response.

        Note: when running with regions=eu, Pinnacle is one of the books
        averaged here. This is intentional — V3.1's design is "EU
        sharp-book consensus (which naturally includes Pinnacle)". V4's
        sports_pinnacle_edge isolates Pinnacle separately, so the A/B
        question is "weighted sharp consensus vs Pinnacle-only signal",
        not "non-Pinnacle consensus vs Pinnacle".
        """
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
            # Full city name (all words except mascot)
            home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
            away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
            if home_city in title_lower and away_city in title_lower:
                return game
        return None

    def evaluate(self, kalshi_yes: float, kalshi_no: float, fair_prob: float,
                 line_movement: float = 0.0, home_adj: float = 0.0,
                 ticker: str = "") -> Optional[dict]:
        """V2 consensus edge with line movement. Base strategy."""
        min_edge = 0.03
        fee = self.config.KALSHI_FEE_PER_CONTRACT

        adjusted_prob = fair_prob
        if abs(line_movement) > 0.02:
            adjusted_prob += line_movement * 0.5
        adjusted_prob = max(0.03, min(0.97, adjusted_prob))

        yes_fee_pct = min(fee, kalshi_yes, 1 - kalshi_yes) / kalshi_yes if kalshi_yes > 0 else 0
        no_fee_pct = min(fee, kalshi_no, 1 - kalshi_no) / kalshi_no if kalshi_no > 0 else 0
        yes_edge = adjusted_prob - kalshi_yes * (1 + yes_fee_pct)
        no_edge = (1 - adjusted_prob) - kalshi_no * (1 + no_fee_pct)

        if yes_edge > min_edge and yes_edge >= no_edge:
            return {"side": "yes", "price": kalshi_yes, "edge": yes_edge, "strategy": "consensus"}
        elif no_edge > min_edge:
            return {"side": "no", "price": kalshi_no, "edge": no_edge, "strategy": "consensus"}
        return None

    def _get_trade_size(self, signal: dict) -> float:
        """Default sizing: 5% bankroll."""
        return self._order_manager.bankroll * 0.05

    def _get_limit_price(self, signal: dict, fair_prob: float) -> float:
        """Default: limit at adjusted fair value."""
        adj = signal.get("adj_prob", fair_prob)
        if signal["side"] == "yes":
            return round(adj, 2)
        return round(1 - adj, 2)

    def _format_log(self, ticker: str, title: str, signal: dict,
                    ya: float, na: float, fair_prob: float, size: float,
                    limit_price: float, **kwargs) -> str:
        """Default log format."""
        return (f"[SPORTS] {ticker}: {title[:40]} "
                f"fair={fair_prob:.2f} ask={ya:.2f}/{na:.2f} edge={signal['edge']:.2f} "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")

    async def scan_and_execute(self):
        if not getattr(self.config, "SPORTS_ENABLED", False):
            return
        if self._order_manager.is_circuit_breaker_active(strategy=self.STRATEGY_NAME):
            return
        now = time.time()
        scan_interval = self._get_scan_interval()
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

        # Check total exposure ONCE per cycle (not per market).
        # Floor the cap at $10 so a temporarily weird bankroll (e.g., paper
        # mode drained by stale settlements) doesn't silently block all sports
        # trading. Sports per-trade size is ~$1-$3 so $10 still allows 3-4
        # fresh trades in the worst case.
        total_exposure = self._get_live_sports_exposure()
        max_exposure = max(self._order_manager.bankroll * 0.30, 10.0)
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
                if ticker in self._traded_tickers:
                    continue  # permanent block — sports games are one-time events

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
                pinnacle = self._pinnacle_probability(matched_game)

                # Determine which team YES refers to using yes_sub_title (most reliable)
                # e.g., ticker -MIN has yes_sub_title="Minnesota", -DET has "Detroit"
                yes_team = market.get("yes_sub_title", "").lower()
                home = consensus["home_team"].lower()
                away = consensus["away_team"].lower()

                # First try exact short-name match for ambiguous cities (NY, LA, Chicago)
                home_short = ODDS_TO_KALSHI_SHORT.get(home, "")
                away_short = ODDS_TO_KALSHI_SHORT.get(away, "")
                is_yes_home = False
                if home_short and home_short == yes_team:
                    fair_prob = consensus["home_prob"]
                    is_yes_home = True
                elif away_short and away_short == yes_team:
                    fair_prob = consensus["away_prob"]
                else:
                    # Fallback: city name matching for non-ambiguous teams
                    home_city = " ".join(home.split()[:-1]) if len(home.split()) > 1 else home
                    away_city = " ".join(away.split()[:-1]) if len(away.split()) > 1 else away
                    if home_city in yes_team or yes_team in home_city:
                        fair_prob = consensus["home_prob"]
                        is_yes_home = True
                    elif away_city in yes_team or yes_team in away_city:
                        fair_prob = consensus["away_prob"]
                    else:
                        logger.debug(f"[SPORTS] Can't match YES team '{yes_team}' to {home}/{away}")
                        continue

                # V2 signals
                sport_key = sport_map.get(series, "")
                game_odds_id = f"{consensus['home_team']}_vs_{consensus['away_team']}"
                line_movement = self._detect_line_movement(
                    game_odds_id, consensus["home_prob"], consensus["away_prob"]
                )
                # Convert home-centric movement to YES-team-centric
                if not is_yes_home:
                    line_movement = -line_movement
                home_adj = self._home_court_adjustment(sport_key, is_yes_home)

                # Expose Pinnacle prob (YES-team perspective) for subclasses that want it
                if pinnacle is not None:
                    self._last_pinnacle_yes_prob = (
                        pinnacle["home_prob"] if is_yes_home else pinnacle["away_prob"]
                    )
                    # Payload-level freshness: seconds since the eu odds payload
                    # for this sport last changed. Subclasses (pinnacle_edge)
                    # reject stale signals. Observe is idempotent per hash —
                    # _fetch_odds already recorded first-seen, so this returns
                    # the current age of the payload we're about to act on.
                    eu_hash = _compute_pinnacle_payload_hash(odds_games)
                    self._last_pinnacle_age_s = _PINNACLE_AGE.observe(
                        self._get_odds_region(), sport_key, eu_hash
                    )
                else:
                    self._last_pinnacle_yes_prob = None
                    self._last_pinnacle_age_s = None

                signal = self.evaluate(ya, na, fair_prob,
                                       line_movement=line_movement, home_adj=home_adj,
                                       ticker=ticker)
                if not signal:
                    continue

                size = self._get_trade_size(signal)
                if size < 1.0:
                    logger.info(
                        f"[{self.STRATEGY_NAME}] {ticker}: signal valid but size "
                        f"${size:.2f} < $1.00 (bankroll=${self._order_manager.bankroll:.2f}), skip"
                    )
                    continue

                if self.MIN_DEPTH_USDC > 0 and not self._has_sufficient_depth(
                    ticker, signal["side"], self.MIN_DEPTH_USDC
                ):
                    logger.debug(
                        f"[{self.STRATEGY_NAME}] {ticker}: depth < ${self.MIN_DEPTH_USDC:.0f} "
                        f"on {signal['side']}, skip"
                    )
                    continue

                limit_price = self._get_limit_price(signal, fair_prob)
                limit_price = max(0.01, min(0.99, limit_price))

                # Entry price floor. For longshot the paper data carves out
                # a dead zone below ~20¢ (n=29 settled at −46% ROI) where
                # signals lose even under idealized execution. Above the
                # floor longshot paper ROI is solidly positive (+24–25%).
                if self.MIN_ENTRY_PRICE is not None and limit_price < self.MIN_ENTRY_PRICE:
                    logger.debug(
                        f"[{self.STRATEGY_NAME}] {ticker}: limit_price "
                        f"{limit_price:.3f} < floor {self.MIN_ENTRY_PRICE:.2f}, skip"
                    )
                    continue

                log_msg = self._format_log(
                    ticker=ticker, title=title, signal=signal,
                    ya=ya, na=na, fair_prob=fair_prob, size=size,
                    limit_price=limit_price, line_movement=line_movement,
                )
                logger.info(log_msg)
                # Mark BEFORE order to prevent duplicates even if order/DB fails
                self._traded_tickers[ticker] = now
                self._save_traded_tickers()
                minutes_to_start = _commence_time_to_minutes(matched_game.get("commence_time"))
                pin_prob_for_side = None
                if pinnacle is not None:
                    pin_prob_for_side = pinnacle["home_prob"] if is_yes_home else pinnacle["away_prob"]
                task = asyncio.create_task(self._dispatch_entry(
                    ticker=ticker, side=signal["side"], size=size,
                    limit_price=limit_price, minutes_to_start=minutes_to_start,
                    matched_game=matched_game, sport=sport_map.get(series, series),
                    pinnacle_prob=pin_prob_for_side,
                ))
                self._pending_entry_tasks.add(task)
                task.add_done_callback(self._pending_entry_tasks.discard)
                trades_attempted += 1
                traded_games.add(game_id)

        if now - self._last_summary_time >= 60:
            logger.info(
                f"[SPORTS] Scanned {markets_scanned} game markets, "
                f"{trades_attempted} trades"
            )
            self._last_summary_time = now
