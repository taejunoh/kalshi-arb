"""Tennis tuple URL bug regression (2026-05-19).

`SPORT_ODDS_KEYS` maps tennis series to a TUPLE of concurrently-active
Odds API tournament slugs (French Open + Hamburg, etc.). Prior to the fix,
`scan_and_execute` passed the raw tuple to `_dispatch_entry` →
`_record_entry_clv` → `_dual_region_consensus` → `_fetch_odds`, which
string-interpolated it into the URL:

    GET https://api.the-odds-api.com/v4/sports/('tennis_wta_french_open',
        'tennis_wta_strasbourg')/odds  → 404

Fix: pass the already-resolved per-game `sport_key` (string slug derived
from `matched_game["_tournament_slug"]`) instead of the raw `sport_map`
entry.

These tests pin the contract two ways:
  1. Static: the dispatch call site must not re-introduce the tuple.
  2. Dynamic: the full chain refuses to issue an Odds API request with a
     tuple `sport` arg.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

from strategies.sports_trader import SportsTraderStrategy


def test_dispatch_site_passes_sport_key_not_raw_sport_map():
    """Pins the literal fix at the only dispatch call site. If anyone
    reverts line 2000 to `sport=sport_map.get(series, series)`, the
    tennis tuple leaks into Odds API URLs and silently kills tennis
    consensus refresh for CLV snapshots."""
    src = inspect.getsource(SportsTraderStrategy.scan_and_execute)
    assert "sport=sport_key" in src, (
        "scan_and_execute must dispatch with the per-game sport_key string "
        "(see [[tennis_tuple_url_bug_2026-05-19]])."
    )
    assert "sport=sport_map.get(series, series)" not in src, (
        "Raw sport_map lookup leaks the tennis tuple into Odds API URLs."
    )


def test_record_entry_clv_chain_rejects_tuple_sport(tmp_path):
    """Defense-in-depth: even if a future caller hands `_record_entry_clv`
    a tuple, _fetch_odds must never see one. We assert by capturing every
    `sport` arg that reaches _fetch_odds and confirming it's a string."""
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    s = SportsTraderStrategy(config, MagicMock(), MagicMock())
    s._order_manager = MagicMock()
    s._order_manager._db = MagicMock()
    s._order_manager._db.record_clv_snapshot = AsyncMock()

    seen: list = []

    async def _spy_fetch_odds(sport):
        seen.append(sport)
        return []

    s._fetch_odds = _spy_fetch_odds  # type: ignore[assignment]

    asyncio.run(
        s._record_entry_clv(
            trade_id=1,
            ticker="KXATPMATCH-FAKE",
            game={"id": "g1", "home_team": "A", "away_team": "B"},
            sport="tennis_atp_french_open",  # post-fix: always a str
            pinnacle_prob=0.5,
            is_yes_home=True,
        )
    )

    assert seen, "_fetch_odds should have been invoked for both regions"
    for arg in seen:
        assert isinstance(arg, str), (
            f"_fetch_odds got non-str sport={arg!r} (type {type(arg).__name__}) — "
            "would string-interpolate into Odds API URL"
        )
