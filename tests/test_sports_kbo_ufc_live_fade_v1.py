from unittest.mock import MagicMock

from strategies.sports_kbo_live_fade_tight_v1 import (
    SportsKboLiveFadeTightV1Strategy,
)
from strategies.sports_ufc_live_fade_v1 import SportsUfcLiveFadeV1Strategy


def _make(cls, pin=0.80, pin_age=100):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = cls.__new__(cls)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = pin
    s._last_pinnacle_age_s = pin_age
    s._traded_tickers = {}
    return s


# ---------- KBO tight fade ----------

def test_kbo_fires_yes_at_tight_corner():
    s = _make(SportsKboLiveFadeTightV1Strategy, pin=0.80)
    sig = s.evaluate(kalshi_yes=0.94, kalshi_no=0.08, ticker="KXKBOGAME-X",
                     fair_prob=0.82)
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.94


def test_kbo_skips_gap_11pp_below_gate():
    s = _make(SportsKboLiveFadeTightV1Strategy, pin=0.83)  # gap 0.11 < 0.12
    sig = s.evaluate(kalshi_yes=0.94, kalshi_no=0.08, ticker="KXKBOGAME-X",
                     fair_prob=0.85)
    assert sig is None


def test_kbo_skips_fav_below_093():
    s = _make(SportsKboLiveFadeTightV1Strategy, pin=0.75)
    sig = s.evaluate(kalshi_yes=0.92, kalshi_no=0.10, ticker="KXKBOGAME-X",
                     fair_prob=0.78)
    assert sig is None


def test_kbo_fires_no_side_mirror():
    s = _make(SportsKboLiveFadeTightV1Strategy, pin=0.20)  # pin NO = 0.80
    sig = s.evaluate(kalshi_yes=0.08, kalshi_no=0.94, ticker="KXKBOGAME-X",
                     fair_prob=0.25)
    assert sig is not None
    assert sig["side"] == "no"


def test_kbo_opted_out_of_observation_only():
    assert "KBO" not in SportsKboLiveFadeTightV1Strategy.OBSERVATION_ONLY_SPORTS
    assert SportsKboLiveFadeTightV1Strategy.ALLOWED_CELLS == frozenset(
        {("KBO", "live")}
    )


def test_kbo_honest_booking_at_signal_ask():
    s = _make(SportsKboLiveFadeTightV1Strategy, pin=0.80)
    sig = s.evaluate(kalshi_yes=0.94, kalshi_no=0.08, ticker="KXKBOGAME-X",
                     fair_prob=0.82)
    assert s._get_limit_price(sig, fair_prob=0.82) == 0.94


# ---------- UFC live fade ----------

def test_ufc_fires_yes_when_pin_lags():
    s = _make(SportsUfcLiveFadeV1Strategy, pin=0.78)
    sig = s.evaluate(kalshi_yes=0.88, kalshi_no=0.14, ticker="KXUFCFIGHT-X",
                     fair_prob=0.80)
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.88


def test_ufc_skips_stale_pinnacle():
    s = _make(SportsUfcLiveFadeV1Strategy, pin=0.78, pin_age=601)
    sig = s.evaluate(kalshi_yes=0.88, kalshi_no=0.14, ticker="KXUFCFIGHT-X",
                     fair_prob=0.80)
    assert sig is None


def test_ufc_skips_gap_below_5pp():
    s = _make(SportsUfcLiveFadeV1Strategy, pin=0.85)
    sig = s.evaluate(kalshi_yes=0.88, kalshi_no=0.14, ticker="KXUFCFIGHT-X",
                     fair_prob=0.86)
    assert sig is None


def test_ufc_individual_sport_opt_in():
    assert SportsUfcLiveFadeV1Strategy.INDIVIDUAL_SPORTS_OPT_IN is True
    assert "UFC" not in SportsUfcLiveFadeV1Strategy.OBSERVATION_ONLY_SPORTS
    assert SportsUfcLiveFadeV1Strategy.ALLOWED_CELLS == frozenset(
        {("UFC", "live")}
    )


def test_ufc_honest_booking_at_signal_ask():
    s = _make(SportsUfcLiveFadeV1Strategy, pin=0.78)
    sig = s.evaluate(kalshi_yes=0.88, kalshi_no=0.14, ticker="KXUFCFIGHT-X",
                     fair_prob=0.80)
    assert s._get_limit_price(sig, fair_prob=0.80) == 0.88


# ---------- junk-quote floor (2026-07-02 review finding) ----------

def test_deep_chalk_strategies_lower_mirror_ask_floor():
    """Tight-book heavy favorites (na < 0.05 ⟺ yes_bid >= 0.96) must reach
    evaluate() for the deep-chalk fade cohorts — floor 0.01, not the parent
    default 0.05, so the forward paper cohort reproduces the replay cohort."""
    from strategies.sports_trader import SportsTraderStrategy

    assert SportsTraderStrategy.MIRROR_ASK_FLOOR == 0.05  # default preserved
    assert SportsKboLiveFadeTightV1Strategy.MIRROR_ASK_FLOOR == 0.01
    assert SportsUfcLiveFadeV1Strategy.MIRROR_ASK_FLOOR == 0.01
