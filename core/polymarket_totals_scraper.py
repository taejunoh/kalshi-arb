"""Polymarket gamma-api totals/spreads scraper — Phase 0 observer.

Pages the public /markets endpoint sorted by volume24hr (descending) and
filters for slug patterns matching daily-sports totals (`*-total-{line}pt{n}`)
and spreads (`*-spread-{home|away}-{line}pt{n}`).

Returns normalized rows for storage; does NOT perform any Kalshi mapping
or trading. Mapping to Kalshi event tickers happens post-hoc.

Slug grammar (Phase 0 — strict, skip variants):
  {sport}-{away}-{home}-{YYYY-MM-DD}-total-{Npt M}
  {sport}-{away}-{home}-{YYYY-MM-DD}-spread-(home|away)-{Npt M}

Variants intentionally skipped for now:
  *-first-set-total-*       (tennis set-level)
  *-game{N}-kill-over-*     (esports per-game)
  *-total-games-*           (esports map count, not score)
"""
import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("polymarket_totals_scraper")

GAMMA_API_BASE = "https://gamma-api.polymarket.com/markets"

# Sports we want to capture. KXMLB/KXNBA/KXNHL/KXWNBA + soccer leagues we
# already track via odds_observations. Excludes esports (lol/cs2/val/dota2)
# and obscure leagues until use-case appears.
_TRACKED_SPORTS = frozenset({
    "mlb", "nba", "nhl", "wnba",
    "epl", "mls", "lal", "bun", "ucl", "fl1", "spl", "efa", "chi",
    "atp", "wta", "ufc", "npb", "kbo",
})

# Slug pattern: anchored by YYYY-MM-DD between hyphens.
_DATE_INFIX = re.compile(r"-(\d{4}-\d{2}-\d{2})-")
# Post-date suffix line: '...pt{N}' at end (e.g. 8pt5 → 8.5).
_LINE_TRAIL = re.compile(r"(\d+pt\d+)$")


def _decode_field(value):
    """gamma-api returns outcomes/outcomePrices as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def parse_totals_slug(slug: str) -> Optional[dict]:
    """Parse a Polymarket totals/spread slug → normalized dict, or None.

    Returns None for moneyline slugs, variants, or unparseable strings.
    """
    m = _DATE_INFIX.search(slug)
    if not m:
        return None
    date = m.group(1)
    pre = slug[: m.start()]
    post = slug[m.end():]
    pre_parts = pre.split("-")
    # Phase 0 requires exactly sport-away-home (3 segments). Multi-token
    # team names (e.g., tennis "los-angeles") would break this — for now
    # we skip them; tennis uses single-token player surnames in slug.
    if len(pre_parts) != 3:
        return None
    sport, away, home = pre_parts
    if sport not in _TRACKED_SPORTS:
        return None
    line_m = _LINE_TRAIL.search(post)
    if not line_m:
        return None
    line = float(line_m.group(1).replace("pt", "."))
    post_clean = post[: line_m.start()].rstrip("-")
    if post_clean == "total":
        market_type, spread_side = "total", None
    elif post_clean == "spread-home":
        market_type, spread_side = "spread", "home"
    elif post_clean == "spread-away":
        market_type, spread_side = "spread", "away"
    else:
        return None  # skip variants
    return {
        "slug": slug,
        "sport": sport,
        "game_date": date,
        "away_team_raw": away,
        "home_team_raw": home,
        "market_type": market_type,
        "line": line,
        "spread_side": spread_side,
    }


def normalize_market_row(raw: dict) -> Optional[dict]:
    """Combine slug parse + price extraction. Returns None on any failure.

    Outcome label semantics by market_type:
      total  → outcomes=["Over","Under"]; yes_price=Over, no_price=Under.
      spread → outcomes=[covering_team_name, other_team_name]; yes_price is
               the price of the side named in `spread_side` (the team
               carrying the points), no_price is the other side. Order of
               outcomes returned by gamma-api matches the question text,
               so we trust prices[0]/prices[1] positionally.
    """
    if not raw.get("active") or raw.get("closed"):
        return None
    slug = raw.get("slug") or ""
    parsed = parse_totals_slug(slug)
    if parsed is None:
        return None
    outcomes = _decode_field(raw.get("outcomes")) or []
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        return None
    if parsed["market_type"] == "total":
        # Strict Over/Under check — anything else is an unexpected variant.
        if outcomes != ["Over", "Under"]:
            return None
    # For 'spread' we accept any 2-element team-name list. We won't know
    # which slot maps to which team without further parsing, but the
    # `spread_side` parsed from the slug already tells us which team
    # carries the line — yes_price ends up being that side's price.
    prices = _decode_field(raw.get("outcomePrices")) or []
    if not isinstance(prices, list) or len(prices) < 2:
        return None
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (TypeError, ValueError):
        return None
    vol_raw = raw.get("volume24hr") or raw.get("volume")
    try:
        vol_24h = float(vol_raw) if vol_raw is not None else None
    except (TypeError, ValueError):
        vol_24h = None
    parsed.update({
        "yes_price": yes_price,
        "no_price": no_price,
        "volume_24h": vol_24h,
        "question": raw.get("question"),
    })
    return parsed


class PolymarketTotalsScraper:
    """Pages gamma-api /markets sorted by volume24hr desc, filters totals."""

    def __init__(self, timeout: float = 15.0,
                 max_pages: int = 30, page_size: int = 100,
                 stop_after_empty_pages: int = 10):
        self._timeout = timeout
        self._max_pages = max_pages
        self._page_size = page_size
        # Totals/spreads have much lower volume24hr than their moneyline
        # parents, so they're scattered through the volume-sorted listing.
        # Set the empty-streak threshold generously (10) so we don't
        # short-circuit before we reach the low-volume sport markets.
        self._stop_after_empty = stop_after_empty_pages

    def fetch_all_totals(self) -> list[dict]:
        """Returns deduplicated list of normalized totals/spread rows.

        Dedup key: slug. Pagination uses offset; gamma-api supports
        order=volume24hr ascending=false to surface tradeable markets
        first, so empty-page early-stop is safe.
        """
        out: dict[str, dict] = {}
        empty_streak = 0
        for page in range(self._max_pages):
            offset = page * self._page_size
            try:
                resp = httpx.get(
                    GAMMA_API_BASE,
                    params={
                        "limit": self._page_size,
                        "offset": offset,
                        "closed": "false",
                        "active": "true",
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                    timeout=self._timeout,
                )
            except Exception as e:
                logger.warning(f"Polymarket totals scrape failed at offset {offset}: {e}")
                break
            if resp.status_code != 200:
                logger.warning(f"Polymarket HTTP {resp.status_code} at offset {offset}")
                break
            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f"Polymarket JSON parse failed at offset {offset}: {e}")
                break
            if not isinstance(data, list) or not data:
                break
            page_matches = 0
            for raw in data:
                normalized = normalize_market_row(raw)
                if normalized is None:
                    continue
                slug = normalized["slug"]
                if slug not in out:
                    out[slug] = normalized
                    page_matches += 1
            if page_matches == 0:
                empty_streak += 1
                if empty_streak >= self._stop_after_empty:
                    break
            else:
                empty_streak = 0
        return list(out.values())
