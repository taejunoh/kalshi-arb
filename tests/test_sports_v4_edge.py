"""Unit tests for Sports V4 Edge strategy — midshot price filter + edge gate."""
from unittest.mock import MagicMock

from strategies.sports_v4_edge import SportsV4EdgeStrategy


def _make_strategy(pinnacle_yes_prob=None, bankroll=2000.0) -> SportsV4EdgeStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    # fee-fix/④: strategy now calls config.gate_fee_pct(price) instead of the
    # inline min(fee, price, 1-price)/price. Stub it with the legacy formula
    # (== gate_fee_pct with REAL_FEE_GATE flag OFF, the live default) so the
    # MagicMock config returns a real float instead of a MagicMock.
    config.gate_fee_pct = MagicMock(
        side_effect=lambda price: 0.0 if (price <= 0 or price >= 1)
        else min(0.07, price, 1.0 - price) / price
    )
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    client = MagicMock()
    s = SportsV4EdgeStrategy(config, order_mgr, client)
    s._last_pinnacle_yes_prob = pinnacle_yes_prob
    return s


def test_fires_when_price_in_range_and_pinnacle_edge_ok():
    """price 0.20, pinnacle yes_prob 0.55. Current code: cost=0.20*(1+0.35)=0.27,
    payoff=1/0.27=3.7>2.5, edge=side_prob-cost=0.55-0.27=0.28>0.03, and
    side_prob 0.55>=MIN_SIDE_PROB 0.50 (favorite-only gate added 2026-04-26)."""
    # MIN_SIDE_PROB=0.50 now requires the bet side to be the favorite, so the
    # old 0.35 prob no longer fires; raise to 0.55 to exercise the fire path.
    strat = _make_strategy(pinnacle_yes_prob=0.55)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.55, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["price"] == 0.20


def test_falls_back_to_consensus_when_pinnacle_missing():
    """No Pinnacle → use consensus fair_prob. fair_prob 0.55 >= MIN_SIDE_PROB."""
    # Same MIN_SIDE_PROB=0.50 favorite gate: consensus prob must be >= 0.50.
    strat = _make_strategy(pinnacle_yes_prob=None)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.55, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"


def test_rejects_when_price_above_midshot_cap():
    """Price > 0.35 filtered regardless of edge."""
    strat = _make_strategy(pinnacle_yes_prob=0.80)
    signal = strat.evaluate(kalshi_yes=0.40, kalshi_no=0.62, fair_prob=0.80, ticker="T")
    assert signal is None


def test_rejects_when_payoff_below_25x():
    """price 0.35 → payoff 1/(0.35+0.07) = 2.38 < 2.5 → skip."""
    strat = _make_strategy(pinnacle_yes_prob=0.50)
    signal = strat.evaluate(kalshi_yes=0.35, kalshi_no=0.68, fair_prob=0.50, ticker="T")
    assert signal is None


def test_rejects_when_edge_below_3pct():
    """Price 0.20 passes filter but edge gate 0.03 not met."""
    # pinnacle 0.22 → yes_edge = 0.22 - 0.20*1.35 = -0.05 < 0.03
    strat = _make_strategy(pinnacle_yes_prob=0.22)
    signal = strat.evaluate(kalshi_yes=0.20, kalshi_no=0.82, fair_prob=0.22, ticker="T")
    assert signal is None


def test_selects_no_side_when_better():
    """NO side selected: no_ask 0.20, yes 0.82. Pinnacle yes_prob 0.35 → no side
    prob = 1-0.35 = 0.65 >= MIN_SIDE_PROB 0.50, while yes price 0.82 > PRICE_MAX
    0.35 is filtered. no_edge = 0.65 - 0.20*1.35 = 0.38 > 0.03."""
    # NO side is the favorite (0.65) so it passes MIN_SIDE_PROB; the YES leg at
    # 0.82 is rejected by the price-range filter, leaving NO as the only signal.
    strat = _make_strategy(pinnacle_yes_prob=0.35)
    signal = strat.evaluate(kalshi_yes=0.82, kalshi_no=0.20, fair_prob=0.35, ticker="T")
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["price"] == 0.20


def test_strategy_name():
    strat = _make_strategy(pinnacle_yes_prob=0.5)
    assert strat.STRATEGY_NAME == "sports_v4_edge"
    assert strat.TRADED_TICKERS_FILE == "data/sports_v4_edge_tickers.json"


def test_v4_edge_has_depth_and_dead_zone_gates():
    """Live-promotion safety rails: 5 USDC depth + dead-zone.

    The global ENTRY_PRICE_DEAD_ZONE = (0.10, 0.20) was SUPERSEDED on 2026-04-26
    by per-cell ALLOWED_CELLS + CELL_PRICE_BANDS (see strategy docstring). The
    strategy-level dead-zone attr is now None; the 0.10¢ dead zone is instead
    enforced structurally by leaving 0.10 out of every cell's price band.
    """
    assert SportsV4EdgeStrategy.MIN_DEPTH_USDC == 5.0
    assert SportsV4EdgeStrategy.MIN_ENTRY_PRICE is None
    # Old global dead-zone retired in favour of per-cell bands.
    assert SportsV4EdgeStrategy.ENTRY_PRICE_DEAD_ZONE is None
    # Per-cell bands now carry the gating; 0.10 must remain excluded from every
    # band (the historic dead-zone), and no band may exceed PRICE_MAX.
    bands = SportsV4EdgeStrategy.CELL_PRICE_BANDS
    assert bands  # whitelist is populated
    for cell, ranges in bands.items():
        for lo, hi in ranges:
            assert not (lo <= 0.10 <= hi), f"{cell} band {lo}-{hi} re-admits dead-zone 0.10"
            assert hi <= SportsV4EdgeStrategy.PRICE_MAX
