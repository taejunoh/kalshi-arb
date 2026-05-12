import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from core.season import is_playoff

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
QUOTA_WARN_THRESHOLD = 5000    # 25%: recovery clears halt flag once we're back above
QUOTA_HALT_THRESHOLD = 1000    # 5%:  halt all sports strategies
QUOTA_ALERT_INTERVAL = 2000    # Informational alert every N calls used (10 per month)

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


_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_kalshi_ticker_datetime(ticker: str) -> Optional[datetime]:
    """Extract scheduled UTC datetime from a Kalshi sports ticker.
    Examples:
      KXNBAGAME-26APR21BOSBUF-BOS    → 2026-04-21 12:00 UTC (date-only, anchor noon)
      KXMLBGAME-26APR221940PHICHC-CHC → 2026-04-22 19:40 UTC (date+time)
      KXNHLGAME-26APR26COLLA-LA      → 2026-04-26 12:00 UTC

    Used by _match_kalshi_to_odds to disambiguate same-team multi-game
    series (NHL/NBA playoff best-of-7, MLB 3-game home stands) where
    the odds API returns multiple commence_times for the same matchup."""
    import re
    m = re.match(r"^KX[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(\d{4})?", ticker or "")
    if not m:
        return None
    yy, mmm, dd, hhmm = m.groups()
    month = _MONTH_MAP.get(mmm)
    if not month:
        return None
    try:
        year = 2000 + int(yy)
        day = int(dd)
        if hhmm and len(hhmm) == 4:
            hour, minute = int(hhmm[:2]), int(hhmm[2:])
        else:
            # Date-only ticker: anchor at noon UTC so closest-time matching
            # tolerates games that play in the afternoon or evening UTC.
            hour, minute = 12, 0
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return None


def _match_individual_sport_to_odds(
    yes_sub_title: str,
    odds_games: list[dict],
    kalshi_ticker: Optional[str] = None,
) -> Optional[dict]:
    """Match a Kalshi individual-sport market to Odds API game data.

    Kalshi yes_sub_title is the full player/fighter name (e.g.,
    'Carlos Alcaraz', 'Jeremiah Wells'). Match by:
      1. Exact (case-insensitive) match against home_team or away_team
      2. Token-overlap fallback: every space-token of the SHORTER name must
         appear in the LONGER name (handles 'Carlos Alcaraz' vs
         'Carlos Alcaraz Garfia' both directions)

    When the same player has multiple matches on different dates, the
    kalshi_ticker date disambiguates — same logic as _match_kalshi_to_odds.
    """
    if not yes_sub_title or not odds_games:
        return None
    yes_lc = yes_sub_title.strip().lower()
    if not yes_lc:
        return None
    yes_tokens = set(yes_lc.split())

    def _name_matches(odds_name: str) -> bool:
        if not odds_name:
            return False
        on_lc = odds_name.strip().lower()
        if on_lc == yes_lc:
            return True
        on_tokens = set(on_lc.split())
        if not on_tokens:
            return False
        if len(yes_tokens) <= len(on_tokens):
            return yes_tokens.issubset(on_tokens)
        return on_tokens.issubset(yes_tokens)

    candidates = [
        g for g in odds_games
        if _name_matches(g.get("home_team") or "")
        or _name_matches(g.get("away_team") or "")
    ]
    if not candidates:
        return None
    if len(candidates) == 1 or not kalshi_ticker:
        return candidates[0]
    ticker_dt = _parse_kalshi_ticker_datetime(kalshi_ticker)
    if not ticker_dt:
        return candidates[0]

    def _delta_seconds(g: dict) -> float:
        ct = g.get("commence_time")
        if not ct:
            return float("inf")
        try:
            game_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return float("inf")
        return abs((game_dt - ticker_dt).total_seconds())

    return min(candidates, key=_delta_seconds)


def _odds_cache_ttl(region: str) -> float:
    return _ODDS_CACHE_TTL_BY_REGION.get(region, _ODDS_CACHE_TTL_DEFAULT)


def _odds_cache_now() -> float:
    return time.time()


def _odds_cache_reset() -> None:
    _ODDS_CACHE.clear()

# Kalshi sports series
SPORTS_SERIES = [
    "KXNBAGAME", "KXWNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXNPBGAME", "KXKBOGAME",
    "KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME", "KXUCLGAME", "KXBUNDESLIGAGAME",
    "KXATPMATCH", "KXWTAMATCH", "KXUFCFIGHT",  # NEW 2026-05-11 — individual-sport h2h match markets
]

# Game-suffix tokens used to extract a sport short code from a ticker.
# Order matters: WNBAGAME must come before NBAGAME because "NBAGAME" is a
# substring of "WNBAGAME" — first-match wins in the `next((s for s in ... if s in ticker))`
# pattern, so a WNBA ticker would classify as NBA if NBAGAME came first.
# 2026-05-03 re-added NPB/KBO/BUN: prior 2026-05-02 removals used Kalshi OI
# as the tradability metric, but the actual strategy gate is top-of-book
# DEPTH (kalshi_yes_ask × kalshi_yes_ask_size). Re-checked metrics:
#   - NPB: depth p50 $122, 100% of obs ≥ $20 → fully tradeable
#   - KBO: depth p50 $90,  100% of obs ≥ $20 → fully tradeable
#   - BUN: depth p50 $20,  51% of obs ≥ $20 → half-tradeable (paper sim
#     and live depth gate will both cull the thin half symmetrically; the
#     5/9 paper backtest will tell us if the surviving half carries alpha)
SPORT_GAME_TOKENS = (
    "WNBAGAME",  # MUST come before NBAGAME — substring collision
    "NBAGAME", "MLBGAME", "NHLGAME",
    "NPBGAME", "KBOGAME",
    "MLSGAME", "EPLGAME", "LALIGAGAME", "UCLGAME", "BUNDESLIGAGAME",
    "ATPMATCH", "WTAMATCH", "UFCFIGHT",  # NEW 2026-05-11 — individual-sport h2h
)

# Token → short sport code. Most tokens use the first 3 chars (NBA, MLB, NHL,
# NPB, KBO, MLS, EPL, LAL, UCL, BUN) but WNBA is 4 letters — explicit map
# avoids "WNBAGAME"[:3] == "WNB" silently misclassifying WNBA tickers in
# OBSERVATION_ONLY/SPORT_BLOCKLIST/PLAYOFF_BLOCKLIST/ALLOWED_CELLS gates.
SPORT_SHORT_BY_TOKEN = {
    "WNBAGAME": "WNBA",
    "NBAGAME": "NBA", "MLBGAME": "MLB", "NHLGAME": "NHL",
    "NPBGAME": "NPB", "KBOGAME": "KBO",
    "MLSGAME": "MLS", "EPLGAME": "EPL", "LALIGAGAME": "LAL",
    "UCLGAME": "UCL", "BUNDESLIGAGAME": "BUN",
    "ATPMATCH": "ATP", "WTAMATCH": "WTA", "UFCFIGHT": "UFC",  # NEW 2026-05-11
}

# Tennis tournament metadata for cell-expansion analysis. Keys are Odds API
# endpoint slugs (matches tournament_slug column in odds_observations).
# Values document tier (GS/M1000/500/250), surface (clay/grass/hard),
# and match format (BO3/BO5). MMA has no equivalent — UFC card position
# is not derivable from Odds API alone.
TENNIS_SLUG_META = {
    # Grand Slams (Best of 5 for ATP men, Best of 3 for WTA women)
    "tennis_atp_aus_open_singles":    {"tier": "GS",      "surface": "hard",  "format": "BO5"},
    "tennis_atp_french_open":         {"tier": "GS",      "surface": "clay",  "format": "BO5"},
    "tennis_atp_wimbledon":           {"tier": "GS",      "surface": "grass", "format": "BO5"},
    "tennis_atp_us_open":             {"tier": "GS",      "surface": "hard",  "format": "BO5"},
    "tennis_wta_aus_open_singles":    {"tier": "GS",      "surface": "hard",  "format": "BO3"},
    "tennis_wta_french_open":         {"tier": "GS",      "surface": "clay",  "format": "BO3"},
    "tennis_wta_wimbledon":           {"tier": "GS",      "surface": "grass", "format": "BO3"},
    "tennis_wta_us_open":             {"tier": "GS",      "surface": "hard",  "format": "BO3"},
    # ATP Masters 1000 (all BO3)
    "tennis_atp_indian_wells":        {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    "tennis_atp_miami_open":          {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    "tennis_atp_monte_carlo_masters": {"tier": "M1000",   "surface": "clay",  "format": "BO3"},
    "tennis_atp_madrid_open":         {"tier": "M1000",   "surface": "clay",  "format": "BO3"},
    "tennis_atp_italian_open":        {"tier": "M1000",   "surface": "clay",  "format": "BO3"},
    "tennis_atp_canadian_open":       {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    "tennis_atp_cincinnati_open":     {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    "tennis_atp_shanghai_masters":    {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    "tennis_atp_paris_masters":       {"tier": "M1000",   "surface": "hard",  "format": "BO3"},
    # WTA 1000 (all BO3)
    "tennis_wta_indian_wells":        {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    "tennis_wta_miami_open":          {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    "tennis_wta_madrid_open":         {"tier": "WTA1000", "surface": "clay",  "format": "BO3"},
    "tennis_wta_italian_open":        {"tier": "WTA1000", "surface": "clay",  "format": "BO3"},
    "tennis_wta_canadian_open":       {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    "tennis_wta_cincinnati_open":     {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    "tennis_wta_china_open":          {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    "tennis_wta_wuhan_open":          {"tier": "WTA1000", "surface": "hard",  "format": "BO3"},
    # ATP 500 / 250 + smaller WTA — populate as encountered. For now in-scope:
    "tennis_atp_barcelona_open":      {"tier": "ATP500",  "surface": "clay",  "format": "BO3"},
    "tennis_atp_dubai":               {"tier": "ATP500",  "surface": "hard",  "format": "BO3"},
    "tennis_atp_qatar_open":          {"tier": "ATP500",  "surface": "hard",  "format": "BO3"},
    "tennis_atp_munich":              {"tier": "ATP250",  "surface": "clay",  "format": "BO3"},
    "tennis_atp_china_open":          {"tier": "ATP500",  "surface": "hard",  "format": "BO3"},
    "tennis_wta_charleston_open":     {"tier": "WTA500",  "surface": "clay",  "format": "BO3"},
    "tennis_wta_stuttgart_open":      {"tier": "WTA500",  "surface": "clay",  "format": "BO3"},
    "tennis_wta_dubai":               {"tier": "WTA500",  "surface": "hard",  "format": "BO3"},
    "tennis_wta_qatar_open":          {"tier": "WTA500",  "surface": "hard",  "format": "BO3"},
}

# Regex for extracting round from a Kalshi tennis market title.
# Titles follow: "Will <player> win the <Last1> vs <Last2>: <Round> match?"
_TENNIS_ROUND_RE = re.compile(
    r":\s*(Round Of \d+|Quarterfinal|Semifinal|Final)\s*match\?",
    re.IGNORECASE,
)


def parse_tennis_round(kalshi_title: str) -> Optional[str]:
    """Extract round name from a tennis Kalshi market title.

    Tennis Kalshi titles follow the pattern:
        "Will <player> win the <Last1> vs <Last2>: <Round> match?"
    where <Round> is "Round Of 128/64/32/16", "Quarterfinal",
    "Semifinal", or "Final".

    Returns a normalized round code: "R128", "R64", "R32", "R16",
    "QF", "SF", "F", or None if no round token matches.
    """
    if not kalshi_title:
        return None
    m = _TENNIS_ROUND_RE.search(kalshi_title)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    if raw.startswith("round of "):
        try:
            n = int(raw.split()[-1])
            return f"R{n}"
        except ValueError:
            return None
    if raw == "quarterfinal":
        return "QF"
    if raw == "semifinal":
        return "SF"
    if raw == "final":
        return "F"
    return None


# Sports whose Pinnacle h2h market includes a "Draw" outcome. Probability
# helpers normalize home + away + draw to sum=1 for these.
THREE_WAY_SPORTS_SHORT = frozenset({"MLS", "EPL", "LAL", "UCL"})


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
    # NPB — only the team where Kalshi drops the corporate sponsor needs disambig.
    # Other 11 NPB teams' city portion already matches Kalshi yes_sub_title.
    "fukuoka softbank hawks": "fukuoka hawks",
    # KBO — Kalshi uses identical "City Mascot" form; no overrides needed.

    # EPL (Odds API name → Kalshi yes_sub_title, both lowercased).
    "brighton and hove albion": "brighton",
    "newcastle united": "newcastle",
    "nottingham forest": "nottingham",
    "tottenham hotspur": "tottenham",
    "west ham united": "west ham",
    "wolverhampton wanderers": "wolverhampton",

    # La Liga
    "alavés": "alaves",
    "athletic bilbao": "bilbao",
    "atlético madrid": "atletico",
    "ca osasuna": "osasuna",
    "elche cf": "elche",
    "rayo vallecano": "vallecano",

    # Bundesliga
    "1. fc heidenheim": "heidenheim",
    "1. fc köln": "fc köln",
    "bayer leverkusen": "leverkusen",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "m´gladbach",
    "eintracht frankfurt": "frankfurt",
    "fc st. pauli": "st. pauli",
    "fsv mainz 05": "mainz",
    "hamburger sv": "hamburg",
    "rb leipzig": "leipzig",
    "sc freiburg": "freiburg",
    "tsg hoffenheim": "hoffenheim",
    "vfb stuttgart": "stuttgart",
    "vfl wolfsburg": "wolfsburg",
    "werder bremen": "bremen",

    # UCL (overlaps with EPL/La Liga; same alias rules apply)
    "paris saint germain": "psg",

    # MLS
    "atlanta united fc": "atlanta",
    "austin fc": "austin",
    "cf montreal": "montreal",
    "charlotte fc": "charlotte",
    "colorado rapids": "colorado",
    "columbus crew sc": "columbus",
    "d.c. united": "dc united",
    "fc cincinnati": "cincinnati",
    "fc dallas": "dallas",
    "houston dynamo": "houston",
    "inter miami cf": "miami",
    "la galaxy": "los angeles g",
    "los angeles fc": "los angeles f",
    "minnesota united fc": "minnesota",
    "nashville sc": "nashville",
    "new england revolution": "new england",
    "new york city fc": "new york city",
    "new york red bulls": "new york rb",
    "orlando city sc": "orlando",
    "philadelphia union": "philadelphia",
    "portland timbers": "portland",
    "real salt lake": "salt lake",
    "san jose earthquakes": "san jose",
    "seattle sounders fc": "seattle",
    "sporting kansas city": "kansas city",
    "st. louis city sc": "saint louis",
    "toronto fc": "toronto",
    "vancouver whitecaps fc": "vancouver",
}


class SportsTraderStrategy:
    """Trade sports game markets by comparing Kalshi prices to sportsbook odds."""

    TRADED_TICKERS_FILE = "data/sports_traded_tickers.json"
    STRATEGY_NAME = "sports_trader"
    REGION_OVERRIDE: Optional[str] = None  # subclass can pin a specific Odds API region
    SCAN_INTERVAL_OVERRIDE: Optional[float] = None  # subclass can override scan cadence (us retail books tick faster)
    MIN_DEPTH_USDC = 0.0  # Subclasses override to enforce orderbook depth before market order
    # Thin-book early-fire gate (2026-05-11). Skip when (mts >= threshold)
    # AND (book depth < threshold). Mechanism: trades placed >=12h before
    # game start with book depth <$500 USD show consistent loss across
    # underdog family — Kalshi price hasn't found consensus yet that early
    # on thin books. Both attrs must be set; None disables. Enabled in
    # sports_underdog.py base only (cascades v31-v35); v36/v37/v38 inherit
    # but never trigger (deeper books or MAX_MTS_AT_ENTRY caps).
    THIN_BOOK_EARLY_GATE_MTS_MIN_MINUTES: Optional[float] = None
    THIN_BOOK_EARLY_GATE_DEPTH_MAX_USDC: Optional[float] = None
    # Minimum entry price (0–1). Used by variants whose paper-settled data
    # shows a structural dead zone below some floor (e.g. underdog/midshot
    # v31 at sub-20¢: 0% win on 27/36 trades). Subclasses set to e.g. 0.20;
    # None = no filter.
    MIN_ENTRY_PRICE: Optional[float] = None
    # Maximum entry price (0–1). Mirror of MIN_ENTRY_PRICE for the upper
    # boundary. Used when the strategy's natural ceiling shows a cliff —
    # underdog v31 at 0.40c hits payoff≈2.0 fee-boundary (n=62, -25% ROI
    # vs +11% at 0.39c). Subclasses set to e.g. 0.39 to drop only the
    # cliff cent. None = no filter.
    MAX_ENTRY_PRICE: Optional[float] = None
    # Dead-zone gate (lo, hi): skip limit_price in [lo, hi). Used when the
    # dead zone has a lower bound above strategy's overall min price, e.g.
    # longshot_v31 and v4_edge show <10¢ winning but 10–20¢ zero-win.
    # Independent of MIN_ENTRY_PRICE; both gates can compose.
    ENTRY_PRICE_DEAD_ZONE: Optional[tuple[float, float]] = None
    # Pre-game-only gate. Block entries when minutes_to_start <= 0 (live game).
    # 2026-04-20 segmentation found longshot_v31 pre-game ROI +134% (live, n=7)
    # / +54% (paper, n=10) vs live-game ROI −30% / −9% — same direction across
    # all 3 v31 variants but most pronounced on longshot. Strategies that gain
    # alpha during live volatility (v4_edge: +59% live-game) leave this None.
    PRE_GAME_ONLY: bool = False
    # Max minutes-to-start at entry. Blocks entries placed more than this
    # many minutes in advance of game start. None = no upper cap.
    # 2026-05-03 added for v36_nhl_playoff after data showed v31 underdog
    # NHL playoff -4.5% ROI on the 35-45c+50-55c sweet spot was driven by
    # entries placed days early (mts=2000-4000 minutes); the observer
    # baseline +33% was measured on T-1h to T-24h FIRST obs window.
    # Limiting to 24h captures the price-discovery sweet spot where sharp
    # money has converged on a fair line but Kalshi may still lag.
    MAX_MTS_AT_ENTRY: Optional[float] = None
    # Per-game aggregate stake cap (USDC). 2026-04-17 paper analysis found
    # v31 fires median 6 trades per game with 75% intra-game outcome
    # correlation — capping aggregate per-game stake at $9 kept +23.1% ROI
    # on kept trades vs +19.7% on the uncapped pool, a capital-efficiency
    # win. v33 variants set this; state is shared across v33 siblings via
    # OrderManager._v33_game_spend so the cap applies to the aggregate, not
    # per-strategy. None = no cap.
    PER_GAME_CAP_USDC: Optional[float] = None
    # Sport-blocklist. Skip entries for tickers in specified Kalshi series
    # (e.g. {"NHL"} to block all NHLGAME-* tickers). Used when a strategy
    # × sport combo shows no edge in either region. Sport codes match the
    # 3-letter ticker prefix: NBA, MLB, NHL.
    SPORT_BLOCKLIST: set[str] = frozenset()
    # Playoff-blocklist. Skip entries when the ticker's sport is in this
    # set AND falls within its playoff window per core.season calendar.
    # Distinct from SPORT_BLOCKLIST in that it auto-deactivates when the
    # sport returns to regular season — no manual cleanup needed when
    # next regular season starts. Composes with ALLOWED_CELLS (both must
    # pass). Built 2026-04-30 for the v3X NBA playoff regime-mismatch
    # finding: midshot/underdog variants bleed -25 to -90% on NBA playoff
    # cells but the same logic worked in NBA regular season per matrix.
    PLAYOFF_BLOCKLIST: frozenset = frozenset()
    # Fine-grained cell matrix. When set, only (sport, phase) tuples in
    # ALLOWED_CELLS are entered. Sport ∈ {"NBA","MLB","NHL"}, phase ∈
    # {"pre","live"}. Takes precedence over PRE_GAME_ONLY and
    # SPORT_BLOCKLIST — when ALLOWED_CELLS is not None those two attrs
    # are ignored. Built 2026-04-21 to capture the matrix's asymmetric
    # cells (e.g. us region wins MLB underdog live but loses MLB under
    # pre; both V3.1 and V3.4 coexist with complementary cell sets).
    ALLOWED_CELLS: Optional[frozenset] = None
    # Per-cell price band whitelist. Maps (sport, phase) -> list of allowed
    # [low, high] price intervals. Signal price must fall inside at least one
    # interval for the cell. When set, subsumes PRICE_MIN/PRICE_MAX/DEAD_ZONE
    # for the cells that appear here. Cells absent from this dict but present
    # in ALLOWED_CELLS use the legacy gates. Built 2026-04-26 for v4_edge's
    # asymmetric per-cell band findings (e.g. MLB live wins at 20-30¢ but
    # NHL live wins at 30-35¢).
    CELL_PRICE_BANDS: Optional[dict[tuple[str, str], list[tuple[float, float]]]] = None
    # Playoff-only gate. When True, skip entries on regular-season tickers
    # (using core.season.is_playoff classifier). Additive with ALLOWED_CELLS —
    # both must pass. Built 2026-04-30 for sports_underdog_v36_playoff which
    # targets NBA playoff-specific underdog mispricing (35¢+ floor cell).
    PLAYOFF_ONLY: bool = False
    # Sports added to sport_map for data collection (observer + odds_observations)
    # but not yet promoted to live trading. Subclasses skip these in
    # scan_and_execute regardless of other gates. To promote a sport, override
    # this set in the subclass with the entry removed. Soccer leagues require
    # 3-way (draw) handling before any live promotion can be considered.
    OBSERVATION_ONLY_SPORTS: frozenset = frozenset({
        "WNBA",  # 2026-05-09 wired (KXWNBAGAME). Observe through ~5/16 opener
                 # + first 1-2 weeks before any promotion. Pinnacle line quality
                 # on women's basketball historically thinner than men's.
        "NPB", "KBO",
        "MLS", "EPL", "LAL", "UCL", "BUN",
        # 2026-05-11: Individual-sport observers. ATP/WTA tennis + UFC MMA.
        # 14-day data window → promotion_eval bucket-replay decides archetype
        # (longshot 5-25¢ / underdog 25-39¢ / mid-fav 39-75¢ / chalk 75+).
        # No strategy file yet — that's the follow-up spec.
        "ATP", "WTA", "UFC",
    })
    # Minimum bookmaker count required in the consensus before evaluating a
    # signal. None = no gate (legacy behavior). Recommended for newly-added
    # sports where coverage is thin: 5 for 2-way, 8 for soccer (3-way splits
    # signal across legs). 2026-05-02: NHL min n_books=2, MLS min=3, MLB min=5
    # in observed scans — without this gate a 2-book "consensus" could trigger
    # a trade with sub-noise alpha.
    MIN_N_BOOKS: Optional[int] = None

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
                if any(s in p.get("ticker", "") for s in SPORT_GAME_TOKENS)
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
        """Monitor Odds API quota with bucket-based alerts.

        - Every QUOTA_ALERT_INTERVAL (2000) calls used: one informational alert.
          Bucket-based dedup means each bucket fires at most once per cycle.
        - <HALT (5%): set _quota_exhausted flag. Sports strategies stop entering.
        - >=WARN (25%): clear halt flag (monthly reset or recovery).
        """
        notifier = getattr(self._order_manager, "_notifier", None)
        om = self._order_manager
        used = max(0, QUOTA_MONTHLY_ALLOWANCE - remaining)
        current_bucket = used // QUOTA_ALERT_INTERVAL

        if om._quota_last_alert_bucket is None:
            # Seed on first fetch after boot so restarts don't re-fire the current bucket.
            om._quota_last_alert_bucket = current_bucket
        elif used < om._quota_last_alert_bucket * QUOTA_ALERT_INTERVAL:
            # Monthly reset detected: provider rolled quota over, `used` dropped
            # below prior bucket floor. Treat as fresh cycle.
            om._quota_last_alert_bucket = current_bucket
        elif current_bucket > om._quota_last_alert_bucket:
            om._quota_last_alert_bucket = current_bucket
            if notifier:
                await notifier.send(
                    f"📊 Odds API quota: {used:,} used / {remaining:,} remaining.",
                    msg_type=f"quota_bucket_{current_bucket}",
                )

        if remaining < QUOTA_HALT_THRESHOLD:
            if not om._quota_exhausted:
                om._quota_exhausted = True
                logger.error(
                    f"[QUOTA] Odds API halt threshold: {remaining} remaining. Sports strategies halted."
                )
                if notifier:
                    await notifier.send(
                        f"⚠️ <b>Odds API quota at 5%</b>\nOnly {remaining} calls remaining.\nSports strategies halted until quota resets.",
                        msg_type="quota_halt",
                    )
        elif remaining >= QUOTA_WARN_THRESHOLD and om._quota_exhausted:
            om._quota_exhausted = False
            logger.info(
                f"[QUOTA] Odds API quota recovered: {remaining} remaining. Sports strategies resumed."
            )
            if notifier:
                await notifier.send(
                    f"✅ Odds API quota recovered: {remaining} remaining.\nSports strategies resumed.",
                    msg_type="quota_recovery",
                )

    async def _dual_region_consensus_dicts(
        self, sport: str, game: dict
    ) -> tuple[Optional[dict], Optional[dict], Optional[dict], Optional[int]]:
        """Return (us_consensus_dict, eu_consensus_dict, pinnacle_dict, pinnacle_age_s).

        Each dict has {home_team, away_team, home_prob, away_prob} for 2-way
        sports plus `draw_prob` for 3-way (soccer). Pulls both regions (cached
        when possible). Pinnacle data is pulled from the eu response (eu
        regions include Pinnacle).
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

        eu_hash = _compute_pinnacle_payload_hash(eu_data) if eu_data else None
        pinnacle_age_s = _PINNACLE_AGE.observe("eu", sport, eu_hash)

        return _us_cons, _eu_cons, _pin, pinnacle_age_s

    async def _dual_region_consensus(
        self, sport: str, game: dict
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[int]]:
        """Return (us_consensus, eu_consensus, pinnacle_prob, pinnacle_age_s).

        Backward-compat 4-tuple of HOME-team referenced floats. New callers
        should prefer `_dual_region_consensus_dicts` which preserves the full
        probability dict (including draw_prob for soccer).

        NOTE: all three probabilities returned here are HOME-team referenced
        (scalar extracted from the home_prob field). The legacy
        `clv_snapshots.pinnacle_prob` column written by the caller is
        YES-side-adjusted. Analysis code must join with trades to resolve
        home↔YES/NO orientation before comparing columns.
        """
        us_cons, eu_cons, pin, age_s = await self._dual_region_consensus_dicts(sport, game)
        us_prob: Optional[float] = us_cons["home_prob"] if us_cons else None
        eu_prob: Optional[float] = eu_cons["home_prob"] if eu_cons else None
        pin_prob: Optional[float] = pin["home_prob"] if pin else None
        return us_prob, eu_prob, pin_prob, age_s

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
        "basketball_wnba": 0.0,
        "baseball_mlb": 0.0,
        "icehockey_nhl": 0.0,
        "baseball_npb": 0.0,
        "baseball_kbo": 0.0,
        "soccer_usa_mls": 0.0,
        "soccer_epl": 0.0,
        "soccer_spain_la_liga": 0.0,
        "soccer_uefa_champs_league": 0.0,
        "soccer_germany_bundesliga": 0.0,
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
        is_yes_home: Optional[bool] = None,
    ):
        """Place the order and record CLV in the background so the scanner
        loop isn't blocked by a v31 limit+TTL poll (can be up to 60s)."""
        try:
            order_result = await self._order_manager.market_order(
                ticker=ticker, side=side, size_usdc=size,
                price=limit_price, strategy=self.STRATEGY_NAME, market_id=ticker,
                minutes_to_start=minutes_to_start,
            ) or {}
            status = order_result.get("status")
            reason = order_result.get("reason")
            # Roll back the pre-dispatch dedup mark when the order never
            # reached Kalshi's book due to a balance check. Lets the next
            # scan re-attempt the same signal once cash is available, instead
            # of permanently burning the ticker for this strategy. Other
            # failure modes (TTL unfilled, Kalshi reject after place_order)
            # keep the dedup so we don't churn the same signal repeatedly
            # against the book.
            if status == "failed" and reason in (
                "insufficient_balance",
                "balance_check_failed",
                "pre_record_failed",
            ):
                if self._traded_tickers.pop(ticker, None) is not None:
                    self._save_traded_tickers()
                    logger.info(
                        f"[{self.STRATEGY_NAME}] dedup rollback {ticker}: {reason}"
                    )
                return
            trade_id = order_result.get("trade_id")
            if trade_id is not None and status in ("paper_filled", "filled"):
                await self._record_entry_clv(
                    trade_id=trade_id,
                    ticker=ticker,
                    game=matched_game,
                    sport=sport,
                    pinnacle_prob=pinnacle_prob,
                    is_yes_home=is_yes_home,
                )
        except Exception as e:
            logger.exception(
                f"[{self.STRATEGY_NAME}] entry dispatch failed for {ticker}: {e}"
            )
            # 2026-05-11: fallback Telegram alert for any dispatch failure
            # outside of record_trade (e.g., _record_entry_clv DB lock,
            # network glitch). The order_manager already alerts on
            # record_trade orphans; this catches the rest so a 5/9-style
            # silent failure can't recur unnoticed.
            notifier = getattr(self._order_manager, "_notifier", None)
            if notifier:
                text = (
                    f"⚠️ <b>Dispatch failure</b>\n"
                    f"Strategy: {self.STRATEGY_NAME}\n"
                    f"Market: {ticker}\n"
                    f"Side: {side} | Size: ${size:.2f} @ {limit_price:.3f}\n"
                    f"Error: {type(e).__name__}: {e}\n"
                    f"Check journalctl + trades table for this ticker."
                )
                try:
                    asyncio.create_task(
                        notifier.send(text, msg_type="dispatch_failure")
                    )
                except RuntimeError:
                    pass

    async def _record_entry_clv(
        self,
        trade_id: int,
        ticker: str,
        game: dict,
        sport: str,
        pinnacle_prob: Optional[float],
        is_yes_home: Optional[bool] = None,
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
        # Snapshot book depth at entry (2026-04-24). Recorded regardless of
        # the strategy's MIN_DEPTH_USDC gate so post-hoc analysis can filter
        # by depth (e.g., chalk_shadow has gate=0 for hypothesis testing).
        kalshi_yes_depth, kalshi_no_depth = None, None
        try:
            kalshi_yes_depth, kalshi_no_depth = self._client.get_order_book_depth(ticker)
        except Exception:
            pass
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
                kalshi_yes_depth=kalshi_yes_depth,
                kalshi_no_depth=kalshi_no_depth,
                is_yes_home=is_yes_home,
            )
        except Exception as e:
            logger.warning(f"[SPORTS] CLV entry snapshot failed for trade {trade_id}: {e}")

    def _has_sufficient_depth(self, ticker: str, side: str, size_usdc: float) -> bool:
        """True iff the ticker's orderbook on `side` has at least size_usdc of resting liquidity."""
        yes_depth, no_depth = self._client.get_order_book_depth(ticker)
        available = yes_depth if side == "yes" else no_depth
        return available >= size_usdc

    def _pinnacle_probability(self, game: dict) -> Optional[dict]:
        """Pinnacle-only implied probability (vig removed). Returns None if Pinnacle absent.

        For 2-way h2h sports (baseball/basketball/hockey) returns
        {home_prob, away_prob} normalized to sum=1.
        For 3-way h2h sports (soccer) the Pinnacle outcome list includes a
        "Draw" entry; the result also contains `draw_prob`, with all three
        probabilities normalized to sum=1.
        """
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        for bm in game.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            home_prob = None
            away_prob = None
            draw_prob = None
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    prob = self._decimal_to_prob(outcome.get("price", 0))
                    if prob is None:
                        continue
                    name = outcome.get("name") or ""
                    if name == home_team:
                        home_prob = prob
                    elif name == away_team:
                        away_prob = prob
                    elif name.lower() == "draw":
                        draw_prob = prob
            if home_prob is None or away_prob is None:
                return None
            if draw_prob is not None:
                total = home_prob + away_prob + draw_prob
                return {
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_prob": home_prob / total,
                    "away_prob": away_prob / total,
                    "draw_prob": draw_prob / total,
                }
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
        draw_probs = []
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    odds = outcome.get("price", 0)
                    prob = self._decimal_to_prob(odds)
                    if prob is None:
                        continue
                    name = outcome.get("name") or ""
                    if name == home_team:
                        home_probs.append(prob)
                    elif name == away_team:
                        away_probs.append(prob)
                    elif name.lower() == "draw":
                        draw_probs.append(prob)
        if not home_probs or not away_probs:
            return None
        avg_home = sum(home_probs) / len(home_probs)
        avg_away = sum(away_probs) / len(away_probs)
        # n_books = books that contributed BOTH home and away outcomes. Used
        # downstream as a coverage signal (NHL min 2, MLS min 3, NPB always 9
        # vs MLB/UCL 22+); thin coverage means consensus is noisier than its
        # numeric value implies.
        n_books = min(len(home_probs), len(away_probs))
        if draw_probs:
            avg_draw = sum(draw_probs) / len(draw_probs)
            total = avg_home + avg_away + avg_draw
            return {
                "home_team": home_team,
                "away_team": away_team,
                "home_prob": avg_home / total,
                "away_prob": avg_away / total,
                "draw_prob": avg_draw / total,
                "n_books": n_books,
            }
        # Normalize to sum to 1.0 (remove vig)
        total = avg_home + avg_away
        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_prob": avg_home / total,
            "away_prob": avg_away / total,
            "n_books": n_books,
        }

    def _match_kalshi_to_odds(self, kalshi_title: str, odds_games: list[dict],
                              kalshi_ticker: Optional[str] = None) -> Optional[dict]:
        """Match a Kalshi market title to odds API game data.

        Kalshi uses city names ("Phoenix at Charlotte"), while Odds API uses
        full team names ("Phoenix Suns" vs "Charlotte Hornets"). Match by
        checking if FULL city name of BOTH teams appears in the title.
        Single-word fallback only for single-word cities to avoid
        "San" matching both "San Diego" and "San Francisco".

        When the same matchup appears multiple times (NHL/NBA playoff
        best-of-7, MLB 3-game home stands), `kalshi_ticker` enables
        date-aware disambiguation: of the matching games, the one whose
        commence_time is closest to the ticker's scheduled datetime wins.
        Without `kalshi_ticker` the legacy first-match behaviour is used.
        """
        title_lower = kalshi_title.lower()

        def _city_in_title(name: str) -> bool:
            """True iff any city-prefix variant of `name` appears in the title.
            Tries the raw name AND its ODDS_TO_KALSHI_SHORT alias (when set);
            this covers both NHL (raw "los angeles kings" matches title's
            "Los Angeles") and soccer (alias "newcastle" matches title's
            "Newcastle" when raw is "Newcastle United").
            """
            for variant in (name, ODDS_TO_KALSHI_SHORT.get(name)):
                if not variant:
                    continue
                parts = variant.split()
                city = " ".join(parts[:-1]) if len(parts) > 1 else variant
                if city and city in title_lower:
                    return True
            return False

        candidates: list[dict] = []
        for game in odds_games:
            home_raw = (game.get("home_team") or "").lower()
            away_raw = (game.get("away_team") or "").lower()
            if _city_in_title(home_raw) and _city_in_title(away_raw):
                candidates.append(game)
        if not candidates:
            return None
        if len(candidates) == 1 or not kalshi_ticker:
            return candidates[0]
        ticker_dt = _parse_kalshi_ticker_datetime(kalshi_ticker)
        if not ticker_dt:
            return candidates[0]
        def _delta_seconds(g: dict) -> float:
            ct = g.get("commence_time")
            if not ct:
                return float("inf")
            try:
                game_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return float("inf")
            return abs((game_dt - ticker_dt).total_seconds())
        return min(candidates, key=_delta_seconds)

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
        }

        all_odds = {}
        for series, sport_key in sport_map.items():
            games = await self._fetch_odds(sport_key)
            all_odds[series] = games

        trades_attempted = 0
        markets_scanned = 0
        max_trades_per_cycle = 3
        hit_limit = False

        # Check total exposure ONCE per cycle (not per market). Cap is 70% of
        # REAL Kalshi total capital (free cash + ALL open position value,
        # incl. mentions positions), not paper bankroll. Paper bankroll drifts
        # from Kalshi reality — today v32/v33 paper variants drained paper
        # bankroll $692→$244 in minutes, tightening the sports cap from $207
        # to $73 and blocking live scans for hours even though real Kalshi
        # exposure hadn't moved. Kalshi total is the correct denominator for
        # a cap gating Kalshi exposure. Floor at $10 so a Kalshi read failure
        # doesn't silently stop all sports trading.
        # 2026-04-17: 30% → 50% (30% × $739 was at the edge of live exposure).
        # 2026-04-20: 50% → 70% (v4_edge added to live; 50% was limiting room
        # for the full v31+v4 quartet to fire concurrent signals).
        # 2026-04-26: 70% → 80% (post 4→20 v31/v32 underdog raise + LIMIT_AGGRESSION
        # 2→15 increased single-signal-group exposure to ~$50-80; 70% binding
        # was projected for evening NBA activity windows).
        # 2026-04-26 (later same day): 80% → 90%. Sports-only operation
        # (mentions/econ live but not in scan loop), so the "reserve for other
        # domains" rationale doesn't apply. The remaining ~10% reserve covers
        # free cash needed for the next 1-2 fills against Kalshi balance check.
        # 2026-04-26 (later again): 90% → 100%. Cap is now tautological
        # (sports_exp ≤ kalshi_total always when sports_exp ⊆ all_exp). Per-order
        # Kalshi balance check (free cash ≥ count × max_price + fee) is the
        # natural floor; cap added no value beyond that. Observed 4 PHIATL
        # entries blocked by balance check at 06:57-06:59 UTC with cap at 90%
        # confirmed cap is not the binding constraint — cash deposit is the
        # remedy, not cap further raises.
        try:
            all_positions = self._client.get_positions() or []
        except Exception:
            all_positions = []
        all_exp = sum(
            float(p.get("market_exposure_dollars", 0))
            for p in all_positions
            if float(p.get("position_fp", 0)) != 0
        )
        sports_exp = sum(
            float(p.get("market_exposure_dollars", 0))
            for p in all_positions
            if any(s in p.get("ticker", "") for s in SPORT_GAME_TOKENS)
            and float(p.get("position_fp", 0)) != 0
        )
        kalshi_balance = self._client.get_balance(max_age_s=60)
        kalshi_total = (kalshi_balance or 0.0) + all_exp
        # Proactive low-balance alert (hysteresis: armed → fire → re-arm at
        # recovery line). Sits alongside the post-reject alert and 14:00/22:00
        # ET scheduled checks to catch evening-cluster cash drains in real time.
        self._order_manager.check_low_balance(kalshi_balance)
        max_sports_exposure = max(kalshi_total * 1.0, 10.0)
        if sports_exp > max_sports_exposure:
            logger.info(
                f"[SPORTS] Exposure ${sports_exp:.2f} > limit ${max_sports_exposure:.2f} "
                f"(Kalshi total ${kalshi_total:.2f}), skipping"
            )
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

                # Observation-only sports (data collection without live trading).
                # Added to sport_map so the observer records them, but live
                # trading strategies must explicitly promote a sport before
                # entering trades on it.
                if self.OBSERVATION_ONLY_SPORTS:
                    obs_sport = next(
                        (SPORT_SHORT_BY_TOKEN[s] for s in SPORT_GAME_TOKENS if s in ticker),
                        None,
                    )
                    if obs_sport and obs_sport in self.OBSERVATION_ONLY_SPORTS:
                        continue

                # Sport-blocklist (legacy — superseded by ALLOWED_CELLS
                # when that's set). Reject tickers for sports where this
                # strategy has no edge in either region (e.g. NHL midshot).
                if self.SPORT_BLOCKLIST and self.ALLOWED_CELLS is None:
                    sport_code = next(
                        (s for s in SPORT_GAME_TOKENS if s in ticker),
                        None,
                    )
                    short = SPORT_SHORT_BY_TOKEN.get(sport_code) if sport_code else None
                    if short and short in self.SPORT_BLOCKLIST:
                        continue

                # Playoff-blocklist (sport-specific suspend during playoff).
                # Composes with ALLOWED_CELLS — applies independently. When
                # ticker's sport is listed AND inside its playoff window,
                # skip. Auto-resumes when sport returns to regular season.
                if self.PLAYOFF_BLOCKLIST:
                    pb_sport = next(
                        (SPORT_SHORT_BY_TOKEN[s] for s in SPORT_GAME_TOKENS if s in ticker),
                        None,
                    )
                    if pb_sport and pb_sport in self.PLAYOFF_BLOCKLIST:
                        if is_playoff(ticker, datetime.now(timezone.utc).isoformat()):
                            logger.debug(
                                f"[{self.STRATEGY_NAME}] {ticker}: sport "
                                f"{pb_sport} in PLAYOFF_BLOCKLIST and is_playoff, skip"
                            )
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
                matched_game = self._match_kalshi_to_odds(title, odds_games, kalshi_ticker=ticker)
                if not matched_game:
                    continue

                consensus = self._consensus_probability(matched_game)
                if not consensus:
                    continue
                # Coverage gate: thin consensus (e.g., 2-3 books) is noisier
                # than its numeric value implies. Skip when below the
                # subclass-set minimum. None = legacy behaviour (no gate).
                if self.MIN_N_BOOKS is not None and consensus.get("n_books", 0) < self.MIN_N_BOOKS:
                    logger.debug(
                        f"[{self.STRATEGY_NAME}] {ticker}: n_books="
                        f"{consensus.get('n_books', 0)} < MIN_N_BOOKS={self.MIN_N_BOOKS}, skip"
                    )
                    continue
                pinnacle = self._pinnacle_probability(matched_game)

                # Determine which team YES refers to using yes_sub_title (most reliable)
                # e.g., ticker -MIN has yes_sub_title="Minnesota", -DET has "Detroit"
                yes_team = market.get("yes_sub_title", "").lower()
                home = consensus["home_team"].lower()
                away = consensus["away_team"].lower()

                # Soccer 3-way DRAW outcome: ticker -TIE represents "draw happens".
                # yes_sub_title is "Tie" — doesn't match home/away teams. Bypass
                # team-matching and pass through to evaluate (v38_soccer_draw
                # handles TIE side; other strategies skip via their own evaluate).
                # fair_prob=0.25 (typical soccer draw prob) — safe sentinel for
                # downstream _format_log formatting; v38 evaluate doesn't use it.
                if ticker.endswith("-TIE"):
                    fair_prob = 0.25
                    is_yes_home = False
                else:
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

                # Cell-matrix gate (ALLOWED_CELLS). When set, subsumes
                # PRE_GAME_ONLY and SPORT_BLOCKLIST — only (sport, phase)
                # tuples in the allow-set pass. Used by V3.4 to express
                # asymmetric per-cell region preferences from the matrix
                # (e.g. MLB underdog live us +38% but MLB underdog pre eu
                # +14% — two cells need different regions).
                if self.ALLOWED_CELLS is not None:
                    sport_code = next(
                        (SPORT_SHORT_BY_TOKEN[s] for s in SPORT_GAME_TOKENS if s in ticker),
                        None,
                    )
                    mts = _commence_time_to_minutes(matched_game.get("commence_time"))
                    phase = "pre" if (mts is not None and mts > 0) else "live"
                    if sport_code is None or (sport_code, phase) not in self.ALLOWED_CELLS:
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: cell "
                            f"({sport_code},{phase}) not in ALLOWED_CELLS, skip"
                        )
                        continue
                    # Per-cell price band whitelist. When CELL_PRICE_BANDS lists
                    # bands for this cell, the signal price must fall inside
                    # one of them (subsumes PRICE_MIN/MAX/DEAD_ZONE for listed
                    # cells). Cells with no entry fall through to legacy gates.
                    if self.CELL_PRICE_BANDS is not None:
                        bands = self.CELL_PRICE_BANDS.get((sport_code, phase))
                        if bands is not None:
                            sp = signal["price"]
                            if not any(lo <= sp <= hi for lo, hi in bands):
                                logger.debug(
                                    f"[{self.STRATEGY_NAME}] {ticker}: price "
                                    f"{sp:.3f} not in cell ({sport_code},{phase}) "
                                    f"bands {bands}, skip"
                                )
                                continue
                elif self.PRE_GAME_ONLY:
                    # Legacy gate — only applies when ALLOWED_CELLS not set.
                    # Subclasses with PRE_GAME_ONLY=True (e.g. longshot_v31)
                    # reject entries once the game has started — consensus
                    # goes stale during live play and chasing Kalshi moves
                    # with stale odds is systematically loss-making.
                    minutes_to_start = _commence_time_to_minutes(matched_game.get("commence_time"))
                    if minutes_to_start is None or minutes_to_start <= 0:
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: live-game entry "
                            f"(mts={minutes_to_start}), pre-game only, skip"
                        )
                        continue

                # Max-MTS gate (block entries placed too far in advance).
                # Protects strategies whose alpha was measured in a specific
                # pre-game time window from drifting into earlier mts where
                # the line is unstable.
                if self.MAX_MTS_AT_ENTRY is not None:
                    mts_check = _commence_time_to_minutes(matched_game.get("commence_time"))
                    if mts_check is not None and mts_check > self.MAX_MTS_AT_ENTRY:
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: mts={mts_check:.0f} > "
                            f"MAX_MTS_AT_ENTRY={self.MAX_MTS_AT_ENTRY:.0f}, skip"
                        )
                        continue

                # Playoff-only gate (additive — combines with ALLOWED_CELLS).
                # Uses core.season calendar windows to classify ticker as
                # playoff vs regular. None = unknown sport / parse failure;
                # treat as not-playoff to be safe.
                if self.PLAYOFF_ONLY:
                    now_ts = datetime.now(timezone.utc).isoformat()
                    if not is_playoff(ticker, now_ts):
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: not playoff, skip"
                        )
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

                # Thin-book early-fire gate (2026-05-11). Drop trades placed
                # too early on thin books — consensus hasn't formed yet.
                # See THIN_BOOK_EARLY_GATE_* attrs above for rationale.
                if (self.THIN_BOOK_EARLY_GATE_MTS_MIN_MINUTES is not None
                        and self.THIN_BOOK_EARLY_GATE_DEPTH_MAX_USDC is not None):
                    mts_te = _commence_time_to_minutes(matched_game.get("commence_time"))
                    if mts_te is not None and mts_te >= self.THIN_BOOK_EARLY_GATE_MTS_MIN_MINUTES:
                        yes_depth, no_depth = self._client.get_order_book_depth(ticker)
                        side_depth = yes_depth if signal["side"] == "yes" else no_depth
                        if side_depth is not None and side_depth < self.THIN_BOOK_EARLY_GATE_DEPTH_MAX_USDC:
                            logger.debug(
                                f"[{self.STRATEGY_NAME}] {ticker}: thin-book early gate "
                                f"(mts={mts_te:.0f}min ≥ {self.THIN_BOOK_EARLY_GATE_MTS_MIN_MINUTES:.0f}, "
                                f"depth=${side_depth:.0f} < ${self.THIN_BOOK_EARLY_GATE_DEPTH_MAX_USDC:.0f}), skip"
                            )
                            continue

                limit_price = self._get_limit_price(signal, fair_prob)
                limit_price = max(0.01, min(0.99, limit_price))

                # Entry price floor. Paper data shows a structural dead zone
                # below 20¢ for midshot/underdog v31 (0% win on 27–36 trades).
                if self.MIN_ENTRY_PRICE is not None and limit_price < self.MIN_ENTRY_PRICE:
                    logger.debug(
                        f"[{self.STRATEGY_NAME}] {ticker}: limit_price "
                        f"{limit_price:.3f} < floor {self.MIN_ENTRY_PRICE:.2f}, skip"
                    )
                    continue

                # Entry price ceiling. underdog_v31 at 0.40c hits the
                # payoff≈2.0 fee-boundary cliff (-25% ROI n=62 vs +11% at
                # 0.39c). Drop the cliff cent without touching the rest of
                # the band.
                if self.MAX_ENTRY_PRICE is not None and limit_price > self.MAX_ENTRY_PRICE:
                    logger.debug(
                        f"[{self.STRATEGY_NAME}] {ticker}: limit_price "
                        f"{limit_price:.3f} > cap {self.MAX_ENTRY_PRICE:.2f}, skip"
                    )
                    continue

                # Dead-zone gate (independent of floor). longshot_v31 and
                # v4_edge show <10¢ winning but 10–20¢ zero-win — skip the
                # [lo, hi) band only, keep <lo entries.
                if self.ENTRY_PRICE_DEAD_ZONE is not None:
                    lo, hi = self.ENTRY_PRICE_DEAD_ZONE
                    if lo <= limit_price < hi:
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: limit_price "
                            f"{limit_price:.3f} in dead zone [{lo:.2f},{hi:.2f}), skip"
                        )
                        continue

                # Per-game aggregate-stake cap (v33 variants). Shared across
                # all strategies that set PER_GAME_CAP_USDC via OrderManager._
                # v33_game_spend so longshot/midshot/underdog siblings see
                # each other's exposure on the same game. Update tracker
                # BEFORE dispatch so near-simultaneous scans don't double-
                # count; on a paper fire the dispatch always records the
                # entry so the reservation matches reality.
                if self.PER_GAME_CAP_USDC is not None:
                    spend = self._order_manager._v33_game_spend
                    current = spend.get(game_id, 0.0)
                    if current + size > self.PER_GAME_CAP_USDC + 0.01:
                        logger.debug(
                            f"[{self.STRATEGY_NAME}] {ticker}: v33 game cap "
                            f"${current:.2f}+${size:.2f} > ${self.PER_GAME_CAP_USDC:.2f}, skip"
                        )
                        continue
                    spend[game_id] = current + size

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
                    is_yes_home=is_yes_home,
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
