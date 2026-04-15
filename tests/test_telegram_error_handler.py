import asyncio
import logging
import time
from unittest.mock import MagicMock, AsyncMock
import pytest

from monitoring.telegram_error_handler import TelegramErrorHandler, BACKOFF_SCHEDULE


def _mk_notifier():
    n = MagicMock()
    n.is_enabled = True
    n.send = AsyncMock()
    return n


def _mk_record(name: str, msg: str, level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord(name, level, "", 0, msg, None, None)


@pytest.mark.asyncio
async def test_first_error_sends_immediately():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    h.emit(_mk_record("kalshi_client", "Market order failed: 400"))
    await asyncio.sleep(0.01)  # let create_task run
    n.send.assert_called_once()


@pytest.mark.asyncio
async def test_same_signature_backoff_suppresses():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    record = _mk_record("kalshi_client", "Market order failed: 400 A")
    h.emit(record); await asyncio.sleep(0.01)
    h.emit(record); await asyncio.sleep(0.01)  # 2nd immediate — backoff 60s not elapsed
    assert n.send.call_count == 1


@pytest.mark.asyncio
async def test_different_signatures_both_send():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    h.emit(_mk_record("kalshi_client", "Market order failed: VANEDM-VAN"))
    h.emit(_mk_record("kalshi_client", "Market order failed: VANEDM-EDM"))
    await asyncio.sleep(0.01)
    assert n.send.call_count == 2


@pytest.mark.asyncio
async def test_different_loggers_both_send():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    h.emit(_mk_record("kalshi_client", "failed"))
    h.emit(_mk_record("order_manager", "failed"))
    await asyncio.sleep(0.01)
    assert n.send.call_count == 2


@pytest.mark.asyncio
async def test_excluded_logger_not_sent():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    h.emit(_mk_record("notifier", "send failed"))
    await asyncio.sleep(0.01)
    n.send.assert_not_called()


@pytest.mark.asyncio
async def test_backoff_schedule_advances():
    """After send, internal state tracks count and last_ts."""
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    record = _mk_record("test", "same error")
    h.emit(record); await asyncio.sleep(0.01)
    sig = h._signature(record)
    # Simulate 2 minutes passed — 1min cooldown elapsed
    h._state[sig] = (time.time() - 120, 1)
    h.emit(record); await asyncio.sleep(0.01)
    assert n.send.call_count == 2


@pytest.mark.asyncio
async def test_below_error_level_ignored():
    n = _mk_notifier()
    h = TelegramErrorHandler(n)
    h.setLevel(logging.ERROR)
    # Handler level filters — warning record below threshold
    warning_rec = _mk_record("test", "oops", level=logging.WARNING)
    # emit is called only if logger dispatched; simulate dispatch path:
    if warning_rec.levelno >= h.level:
        h.emit(warning_rec)
    await asyncio.sleep(0.01)
    n.send.assert_not_called()


@pytest.mark.asyncio
async def test_notifier_disabled_no_send():
    n = _mk_notifier()
    n.is_enabled = False
    h = TelegramErrorHandler(n)
    h.emit(_mk_record("test", "err"))
    await asyncio.sleep(0.01)
    n.send.assert_not_called()


@pytest.mark.asyncio
async def test_handler_never_raises():
    """Handler swallows its own exceptions — must never propagate."""
    n = _mk_notifier()
    n.is_enabled = True
    n.send = MagicMock(side_effect=Exception("boom"))  # non-async breaks create_task
    h = TelegramErrorHandler(n)
    # Should not raise
    h.emit(_mk_record("test", "err"))
    await asyncio.sleep(0.01)
