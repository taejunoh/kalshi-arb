from unittest.mock import MagicMock

from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy


def _make_strategy(slug="tennis_atp_french_open",
                   title="Will Sinner win the Sinner vs Nadal: Round Of 64 match?",
                   yes_sub_title="sinner"):
    """Build an A2 strategy with both stashed fields populated.

    Production behavior (2026-05-18 fix):
    - `_last_market_title` is the full Kalshi market title (used for round parsing)
    - `_last_yes_sub_title` is just the player name (used by individual matcher)
    Earlier test setups conflated the two — passing a full sentence as
    yes_sub_title hid the round-parse bug.
    """
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsAtpGsBo5ChalkV1Strategy.__new__(SportsAtpGsBo5ChalkV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.78
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    s._last_tournament_slug = slug
    s._last_yes_sub_title = yes_sub_title
    s._last_market_title = title
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


def test_round_parses_from_market_title_not_yes_sub_title():
    """Regression — 2026-05-18 incident.

    In production yes_sub_title is just the player name ("emilio nava")
    while round info ("Round Of 64") lives in market title only. Earlier
    code read yes_sub_title for round parsing, so A2 never fired even when
    all other gates passed (player names don't contain round tokens).
    """
    s = _make_strategy(
        title="Will Khachanov win the Gaston vs Khachanov: Round Of 64 match?",
        yes_sub_title="karen khachanov",  # realistic — player name only
    )
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.78,
                     ticker="KXATPMATCH-26MAY26GASKHA-KHA")
    assert sig is not None, "Round Of 64 in title should parse to R64 and fire"
    assert sig["side"] == "yes"


def test_no_fire_when_market_title_missing_round():
    """If the market title is junk (e.g. an unusual round name), skip."""
    s = _make_strategy(
        title="Will Federer win the Federer vs Nadal: Championship match?",
        yes_sub_title="roger federer",
    )
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.78,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_blocks_wta_and_other_team_sports():
    """A2 is ATP-only — must block WTA + MLB/NBA/NHL/soccer/UFC."""
    blocked = SportsAtpGsBo5ChalkV1Strategy.SPORT_BLOCKLIST
    assert "MLB" in blocked
    assert "NBA" in blocked
    assert "NHL" in blocked
    assert "WNBA" in blocked
    assert "UFC" in blocked
    assert "MLS" in blocked
    assert "EPL" in blocked
    assert "WTA" in blocked  # A2 is ATP-only — WTA must be blocked
    assert "ATP" not in blocked  # ATP is the sole target for A2
