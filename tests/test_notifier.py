import pytest
import time
from monitoring.notifier import TelegramNotifier


def test_rate_limiting():
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    # First message should not be rate-limited
    assert notifier._should_send("trade") is True
    # Record the send
    notifier._last_sent["trade"] = time.time()
    # Immediate second message of same type should be rate-limited
    assert notifier._should_send("trade") is False
    # Different type should not be rate-limited
    assert notifier._should_send("error") is True


def test_format_trade_message():
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    msg = notifier.format_trade(
        strategy="sum_arb", market_id="m1", direction="BUY",
        price=0.48, size=100.0, expected_profit=4.0,
    )
    assert "sum_arb" in msg
    assert "$100.00" in msg or "100.0" in msg
    assert "$4.00" in msg or "4.0" in msg


def test_format_daily_summary():
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    msg = notifier.format_daily_summary(
        total_trades=15, wins=14, losses=1, daily_pnl=52.30, bankroll=1052.30,
    )
    assert "15" in msg
    assert "52.30" in msg or "52.3" in msg


def test_disabled_when_no_token():
    notifier = TelegramNotifier(bot_token="", chat_id="")
    assert notifier.is_enabled is False

    notifier2 = TelegramNotifier(bot_token="abc", chat_id="123")
    assert notifier2.is_enabled is True
