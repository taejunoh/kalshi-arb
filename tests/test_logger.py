import os
import csv
import pytest
from monitoring.logger import setup_logger, TradeLogger


def test_setup_logger_returns_logger(tmp_path):
    logger = setup_logger("test_bot", log_dir=str(tmp_path))
    assert logger.name == "test_bot"
    assert len(logger.handlers) >= 2


def test_trade_logger_writes_csv(tmp_path):
    tl = TradeLogger(log_dir=str(tmp_path))
    tl.log_trade(
        strategy="sum_arb", market_id="market_123", direction="YES",
        price=0.48, size=100.0, fee=1.51, pnl=2.0, status="filled",
    )
    csv_path = tmp_path / "trades.csv"
    assert csv_path.exists()
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["strategy"] == "sum_arb"
    assert rows[0]["price"] == "0.48"


def test_trade_logger_appends(tmp_path):
    tl = TradeLogger(log_dir=str(tmp_path))
    tl.log_trade("sum_arb", "m1", "YES", 0.48, 100, 1.5, 2.0, "filled")
    tl.log_trade("sum_arb", "m2", "NO", 0.49, 50, 0.75, -1.0, "filled")
    csv_path = tmp_path / "trades.csv"
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
