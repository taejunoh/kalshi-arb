"""Telegram notifier gating.

_should_send() reads the wall clock — it suppresses non-CRITICAL types during
quiet hours (02:00-09:00 UTC = 22:00-05:00 NYC). Any test that leaves the clock
alone therefore passes or fails depending on what time the suite is run at.

That is what happened here: these tests were written 2026-04-16 (d8e41c4, the
60s-rate-limit exemption), and 2026-05-25 (d6a7388) added the quiet-hours gate
IN FRONT of the exemption check. Two of them have failed for seven hours a day
ever since, and read green the other seventeen. So every test below pins the
hour, and a non-vacuity guard asserts the pinned hours are still on the side of
the window the test thinks they are on.
"""
import asyncio
import contextlib
import datetime as _dt
import time
from unittest import mock

from monitoring.notifier import (
    CRITICAL_MSG_TYPES,
    QUIET_HOURS_UTC_END,
    QUIET_HOURS_UTC_START,
    UNTHROTTLED_MSG_TYPES,
    TelegramNotifier,
)


@contextlib.contextmanager
def at_utc_hour(hour):
    """Pin the notifier's clock to a UTC hour for the duration of the block."""
    class _Fixed(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 8, 13, hour, 0, 0, tzinfo=tz)

    with mock.patch("monitoring.notifier.datetime", _Fixed):
        yield


@contextlib.contextmanager
def telegram(status_code=200, raises=None):
    """Stub the Telegram HTTP call. Yields the list of payloads actually POSTed."""
    posted = []

    class _Resp:
        status_code = None
        text = ""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, timeout=None):
            if raises is not None:
                raise raises
            posted.append(json)
            resp = _Resp()
            resp.status_code = status_code
            return resp

    with mock.patch("monitoring.notifier.httpx.AsyncClient", _Client):
        yield posted


QUIET_HOUR = QUIET_HOURS_UTC_START
AWAKE_HOUR = next(h for h in range(24)
                  if not (QUIET_HOURS_UTC_START <= h < QUIET_HOURS_UTC_END))


def _notifier():
    return TelegramNotifier(bot_token="fake", chat_id="fake")


def test_the_pinned_hours_are_on_the_side_of_the_window_they_claim():
    """Non-vacuity guard: if the quiet window ever moves, the tests below must
    fail loudly rather than quietly stop testing what they name."""
    with at_utc_hour(AWAKE_HOUR):
        assert _notifier()._is_quiet_hours() is False
    with at_utc_hour(QUIET_HOUR):
        assert _notifier()._is_quiet_hours() is True


# ── rate limiting, outside quiet hours ────────────────────────────────

def test_rate_limiting_for_throttled_types():
    with at_utc_hour(AWAKE_HOUR):
        notifier = _notifier()
        assert notifier._should_send("error") is True
        notifier._last_sent["error"] = time.time()
        assert notifier._should_send("error") is False
        # Different throttled type still gets through.
        assert notifier._should_send("bot_status") is True


def test_trade_and_settlement_are_unthrottled():
    # Each fill/settle is a distinct event the user needs to see — sharing a
    # 60s window silently dropped back-to-back live fills.
    with at_utc_hour(AWAKE_HOUR):
        notifier = _notifier()
        notifier._last_sent["trade"] = time.time()
        notifier._last_sent["settlement"] = time.time()
        assert notifier._should_send("trade") is True
        assert notifier._should_send("settlement") is True


def test_throttle_state_is_per_instance():
    """A fresh notifier starts with an empty window — the throttle is instance
    state, not a class-level or module-level dict."""
    first = _notifier()
    first._last_sent["error"] = time.time()
    with at_utc_hour(AWAKE_HOUR):
        assert _notifier()._last_sent == {}
        assert _notifier()._should_send("error") is True


# ── quiet hours, and how it ranks against the exemption ───────────────

def test_quiet_hours_suppress_throttled_types():
    with at_utc_hour(QUIET_HOUR):
        notifier = _notifier()
        assert notifier._should_send("error") is False
        assert notifier._should_send("bot_status") is False


def test_fills_and_settlements_break_through_quiet_hours():
    """User asked for these overnight (2026-08-13). The quiet-hours check runs
    BEFORE the unthrottled check, so being exempt from the 60s window was not
    enough on its own — they have to be CRITICAL as well."""
    with at_utc_hour(QUIET_HOUR):
        notifier = _notifier()
        assert notifier._should_send("trade") is True
        assert notifier._should_send("settle_digest") is True
        assert notifier._should_send("settlement") is True


def test_the_live_settlement_path_is_the_one_that_breaks_through():
    """Settlements do NOT reach Telegram as msg_type 'settlement' — nothing
    calls notify_settlement(); core/settlement.py buffers into the hourly
    digest instead. Exempting only 'settlement' would have been a no-op, so
    this pins the type that actually carries settlements."""
    assert "settle_digest" in CRITICAL_MSG_TYPES


def test_critical_types_break_through_quiet_hours():
    with at_utc_hour(QUIET_HOUR):
        notifier = _notifier()
        for msg_type in CRITICAL_MSG_TYPES:
            assert notifier._should_send(msg_type) is True, msg_type


# ── the settle digest must never be drained without being delivered ───
#
# flush_settle_digest() cleared the buffer BEFORE awaiting send(), so anything
# that stopped the send threw the settles away instead of deferring them. Quiet
# hours used to be the loud case; since settle_digest became CRITICAL that door
# is shut, but a non-200 or a network blip still loses them the same way. The
# buffer is only allowed to drain once the message is actually out.

def _settle(notifier, ticker, pnl, bankroll):
    asyncio.run(notifier.buffer_settlement(
        ticker=ticker, strategy="mlb_pitcher", direction="YES",
        price=0.5, pnl=pnl, bankroll=bankroll))


def test_the_digest_goes_out_during_quiet_hours():
    """Overnight settles reach the user the same night, not at 09:00 UTC."""
    notifier = _notifier()
    _settle(notifier, "T1", 12.0, 1000.0)
    with at_utc_hour(QUIET_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
    assert len(posted) == 1
    assert notifier._settle_buffer == []


def test_a_rejected_send_keeps_the_digest():
    notifier = _notifier()
    _settle(notifier, "T1", 12.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram(status_code=500):
        asyncio.run(notifier.flush_settle_digest())
    assert len(notifier._settle_buffer) == 1


def test_a_network_failure_keeps_the_digest():
    notifier = _notifier()
    _settle(notifier, "T1", 12.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram(raises=OSError("connection reset")):
        asyncio.run(notifier.flush_settle_digest())
    assert len(notifier._settle_buffer) == 1


def test_the_kept_digest_goes_out_on_the_next_flush():
    notifier = _notifier()
    _settle(notifier, "T1", 12.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram(raises=OSError("down")):
        asyncio.run(notifier.flush_settle_digest())
    with at_utc_hour(AWAKE_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
    assert len(posted) == 1
    assert "mlb_pitcher" in posted[0]["text"]
    assert notifier._settle_buffer == []         # delivered, so now it may drain


def test_settles_from_across_an_outage_merge_into_one_digest():
    """Seven failed hourly flushes must not become seven lost digests, nor seven
    separate messages once Telegram comes back — one combined digest carries
    them all."""
    notifier = _notifier()
    for i in range(7):
        _settle(notifier, f"T{i}", 1.0, 1000.0 + i)
        with at_utc_hour(AWAKE_HOUR), telegram(raises=OSError("down")):
            asyncio.run(notifier.flush_settle_digest())
    assert len(notifier._settle_buffer) == 7

    with at_utc_hour(AWAKE_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
    assert len(posted) == 1
    assert "7 settles" in posted[0]["text"]
    assert notifier._settle_buffer == []


def test_settles_arriving_during_a_failed_flush_are_not_reordered_or_lost():
    notifier = _notifier()
    _settle(notifier, "OLD", 1.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram(raises=OSError("down")):
        asyncio.run(notifier.flush_settle_digest())
    _settle(notifier, "NEW", 2.0, 1002.0)
    assert [s["ticker"] for s in notifier._settle_buffer] == ["OLD", "NEW"]


def test_a_delivered_digest_drains_the_buffer_exactly_once():
    notifier = _notifier()
    _settle(notifier, "T1", 12.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
        asyncio.run(notifier.flush_settle_digest())   # nothing left to resend
    assert len(posted) == 1
    assert notifier._settle_buffer == []


def test_a_carried_digest_does_not_still_claim_to_cover_one_hour():
    """"last hour" used to be true by construction — a digest never covered more
    than one flush interval, because anything longer had been thrown away. Now
    that suppressed digests accumulate, the header has to say what it covers."""
    notifier = _notifier()
    _settle(notifier, "OLD", 1.0, 1000.0)
    _settle(notifier, "NEW", 2.0, 1002.0)
    notifier._settle_buffer[0]["ts"] -= 6 * 3600          # carried since 6h ago
    with at_utc_hour(AWAKE_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
    assert "last hour" not in posted[0]["text"]
    assert "6h" in posted[0]["text"]


def test_a_single_interval_digest_still_reads_as_last_hour():
    notifier = _notifier()
    _settle(notifier, "T1", 1.0, 1000.0)
    with at_utc_hour(AWAKE_HOUR), telegram() as posted:
        asyncio.run(notifier.flush_settle_digest())
    assert "last hour" in posted[0]["text"]


def test_send_reports_whether_it_delivered():
    notifier = _notifier()
    with at_utc_hour(AWAKE_HOUR), telegram():
        assert asyncio.run(notifier.send("hi", msg_type="settle_digest")) is True
    with at_utc_hour(QUIET_HOUR), telegram():
        assert asyncio.run(notifier.send("hi", msg_type="error")) is False  # suppressed
    with at_utc_hour(AWAKE_HOUR), telegram(status_code=500):
        assert asyncio.run(notifier.send("hi", msg_type="settle_digest")) is False
    with at_utc_hour(AWAKE_HOUR), telegram(raises=OSError("down")):
        assert asyncio.run(notifier.send("hi", msg_type="settle_digest")) is False
    assert asyncio.run(TelegramNotifier("", "").send("hi")) is False


# ── formatting / enablement ───────────────────────────────────────────

def test_format_trade_message():
    notifier = _notifier()
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
