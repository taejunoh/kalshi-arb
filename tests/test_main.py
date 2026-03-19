import pytest
from unittest.mock import MagicMock, patch


def test_bot_initializes():
    with patch("main.Config") as MockConfig, \
         patch("main.Database") as MockDB, \
         patch("main.PriceFeed") as MockFeed, \
         patch("main.PolymarketClient") as MockClient, \
         patch("main.MarketScanner") as MockScanner, \
         patch("main.OrderManager") as MockOM, \
         patch("main.SumArbStrategy") as MockSumArb, \
         patch("main.TradeLogger") as MockLogger:

        cfg = MockConfig.return_value
        cfg.is_paper_mode = True
        cfg.TRADING_MODE = "paper"
        cfg.BINANCE_WS_URLS = {"BTC": "wss://fake"}

        from main import Bot
        b = Bot()
        assert b.config.is_paper_mode is True
        assert b.sum_arb is not None
