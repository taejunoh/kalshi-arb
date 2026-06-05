from unittest.mock import MagicMock

from strategies.sports_mls_fade_favorite_v1 import SportsMLSFadeFavoriteV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    # ④ REAL_FEE_GATE refactor: evaluate() now calls config.gate_fee_pct(price)
    # in place of the inline min(fee, X, 1-X)/X. Stub it with the legacy
    # flag-OFF formula (byte-identical to the old inline expression) so the
    # MagicMock config returns a numeric instead of a MagicMock.
    cfg.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1 else min(0.07, price, 1.0 - price) / price
    )
    s = SportsMLSFadeFavoriteV1Strategy.__new__(SportsMLSFadeFavoriteV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._traded_tickers = {}
    return s


def test_fires_buy_no_when_yes_ask_in_favorite_band():
    """yes_ask 0.60 → no_ask 0.40, payoff ~2.13x → BUY NO."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.62,
                     ticker="KXMLSGAME-25MAY15NYCATL-NYC")
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.40
    assert sig["edge"] >= 2.0


def test_skips_when_team_is_underdog():
    """yes_ask 0.40 → team is dog, not favorite. Strategy fades favorites only."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.40, kalshi_no=0.60, fair_prob=0.40,
                     ticker="KXMLSGAME-X-NYC")
    assert sig is None


def test_skips_tie_tickers():
    """3-way TIE outcomes are handled by sports_soccer_draw_v1, not this strategy."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.30, kalshi_no=0.70, fair_prob=0.30,
                     ticker="KXMLSGAME-X-TIE")
    assert sig is None


def test_skips_when_no_ask_above_cap():
    """MAX_ENTRY_PRICE=0.45 — no_ask 0.50 should not fire."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.50, kalshi_no=0.50, fair_prob=0.50,
                     ticker="KXMLSGAME-X-NYC")
    assert sig is None


def test_skips_when_yes_ask_above_band():
    """yes_ask > 0.70 → super-chalk band, observer signal does not extend."""
    s = _make_strategy()
    # yes_ask 0.75 → no_ask 0.25 (below MIN_ENTRY_PRICE 0.30 anyway, but
    # the yes_ask cap is the primary gate).
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.30, fair_prob=0.75,
                     ticker="KXMLSGAME-X-NYC")
    assert sig is None


def test_allowed_cells_restricts_to_mls_pre():
    """Cell-matrix gate must restrict to MLS pre-game only."""
    assert SportsMLSFadeFavoriteV1Strategy.ALLOWED_CELLS == frozenset({("MLS", "pre")})


def test_observation_only_sports_cleared():
    """Parent default OBSERVATION_ONLY_SPORTS contains MLS — must be cleared
    so MLS tickers reach evaluate()."""
    assert "MLS" not in SportsMLSFadeFavoriteV1Strategy.OBSERVATION_ONLY_SPORTS


def test_price_band_bounds():
    """Observer-derived: no_ask 0.30-0.45 fade-favorite band."""
    assert SportsMLSFadeFavoriteV1Strategy.MIN_ENTRY_PRICE == 0.30
    assert SportsMLSFadeFavoriteV1Strategy.MAX_ENTRY_PRICE == 0.45
