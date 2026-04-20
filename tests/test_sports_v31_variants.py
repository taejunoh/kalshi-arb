"""Smoke tests for V3.1 strategy variants — verify name, file, and region pinning."""
from unittest.mock import MagicMock

from strategies.sports_longshot import SportsLongshotStrategy
from strategies.sports_midshot import SportsMidshotStrategy
from strategies.sports_underdog import SportsUnderdogStrategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy


def _make(klass):
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    return klass(config, MagicMock(), MagicMock())


def test_v3_pinned_to_us():
    assert _make(SportsLongshotStrategy)._get_odds_region() == "us"
    assert _make(SportsMidshotStrategy)._get_odds_region() == "us"
    assert _make(SportsUnderdogStrategy)._get_odds_region() == "us"


def test_v4_all_use_eu_region():
    """All V4 strategies must use EU region, not inherit midshot's us override.
    (sports_liquidity_filtered retired 2026-04-15 — dup of sports_midshot_v31.)"""
    from strategies.sports_pinnacle_edge import SportsPinnacleEdgeStrategy
    from strategies.sports_v4_edge import SportsV4EdgeStrategy
    from strategies.sports_consolidated import SportsConsolidatedStrategy
    for cls in (SportsPinnacleEdgeStrategy, SportsV4EdgeStrategy,
                SportsConsolidatedStrategy):
        assert _make(cls)._get_odds_region() == "eu", f"{cls.__name__} not on eu"


def test_v31_pinned_to_eu():
    assert _make(SportsLongshotV31Strategy)._get_odds_region() == "eu"
    assert _make(SportsMidshotV31Strategy)._get_odds_region() == "eu"
    assert _make(SportsUnderdogV31Strategy)._get_odds_region() == "eu"


def test_v31_has_distinct_names_and_files():
    for v3_cls, v31_cls in [
        (SportsLongshotStrategy, SportsLongshotV31Strategy),
        (SportsMidshotStrategy, SportsMidshotV31Strategy),
        (SportsUnderdogStrategy, SportsUnderdogV31Strategy),
    ]:
        v3 = _make(v3_cls)
        v31 = _make(v31_cls)
        assert v3.STRATEGY_NAME != v31.STRATEGY_NAME
        assert v3.TRADED_TICKERS_FILE != v31.TRADED_TICKERS_FILE
        assert v31.STRATEGY_NAME.endswith("_v31")


def test_v31_inherits_evaluate_logic():
    """V3.1 must produce the same signal as V3 on the same inputs (only region differs)."""
    v3 = _make(SportsLongshotStrategy)
    v31 = _make(SportsLongshotV31Strategy)
    args = {"kalshi_yes": 0.15, "kalshi_no": 0.88, "fair_prob": 0.20, "ticker": "T"}
    assert v3.evaluate(**args) == v31.evaluate(**args)


def test_v31_entry_price_gates():
    """2026-04-20 paper+live re-segmentation:
      - longshot_v31: <10¢ +166% (n=6), 10–20¢ −35~−78% → dead-zone (0.10, 0.20)
      - midshot_v31 / underdog_v31: <20¢ 전부 0% win → simple 20¢ floor
    """
    assert SportsLongshotV31Strategy.ENTRY_PRICE_DEAD_ZONE == (0.10, 0.20)
    assert SportsLongshotV31Strategy.MIN_ENTRY_PRICE is None
    assert SportsMidshotV31Strategy.MIN_ENTRY_PRICE == 0.20
    assert SportsUnderdogV31Strategy.MIN_ENTRY_PRICE == 0.20


# ---------- V3.2 (edge-scaled sizing) ----------

def test_v32_scales_size_by_payoff():
    """v32 multiplies base by clamp(payoff/threshold, 0.5, 2.0)."""
    from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
    from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
    from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy

    ls = _make(SportsLongshotV32Strategy)
    ls._order_manager.bankroll = 1000.0
    # At threshold (3.0x), multiplier == 1.0 → base $4
    assert ls._get_trade_size({"edge": 3.0}) == 4.0
    # 2× threshold → 2.0×
    assert ls._get_trade_size({"edge": 6.0}) == 8.0
    # 10× threshold → clamped to 2.0×
    assert ls._get_trade_size({"edge": 30.0}) == 8.0
    # 0.5× threshold → clamped to 0.5×
    assert ls._get_trade_size({"edge": 0.1}) == 2.0

    ms = _make(SportsMidshotV32Strategy)
    ms._order_manager.bankroll = 1000.0
    # Midshot threshold 2.5, at threshold → 1.0×
    assert ms._get_trade_size({"edge": 2.5}) == 4.0
    assert ms._get_trade_size({"edge": 5.0}) == 8.0

    ud = _make(SportsUnderdogV32Strategy)
    ud._order_manager.bankroll = 1000.0
    # Underdog threshold 2.0
    assert ud._get_trade_size({"edge": 2.0}) == 4.0
    assert ud._get_trade_size({"edge": 4.0}) == 8.0


def test_v32_inherits_v31_entry_price_gates():
    """v32 inherits parent v31's price gates (dead-zone or floor)."""
    from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
    from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
    from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
    assert SportsLongshotV32Strategy.ENTRY_PRICE_DEAD_ZONE == (0.10, 0.20)
    assert SportsLongshotV32Strategy.MIN_ENTRY_PRICE is None
    assert SportsMidshotV32Strategy.MIN_ENTRY_PRICE == 0.20
    assert SportsUnderdogV32Strategy.MIN_ENTRY_PRICE == 0.20
    # v32 has no per-game cap
    assert SportsLongshotV32Strategy.PER_GAME_CAP_USDC is None


# ---------- V3.3 (per-game cap) ----------

def test_v33_sets_per_game_cap_and_inherits_v31_price_gates():
    from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
    from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
    from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy
    for cls in (SportsLongshotV33Strategy, SportsMidshotV33Strategy,
                SportsUnderdogV33Strategy):
        assert cls.PER_GAME_CAP_USDC == 12.0, cls.__name__
    # v33 inherits v31 price gates: longshot dead-zone, siblings 20¢ floor.
    assert SportsLongshotV33Strategy.ENTRY_PRICE_DEAD_ZONE == (0.10, 0.20)
    assert SportsMidshotV33Strategy.MIN_ENTRY_PRICE == 0.20
    assert SportsUnderdogV33Strategy.MIN_ENTRY_PRICE == 0.20


def test_v33_cap_state_is_shared_via_order_manager():
    """All v33 siblings must read/write the same dict so cross-strategy
    game exposure accumulates correctly."""
    from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
    from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
    from unittest.mock import MagicMock

    om = MagicMock()
    om._v33_game_spend = {}

    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"

    ls = SportsLongshotV33Strategy(config, om, MagicMock())
    ms = SportsMidshotV33Strategy(config, om, MagicMock())
    # Mutating via one sibling is visible to the other (same dict reference).
    ls._order_manager._v33_game_spend["GAME-X"] = 3.0
    assert ms._order_manager._v33_game_spend["GAME-X"] == 3.0
