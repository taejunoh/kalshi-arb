"""Persistent cross-scan pair-fire dedup (2026-05-15).

The base class scan loop already blocks same-game both-sides fires within a
single scan cycle via the local `traded_games` set. The PAIR_FIRE_DEDUP class
flag extends that protection across scan cycles by checking `_traded_tickers`
for any prior ticker sharing this ticker's game key (ticker minus last
"-SIDE" suffix).

Per-strategy enablement is keyed off lifetime LIVE pair-fire analysis (see
memory cross_strategy_tier_2026-05-12 + the 2026-05-15 v31-trio audit):

  enable  : midshot_v31 (90% trade pair-fire, dedup +50pp ROI),
            midshot_v35 (100% pair-fire, dedup +13pp),
            v4_edge     (29% pair-fire, dedup +45pp)
  disable : longshot_v31 (dedup HURTS by -36pp),
            underdog_v31/v32 (~0pp / -7pp; not worth the loss-of-fires risk),
            playoff_underdog_v1 (already bleeding, dedup ~no-op)
"""
from unittest.mock import MagicMock

from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
from strategies.sports_midshot_v35 import SportsMidshotV35Strategy
from strategies.sports_v4_edge import SportsV4EdgeStrategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy


def test_base_default_is_off():
    assert SportsTraderStrategy.PAIR_FIRE_DEDUP is False


def test_flag_on_for_targeted_strategies():
    assert SportsMidshotV31Strategy.PAIR_FIRE_DEDUP is True
    assert SportsMidshotV35Strategy.PAIR_FIRE_DEDUP is True
    assert SportsV4EdgeStrategy.PAIR_FIRE_DEDUP is True


def test_flag_off_for_dedup_hurts_strategies():
    """Lifetime audit: dedup hurts longshot_v31 (-36pp). Keep flag off."""
    assert SportsLongshotV31Strategy.PAIR_FIRE_DEDUP is False


def test_flag_off_for_underdog_v31():
    """Lifetime audit: dedup ~neutral for underdog_v31 (0pp). Not worth
    risking loss-of-fires until paired-trade thesis stabilizes."""
    assert SportsUnderdogV31Strategy.PAIR_FIRE_DEDUP is False


def test_scan_loop_contains_dedup_check():
    """The dedup mechanism lives in scan_and_execute. Verify the source
    references game_key + _traded_tickers so a future refactor doesn't
    silently remove the cross-scan protection."""
    import inspect
    src = inspect.getsource(SportsTraderStrategy.scan_and_execute)
    assert "PAIR_FIRE_DEDUP" in src
    assert "game_key" in src
    assert "rsplit" in src
