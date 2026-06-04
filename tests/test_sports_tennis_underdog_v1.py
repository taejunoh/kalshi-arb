"""Tennis underdog band (0.25-0.29 BUY YES) paper strategy — 2026-05-19.

Band tightened 0.35 -> 0.29 on 2026-05-31 (the 0.30-0.35 sub-band bled
-$121.76 / WR 14.8%). Tests use kalshi_yes=0.28 (mid-band) accordingly.
"""
from unittest.mock import MagicMock

from strategies.sports_tennis_underdog_v1 import SportsTennisUnderdogV1Strategy


def _make(pin=0.32):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisUnderdogV1Strategy.__new__(SportsTennisUnderdogV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = pin
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    s._last_tournament_slug = "tennis_atp_french_open"
    s._last_yes_sub_title = "carlos alcaraz"
    s._last_market_title = "Will Alcaraz win the Sinner vs Alcaraz: Round Of 64 match?"
    s._last_mts = 1000.0
    return s


def test_fires_inside_underdog_band():
    s = _make(pin=0.32)
    sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.72, fair_prob=0.29,
                     ticker="KXATPMATCH-26MAY26ABCDEF-ABC")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.28


def test_skips_below_band():
    s = _make(pin=0.20)
    sig = s.evaluate(kalshi_yes=0.20, kalshi_no=0.81, fair_prob=0.21,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_above_band():
    s = _make(pin=0.40)
    sig = s.evaluate(kalshi_yes=0.40, kalshi_no=0.61, fair_prob=0.40,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees_strongly():
    """Pinnacle says underdog is MUCH less likely than Kalshi → veto."""
    s = _make(pin=0.18)  # pin=0.18, kalshi_yes=0.28, gap=-0.10 < -0.05
    sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.72, fair_prob=0.18,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_fires_when_pinnacle_mildly_disagrees():
    """Mild Pinnacle disagreement (within 5pp) still fires — keeps fire rate up."""
    s = _make(pin=0.25)  # pin=0.25, kalshi_yes=0.28, gap=-0.03 inside tolerance
    sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.72, fair_prob=0.25,
                     ticker="KXATPMATCH-X")
    assert sig is not None


def test_accepts_stale_pinnacle():
    """TU1 signal is price-band, not Pinnacle-event-driven. A stale Pinnacle
    line is still useful as a sanity veto — don't gate on age. 2026-05-19:
    inherited age gate was rejecting all candidates and producing 0 fires."""
    s = _make(pin=0.32)
    s._last_pinnacle_age_s = 100000  # very old — must still fire
    sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.72, fair_prob=0.32,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_skips_when_pinnacle_missing():
    s = _make(pin=None)
    sig = s.evaluate(kalshi_yes=0.30, kalshi_no=0.71, fair_prob=0.32,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_opts_into_individual_sports():
    assert SportsTennisUnderdogV1Strategy.INDIVIDUAL_SPORTS_OPT_IN is True


def test_blocks_team_sports_and_ufc():
    blocked = SportsTennisUnderdogV1Strategy.SPORT_BLOCKLIST
    for sport in ("NBA", "WNBA", "MLB", "NHL", "NPB", "KBO",
                  "MLS", "EPL", "LAL", "UCL", "BUN", "UFC"):
        assert sport in blocked, f"{sport} must be blocked from tennis underdog"
    # ATP/WTA must NOT be blocked — that's the target universe
    assert "ATP" not in blocked
    assert "WTA" not in blocked


def test_size_caps_at_4():
    s = _make()
    size = s._get_trade_size({"price": 0.30, "edge": 0.02})
    assert size <= 4.0


def test_price_band_constants():
    """Lock the calibrated band — change requires re-running the backtest.
    MAX tightened 0.35 -> 0.29 on 2026-05-31 (0.30-0.35 sub-band bled)."""
    assert SportsTennisUnderdogV1Strategy.MIN_ENTRY_PRICE == 0.25
    assert SportsTennisUnderdogV1Strategy.MAX_ENTRY_PRICE == 0.29
