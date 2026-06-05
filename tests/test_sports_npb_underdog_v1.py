from unittest.mock import MagicMock

from strategies.sports_npb_underdog_v1 import SportsNpbUnderdogV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    # ④ gate-fee flag: evaluate() now calls config.gate_fee_pct(price) instead
    # of the inline min(fee, X, 1-X)/X. With REAL_FEE_GATE OFF (live default)
    # gate_fee_pct is byte-identical to the legacy formula — stub it so the
    # MagicMock config returns a float, not a MagicMock.
    cfg.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1 else min(0.07, price, 1 - price) / price
    )
    s = SportsNpbUnderdogV1Strategy.__new__(SportsNpbUnderdogV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._order_manager.sizing_bankroll = lambda x: s._order_manager.bankroll
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_yes_is_underdog_at_039():
    """fair=0.30 (YES side underdog) + kalshi_yes_ask=0.39 → BUY YES.

    Inherited v31 underdog evaluate: fair<0.40 + 0.05<=kalshi_yes<=0.40 +
    payoff>=2.0. Class-level MIN/MAX 0.38-0.40 enforced separately downstream.
    """
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.39, kalshi_no=0.61, fair_prob=0.30,
                     ticker="KXNPBGAME-26MAY200500HANSAM-HAN")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.39


def test_fires_buy_no_when_no_is_underdog_at_038():
    """fair=0.70 (NO side underdog) + kalshi_no_ask=0.38 → BUY NO."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.62, kalshi_no=0.38, fair_prob=0.70,
                     ticker="KXNPBGAME-26MAY200500HANSAM-SAM")
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.38


def test_v31_evaluate_alone_does_not_block_below_038():
    """Sanity: parent v31 underdog evaluate accepts 0.05-0.40, so a 0.35
    signal passes evaluate(). The class-level MIN_ENTRY_PRICE=0.38 gate
    runs separately in sports_trader.scan_and_execute (not evaluate)."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.35, kalshi_no=0.65, fair_prob=0.30,
                     ticker="KXNPBGAME-X-HAN")
    assert sig is not None  # evaluate passes; MIN_ENTRY_PRICE gate is downstream


def test_skips_when_no_underdog_signal():
    """fair=0.50 (coin flip) → neither side qualifies as underdog."""
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.50, kalshi_no=0.50, fair_prob=0.50,
                     ticker="KXNPBGAME-X-HAN")
    assert sig is None


def test_allowed_cells_restricts_to_npb_pre():
    """Cell-matrix gate must restrict to NPB pre-game only."""
    assert SportsNpbUnderdogV1Strategy.ALLOWED_CELLS == frozenset({("NPB", "pre")})


def test_observation_only_sports_removes_npb_only():
    """Parent default OBSERVATION_ONLY_SPORTS contains NPB — must be cleared
    so NPB tickers reach evaluate(). KBO and other observation-only sports
    must remain so this strategy never fires on them."""
    obs = SportsNpbUnderdogV1Strategy.OBSERVATION_ONLY_SPORTS
    assert "NPB" not in obs
    assert "KBO" in obs
    assert "MLS" in obs
    assert "ATP" in obs


def test_price_band_bounds():
    """Observer sweet-spot sub-band 0.38-0.40."""
    assert SportsNpbUnderdogV1Strategy.MIN_ENTRY_PRICE == 0.38
    assert SportsNpbUnderdogV1Strategy.MAX_ENTRY_PRICE == 0.40


def test_pair_fire_dedup_enabled():
    """Same-game pair-fire dedup is load-bearing: 2-way NPB market produces
    2 ticker fires per underdog signal (HOME + AWAY ticker). Without dedup
    the strategy doubles exposure on every signal."""
    assert SportsNpbUnderdogV1Strategy.PAIR_FIRE_DEDUP is True


def test_trade_size_is_8():
    """Phase 0 conservative launch size."""
    s = _make_strategy()
    size = s._get_trade_size({"price": 0.39})
    assert size == 8.0


def test_min_n_books_floor():
    """NPB eu consensus typically 2-4 bookmakers — floor at 2 to avoid
    single-book signals while keeping universe usable."""
    assert SportsNpbUnderdogV1Strategy.MIN_N_BOOKS == 2
