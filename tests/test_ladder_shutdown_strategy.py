import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.ladder_shutdown import LadderShutdownStrategy


@pytest.mark.asyncio
async def test_suffix_parser_t_and_g():
    s = LadderShutdownStrategy(
        config=MagicMock(LADDER_MIN_ARB_EDGE_USDC=0.02,
                          LADDER_MIN_FADE_INVERSION=0.05,
                          LADDER_MIN_DEPTH_USDC=30.0,
                          LADDER_MAX_POSITION_PER_GROUP=50.0,
                          KALSHI_FEE_PER_CONTRACT=0.07),
        order_manager=MagicMock(), market_scanner=MagicMock(),
        kalshi_client=MagicMock(),
    )
    assert s._parse_suffix("T2") == 2.0
    assert s._parse_suffix("T17") == 17.0
    assert s._parse_suffix("G95") == 95.0
    assert s._parse_suffix("BAD") is None


@pytest.mark.asyncio
async def test_event_key_extraction():
    s = LadderShutdownStrategy(
        config=MagicMock(), order_manager=MagicMock(),
        market_scanner=MagicMock(), kalshi_client=MagicMock(),
    )
    m = {"ticker": "KXNUMSHUTDOWNS-27JAN01-T2"}
    assert s._extract_event_key(m) == "27JAN01"


@pytest.mark.asyncio
async def test_strategy_universe_filter_includes_only_targeted_series():
    s = LadderShutdownStrategy(
        config=MagicMock(), order_manager=MagicMock(),
        market_scanner=MagicMock(), kalshi_client=MagicMock(),
    )
    assert s._is_target_market({"ticker": "KXNUMSHUTDOWNS-27JAN01-T2"}) is True
    assert s._is_target_market({"ticker": "KXGOVTSHUTLENGTH-26FEB07-G95"}) is True
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
    assert s._is_target_market({"ticker": "KXFEDMENTION-26APR-YIEL"}) is False
