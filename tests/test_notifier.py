import pytest
import time
from monitoring.notifier import TelegramNotifier


def test_rate_limiting_for_throttled_types():
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    assert notifier._should_send("error") is True
    notifier._last_sent["error"] = time.time()
    assert notifier._should_send("error") is False
    # Different throttled type still gets through.
    assert notifier._should_send("bot_status") is True


def test_trade_and_settlement_are_unthrottled():
    # Each fill/settle is a distinct event the user needs to see — sharing a
    # 60s window silently dropped back-to-back live fills.
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    notifier._last_sent["trade"] = time.time()
    notifier._last_sent["settlement"] = time.time()
    assert notifier._should_send("trade") is True
    assert notifier._should_send("settlement") is True


def test_format_trade_message():
    notifier = TelegramNotifier(bot_token="fake", chat_id="fake")
    msg = notifier.format_trade(
        strategy="sum_arb", market_id="m1", direction="BUY",
        price=0.48, size=100.0, expected_profit=4.0,
    )
    assert "sum_arb" in msg
    assert "$100.00" in msg or "100.0" in msg
    assert "$4.00" in msg or "4.0" in msg


def test_disabled_when_no_token():
    notifier = TelegramNotifier(bot_token="", chat_id="")
    assert notifier.is_enabled is False

    notifier2 = TelegramNotifier(bot_token="abc", chat_id="123")
    assert notifier2.is_enabled is True
