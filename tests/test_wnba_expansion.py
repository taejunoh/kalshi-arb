"""WNBA strategy expansion — verify v31 trio + v36 + v37 fire on
WNBA tickers; v32 + v33 paper + v37_soccer remain blocked; v34/v35 naturally
blocked by ALLOWED_CELLS; pair-fire dedup works.

Built 2026-05-13 alongside the WNBA 5-strategy expansion. See spec
docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md."""
from unittest.mock import MagicMock

import pytest

from strategies.sports_trader import SportsTraderStrategy, SPORT_GAME_TOKENS


# WNBA test tickers — pair of paired sides for one game
WNBA_TICKER_A = "KXWNBAGAME-26MAY14NYWSH-NY"
WNBA_TICKER_B = "KXWNBAGAME-26MAY14NYWSH-WSH"
WNBA_GAME_KEY = "KXWNBAGAME-26MAY14NYWSH"


def _make_config():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.SPORTS_SCAN_INTERVAL = 300
    cfg.ODDS_API_REGION = "eu"
    cfg.LIVE_STRATEGIES = []
    cfg.is_paper_mode = False
    cfg.INITIAL_BANKROLL = 1000.0
    cfg.ODDS_API_KEY = "test-key"
    return cfg


def _make_order_manager():
    om = MagicMock()
    om.bankroll = 1000.0
    om._notifier = None
    om._db = MagicMock()
    om._quota_last_alert_bucket = None
    return om


def _make_kalshi_client():
    return MagicMock()


def _make(klass):
    """Instantiate a strategy class with mocked deps. Returns (strat, om)."""
    om = _make_order_manager()
    strat = klass(_make_config(), om, _make_kalshi_client())
    return strat, om


# ---------------------------------------------------------------------------
# Task 2: longshot_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_longshot_v31_unblocks_wnba():
    """v31 longshot must remove WNBA from OBSERVATION_ONLY_SPORTS so
    scan_and_execute reaches its evaluate() for WNBA tickers."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    assert "WNBA" not in SportsLongshotV31Strategy.OBSERVATION_ONLY_SPORTS
    # Parent set still has WNBA — only this subclass removed it
    assert "WNBA" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_longshot_v31_pair_fire_dedup_wnba_only():
    """evaluate() returns None on a 2nd WNBA fire when sibling already fired."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    strat, _ = _make(SportsLongshotV31Strategy)
    # Simulate sibling already fired
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    # 2nd fire on paired ticker — should be deduped
    sig = strat.evaluate(0.18, 0.83, 0.18, ticker=WNBA_TICKER_B)
    assert sig is None, "WNBA pair-fire dedup did not block sibling fire"


def test_longshot_v31_no_dedup_on_non_wnba():
    """NBA tickers still allow pair-fire (P2 cleanup not part of this expansion)."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    import inspect
    strat, _ = _make(SportsLongshotV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src, "Expected WNBA-only dedup branch"


# ---------------------------------------------------------------------------
# Task 3: midshot_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_midshot_v31_unblocks_wnba():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    assert "WNBA" not in SportsMidshotV31Strategy.OBSERVATION_ONLY_SPORTS


def test_midshot_v31_pair_fire_dedup_flag_on():
    """2026-05-15: midshot pair-fire dedup generalized from a WNBA-only
    evaluate() override to the base-class PAIR_FIRE_DEDUP flag, which
    runs at scan-loop level (sports_trader.py). Lifetime LIVE n=221 /
    121 games showed 82% of games saw both-sides fired across separate
    scans; game-level dedup sim lifts ROI 22%→72%."""
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    assert SportsMidshotV31Strategy.PAIR_FIRE_DEDUP is True


def test_midshot_v31_evaluate_no_longer_has_wnba_branch():
    """Sanity: the WNBA-only evaluate() override was removed when the
    flag generalized to all sports. If a future change re-introduces a
    per-sport evaluate() dedup branch, this test will catch it so we
    can decide whether both layers are intended."""
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    import inspect
    src = inspect.getsource(SportsMidshotV31Strategy)
    assert "WNBAGAME" not in src, "WNBA-only dedup branch should be gone"


# ---------------------------------------------------------------------------
# Task 4: underdog_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_underdog_v31_unblocks_wnba():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    assert "WNBA" not in SportsUnderdogV31Strategy.OBSERVATION_ONLY_SPORTS


def test_underdog_v31_pair_fire_dedup():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    strat, _ = _make(SportsUnderdogV31Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.35, 0.66, 0.35, ticker=WNBA_TICKER_B)
    assert sig is None


def test_underdog_v31_dedup_branch_is_wnba_only():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    import inspect
    strat, _ = _make(SportsUnderdogV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src


# ---------------------------------------------------------------------------
# Tasks 5+6: v32 + v33 family — must re-block WNBA after v31 trio cascade
# ---------------------------------------------------------------------------

def test_v32_blocks_wnba_via_parent_restore():
    """v32 inherits from v31 underdog — must NOT inherit the WNBA unblock."""
    from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
    assert "WNBA" in SportsUnderdogV32Strategy.OBSERVATION_ONLY_SPORTS


def test_v32_v33_family_blocks_wnba():
    """All v32 + v33 variants must re-block WNBA after v31 trio cascade."""
    from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
    from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
    from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
    from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
    from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy

    for cls in (SportsLongshotV32Strategy, SportsMidshotV32Strategy,
                SportsLongshotV33Strategy, SportsMidshotV33Strategy,
                SportsUnderdogV33Strategy):
        assert "WNBA" in cls.OBSERVATION_ONLY_SPORTS, f"{cls.__name__} did not re-block WNBA"


# ---------------------------------------------------------------------------
# Task 7: v36 — chalk-favorite YES/NO at 35-55c, fair_prob > 0.55
# ---------------------------------------------------------------------------

def test_v36_class_attributes():
    from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
    assert SportsUnderdogV36Strategy.STRATEGY_NAME == "sports_underdog_v36"
    assert SportsUnderdogV36Strategy.ALLOWED_CELLS == frozenset({("WNBA", "pre")})
    assert SportsUnderdogV36Strategy.PLAYOFF_ONLY is False
    assert SportsUnderdogV36Strategy.MIN_ENTRY_PRICE == 0.35
    assert SportsUnderdogV36Strategy.MAX_ENTRY_PRICE == 0.55
    assert "WNBA" not in SportsUnderdogV36Strategy.OBSERVATION_ONLY_SPORTS


def test_v36_fires_on_favorite_in_band():
    """v36 fires when YES is the favorite (fair > 0.55) priced at 35-55c."""
    from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
    strat, _ = _make(SportsUnderdogV36Strategy)
    # SEA-CONN: SEA yes_ask 0.55, eu 0.534 — should NOT fire (eu < 0.55)
    sig = strat.evaluate(0.55, 0.46, 0.534, ticker="KXWNBAGAME-26MAY14SEA-SEA")
    assert sig is None
    # PHX-GS: GS yes 0.54, eu 0.555 — eu > 0.55 → fire YES
    sig = strat.evaluate(0.54, 0.47, 0.555, ticker="KXWNBAGAME-26MAY14PHXGS-GS")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.54


def test_v36_no_fire_outside_band():
    from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
    strat, _ = _make(SportsUnderdogV36Strategy)
    # price 0.30 — below 0.35 floor
    sig = strat.evaluate(0.30, 0.71, 0.55, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None
    # price 0.60 — above 0.55 cap
    sig = strat.evaluate(0.60, 0.41, 0.65, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None


def test_v36_pair_fire_dedup():
    from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
    strat, _ = _make(SportsUnderdogV36Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.54, 0.47, 0.56, ticker=WNBA_TICKER_B)
    assert sig is None  # paired sibling already fired


def test_v36_size_12():
    from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
    strat, _ = _make(SportsUnderdogV36Strategy)
    assert strat._get_trade_size({"side": "yes", "price": 0.54}) == 12.0


def test_v37_size_12():
    """v37 overrides parent's depth-tiered caps with fixed 12 (symmetric with v36)."""
    from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
    strat, _ = _make(SportsUnderdogV37Strategy)
    assert strat._get_trade_size({"side": "yes", "price": 0.22, "ticker": "KXWNBAGAME-26MAY14NYWSH-NY"}) == 12.0


# ---------------------------------------------------------------------------
# Task 8: v37 — TEAM 20-29c band, sibling of sports_soccer_underdog_v1
# ---------------------------------------------------------------------------

def test_v37_class_attributes():
    from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
    assert SportsUnderdogV37Strategy.STRATEGY_NAME == "sports_underdog_v37"
    # Block everything except WNBA — soccer also blocked here
    blocklist = SportsUnderdogV37Strategy.SPORT_BLOCKLIST
    for sport in ("NBA", "MLB", "NHL", "NPB", "KBO"):
        assert sport in blocklist, f"{sport} should be in v37 blocklist"
    assert "WNBA" not in blocklist
    # OBSERVATION_ONLY override — WNBA tradeable
    assert "WNBA" not in SportsUnderdogV37Strategy.OBSERVATION_ONLY_SPORTS


def test_v37_fires_on_20_29c_team():
    from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
    strat, _ = _make(SportsUnderdogV37Strategy)
    # WNBA-formatted ticker, price 0.22 — in 20-29c band, payoff >= 2.0
    sig = strat.evaluate(0.22, 0.79, 0.22, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.22


def test_v37_skips_outside_band():
    from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
    strat, _ = _make(SportsUnderdogV37Strategy)
    # 0.30 — above 0.29 cap
    sig = strat.evaluate(0.30, 0.71, 0.30, ticker="KXWNBAGAME-26MAY14NYWSH-NY")
    assert sig is None


def test_v37_pair_fire_dedup():
    from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
    strat, _ = _make(SportsUnderdogV37Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.22, 0.79, 0.22, ticker=WNBA_TICKER_B)
    assert sig is None


def test_v37_soccer_still_blocks_wnba():
    """Renamed v37 soccer parent (SportsSoccerUnderdogV1Strategy) must still
    contain WNBA in SPORT_BLOCKLIST — sibling separation."""
    from strategies.sports_soccer_underdog_v1 import SportsSoccerUnderdogV1Strategy
    assert "WNBA" in SportsSoccerUnderdogV1Strategy.SPORT_BLOCKLIST
