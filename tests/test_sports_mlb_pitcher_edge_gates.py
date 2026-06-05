"""Gates added 2026-06-05 after the MLB-pitcher LIVE-readiness audit.

Two defects were fixed at the root:
  1. PIT collector has no min-IP floor → 1-2-start pitchers get garbage ERAs
     that manufacture fake >=2.5 gaps (and drive the season-vs-PIT collector
     disagreement). v2/v2.1 now require both starters >= MIN_PITCHER_IP.
  2. Probable starters flip between a >24h-early fire and game day → the strategy
     could bet opposite sides of one game across scan cycles. All three variants
     now set PAIR_FIRE_DEDUP=True (scan-loop game_key dedup).
"""
import time
from unittest.mock import MagicMock

from strategies.sports_mlb_pitcher_edge_v1 import SportsMlbPitcherEdgeV1Strategy
from strategies.sports_mlb_pitcher_edge_v2 import SportsMlbPitcherEdgeV2Strategy
from strategies.sports_mlb_pitcher_edge_v2_1 import SportsMlbPitcherEdgeV21Strategy


def _make_v2(cls=SportsMlbPitcherEdgeV2Strategy):
    s = cls.__new__(cls)
    s.config = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s._order_manager = MagicMock(bankroll=10000)
    s._order_manager.sizing_bankroll = lambda x: 10000
    s._traded_tickers = {}
    return s


def _seed_cache(s, *, era_diff, away_ip, home_ip, better="HOM",
                away_era=5.0, home_era=2.0, ticker="KXMLBGAME-TEST-HOM"):
    """Seed the in-memory PIT cache and freeze TTL so evaluate() never hits DB."""
    info = {
        "era_diff": era_diff, "better_team": better,
        "away_era": away_era, "home_era": home_era,
        "away_code": "AWY", "home_code": "HOM",
        "away_pitcher": "A. Pitcher", "home_pitcher": "H. Pitcher",
        "away_ip": away_ip, "home_ip": home_ip,
    }
    s._pit_cache = {"home": {ticker: info}, "away": {}}
    s._pit_cache_ts = time.time() + 10_000  # far future → skip refresh
    return ticker


# --- PAIR_FIRE_DEDUP set on all three variants -----------------------------

def test_pair_fire_dedup_enabled_on_all_variants():
    assert SportsMlbPitcherEdgeV1Strategy.PAIR_FIRE_DEDUP is True
    assert SportsMlbPitcherEdgeV2Strategy.PAIR_FIRE_DEDUP is True
    assert SportsMlbPitcherEdgeV21Strategy.PAIR_FIRE_DEDUP is True


def test_v21_inherits_min_pitcher_ip():
    assert SportsMlbPitcherEdgeV2Strategy.MIN_PITCHER_IP == 20.0
    assert SportsMlbPitcherEdgeV21Strategy.MIN_PITCHER_IP == 20.0


# --- min-IP gate -----------------------------------------------------------

def test_fires_when_both_starters_have_stable_ip():
    s = _make_v2()
    tk = _seed_cache(s, era_diff=3.0, away_ip=45.0, home_ip=40.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.55


def test_skips_when_a_starter_is_small_sample():
    """Same game, but the home starter has only 10 IP (1-2 starts) → reject.
    This is the 6/05 fix: a tiny-sample ERA must not manufacture a fake gap."""
    s = _make_v2()
    tk = _seed_cache(s, era_diff=3.0, away_ip=45.0, home_ip=10.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is None


def test_skips_when_ip_missing():
    s = _make_v2()
    tk = _seed_cache(s, era_diff=3.0, away_ip=None, home_ip=40.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is None


def test_ip_gate_boundary_inclusive_at_20():
    s = _make_v2()
    tk = _seed_cache(s, era_diff=3.0, away_ip=20.0, home_ip=20.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is not None  # >= MIN_PITCHER_IP passes


def test_v21_min_ip_gate_active_via_super():
    """v2.1 overrides evaluate() but delegates to super() → IP gate still runs."""
    s = _make_v2(SportsMlbPitcherEdgeV21Strategy)
    # stub the extra book-depth gate so only the IP gate decides the outcome
    s._check_books_deep = lambda ticker: True
    tk = _seed_cache(s, era_diff=3.0, away_ip=45.0, home_ip=8.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is None


def test_era_gate_still_rejects_below_threshold():
    """Sanity: the pre-existing era<2.5 reject is unaffected by the new gate."""
    s = _make_v2()
    tk = _seed_cache(s, era_diff=2.0, away_ip=45.0, home_ip=40.0)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.5, ticker=tk)
    assert sig is None
