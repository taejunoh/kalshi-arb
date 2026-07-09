import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsUfcChalkFadeV1Strategy.__new__(SportsUfcChalkFadeV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.82
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    return s


def test_fires_buy_no_when_both_kalshi_and_pinnacle_chalk():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.20


def test_skips_below_chalk_threshold():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.75,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.70
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.70,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_stale():
    s = _make_strategy()
    s._last_pinnacle_age_s = 1000
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_missing():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = None
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.50,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


# ---------- 2026-07-09 NEVER-FIRED regression ----------
#
# evaluate() fired correctly since 2026-05-15 birth, but zero orders ever
# reached the book: the strategy never overrode _get_limit_price, so the
# base default booked round(1 - fair) ~= 0.05-0.22 for the NO side, and the
# class attr MIN_ENTRY_PRICE=0.78 — meant as evaluate()'s favorite-YES
# threshold — doubled as the base entry-price FLOOR (sports_trader.py
# "limit_price < MIN_ENTRY_PRICE, skip"), rejecting 100% of signals.
# The evaluate()-only tests above could never catch this; these drive the
# real scan_and_execute gate chain.


def _scan_harness(tmp_path, monkeypatch, yes_ask=0.80, no_ask=0.21):
    """Real strategy instance wired to a fully mocked Kalshi client +
    order manager, with one UFC market whose favorite trades at `yes_ask`
    and fresh Pinnacle chalk agreeing (>= 0.78)."""
    monkeypatch.setattr(
        SportsUfcChalkFadeV1Strategy, "TRADED_TICKERS_FILE",
        str(tmp_path / "tickers.json"),
    )

    config = MagicMock()
    config.SPORTS_ENABLED = True
    config.SPORTS_SCAN_INTERVAL = 0
    config.ODDS_API_KEY = ""
    config.KALSHI_FEE_PER_CONTRACT = 0.07

    om = MagicMock(bankroll=10000)
    om.is_circuit_breaker_active.return_value = False
    om.sizing_bankroll.return_value = 10000.0
    om.market_order = AsyncMock(
        return_value={"status": "paper_filled", "trade_id": 1}
    )

    client = MagicMock()
    client.get_positions.return_value = []
    client.get_balance.return_value = 1000.0
    client.get_order_book_depth.return_value = (200.0, 200.0)

    # Rolling commence_time (no date-bomb): fight is tomorrow, inside
    # PRE_GAME_ONLY + MAX_MTS_AT_ENTRY=4320.
    fight_dt = datetime.now(timezone.utc) + timedelta(hours=24)
    ticker = f"KXUFCFIGHT-{fight_dt:%y%b%d}JONASP-JON".upper()
    market = {
        "ticker": ticker,
        "title": "Jones vs. Aspinall Winner?",
        "yes_sub_title": "Jon Jones",
        "yes_ask_dollars": str(yes_ask),
        "no_ask_dollars": str(no_ask),
    }

    def fake_get(path):
        if "series_ticker=KXUFCFIGHT" in path:
            return {"markets": [market]}
        return {"markets": []}

    client._get = MagicMock(side_effect=fake_get)

    # Pinnacle 1.20 / 5.50 decimal -> vig-removed home prob ~0.821 (>= 0.78
    # confirm gate); consensus == pinnacle (single book), so fair ~0.821 and
    # the buggy base default books round(1 - fair) = 0.18 for the NO side.
    odds_games = [{
        "id": "ufc-g1",
        "home_team": "Jon Jones",
        "away_team": "Tom Aspinall",
        "commence_time": fight_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Jon Jones", "price": 1.20},
                    {"name": "Tom Aspinall", "price": 5.50},
                ],
            }],
        }],
    }]

    s = SportsUfcChalkFadeV1Strategy(config, om, client)

    async def fake_fetch_odds_multi(sport_value):
        if sport_value == "mma_mixed_martial_arts":
            return odds_games
        return []

    s._fetch_odds_multi = fake_fetch_odds_multi
    s._record_entry_clv = AsyncMock()
    return s, om, ticker


def test_scan_path_no_signal_reaches_paper_order(tmp_path, monkeypatch):
    """THE never-fired bug: a valid NO-side chalk-fade signal must survive
    the base gate chain (entry-price floor included) and reach
    order_manager.market_order — booked at the observed NO ask."""
    s, om, ticker = _scan_harness(tmp_path, monkeypatch)

    async def run():
        await s.scan_and_execute()
        if s._pending_entry_tasks:
            await asyncio.gather(*s._pending_entry_tasks)

    asyncio.run(run())

    om.market_order.assert_awaited_once()
    kwargs = om.market_order.await_args.kwargs
    assert kwargs["ticker"] == ticker
    assert kwargs["side"] == "no"
    assert kwargs["strategy"] == "sports_ufc_chalk_fade_v1"
    # Honest booking: the observed NO ask, not round(1-fair)=0.18.
    assert kwargs["price"] == 0.21


def test_books_at_observed_no_ask_not_fair_complement():
    """Fade-style bookkeeping honesty (see sports_ufc_live_fade_v1 and
    feedback_paper_ledger_booking_integrity_2026-07-02): the paper ledger
    must book the ask the signal actually saw, never a fair-derived price
    that never existed on the book."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert s._get_limit_price(sig, fair_prob=0.82) == 0.20


def test_min_entry_price_floor_disabled():
    """The 0.78 favorite-YES threshold lives in evaluate() (MIN_FAVORITE_YES).
    The base-class MIN_ENTRY_PRICE floor must stay None — a NO-side fade
    enters at ~0.05-0.22, which any floor >= 0.22 silently kills 100%."""
    assert SportsUfcChalkFadeV1Strategy.MIN_ENTRY_PRICE is None
    assert SportsUfcChalkFadeV1Strategy.MIN_FAVORITE_YES == 0.78


def test_blocks_atp_wta_and_other_team_sports():
    """B1 is UFC-only — must block ATP+WTA + MLB/NBA/NHL/soccer."""
    blocked = SportsUfcChalkFadeV1Strategy.SPORT_BLOCKLIST
    assert "MLB" in blocked
    assert "NBA" in blocked
    assert "NHL" in blocked
    assert "WNBA" in blocked
    assert "MLS" in blocked
    assert "EPL" in blocked
    assert "ATP" in blocked  # B1 is UFC-only — tennis must be blocked
    assert "WTA" in blocked  # B1 is UFC-only — tennis must be blocked
    assert "UFC" not in blocked  # UFC is the sole target for B1
