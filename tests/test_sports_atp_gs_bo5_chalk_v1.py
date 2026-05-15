from unittest.mock import MagicMock

from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy


def _make_strategy(slug="tennis_atp_french_open", title="Will Sinner win the Sinner vs Nadal: Round Of 64 match?"):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsAtpGsBo5ChalkV1Strategy.__new__(SportsAtpGsBo5ChalkV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.78
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    s._last_tournament_slug = slug
    s._last_yes_sub_title = title
    return s


def test_fires_on_french_open_r64_chalk():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_skips_us_open_qf_round():
    s = _make_strategy(title="Will Alcaraz win the Alcaraz vs Djokovic: Quarterfinal match?")
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_bo3_atp_1000():
    s = _make_strategy(slug="tennis_atp_italian_open",
                       title="Will X win the X vs Y: Round Of 64 match?")
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_below_price_band():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.65, kalshi_no=0.35, fair_prob=0.66,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_above_price_band():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.90, kalshi_no=0.10, fair_prob=0.90,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.68
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.68,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_slug_missing():
    s = _make_strategy(slug=None)
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None
