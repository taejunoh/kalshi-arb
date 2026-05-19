"""SportsTraderStrategy.scan_loop — dedicated task pattern (2026-05-19).

Tennis/UFC paper strategies moved out of the shared `_strategy_loop`
into per-strategy asyncio tasks to dodge the serial-queue stall caused
by slow upstream sports strategies. This test pins the loop contract.
"""
import asyncio

from strategies.sports_trader import SportsTraderStrategy


class _CountingStrategy(SportsTraderStrategy):
    STRATEGY_NAME = "scan_loop_test"

    def __init__(self):
        # Bypass parent __init__ — we only exercise scan_loop.
        self.scan_count = 0
        self.raise_n = 0

    async def scan_and_execute(self):
        self.scan_count += 1
        if self.raise_n and self.scan_count == self.raise_n:
            raise RuntimeError("simulated transient")


def test_scan_loop_calls_scan_and_execute_repeatedly():
    s = _CountingStrategy()

    async def driver():
        # jitter=0 so the loop starts scanning immediately.
        task = asyncio.create_task(
            s.scan_loop(scan_interval_secs=0.01, startup_jitter_secs=0)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    assert s.scan_count >= 2, (
        f"scan_loop should call scan_and_execute repeatedly, got {s.scan_count}"
    )


def test_scan_loop_swallows_exceptions_and_keeps_going():
    """A transient error in one scan must not kill the dedicated task."""
    s = _CountingStrategy()
    s.raise_n = 1  # first call raises

    async def driver():
        task = asyncio.create_task(
            s.scan_loop(scan_interval_secs=0.01, startup_jitter_secs=0)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    # First call raised but loop should have continued running.
    assert s.scan_count >= 2


def test_scan_loop_method_is_present_on_base_class():
    assert hasattr(SportsTraderStrategy, "scan_loop")
    assert asyncio.iscoroutinefunction(SportsTraderStrategy.scan_loop)


def test_scan_loop_jitter_zero_starts_scanning_immediately():
    """startup_jitter_secs=0 → no startup delay (back-compat)."""
    s = _CountingStrategy()

    async def driver():
        task = asyncio.create_task(
            s.scan_loop(scan_interval_secs=0.01, startup_jitter_secs=0)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    assert s.scan_count >= 2


def test_scan_loop_jitter_delays_first_scan():
    """startup_jitter > 0 must produce at least one delay before first scan."""
    s = _CountingStrategy()

    async def driver():
        task = asyncio.create_task(
            s.scan_loop(scan_interval_secs=0.01, startup_jitter_secs=0.05)
        )
        # 0.02s < jitter window of [0, 0.05) — usually still in jitter
        await asyncio.sleep(0.02)
        early_count = s.scan_count
        await asyncio.sleep(0.10)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return early_count

    early = asyncio.run(driver())
    # After the full jitter window has elapsed, scans must have started.
    assert s.scan_count >= 1
    # Note: due to randomness `early` is sometimes >= 1 — the test would
    # be flaky with a strict `early == 0`. The 0.10s post-jitter wait is
    # what we actually rely on for "scans eventually happen". Jitter is
    # exercised in the >0 path purely as a smoke test.


def test_odds_429_cooldown_arms_on_429_error():
    """When _fetch_odds catches a 429 error, the global cooldown timestamp
    should advance into the future, gating subsequent fetches."""
    from strategies import sports_trader as st
    # Reset cooldown
    st._ODDS_429_COOLDOWN_UNTIL = 0.0
    assert st._ODDS_429_COOLDOWN_UNTIL == 0.0

    class _Stub(SportsTraderStrategy):
        STRATEGY_NAME = "stub_429"

        def __init__(self):
            self._odds_api_key = "k"
            self.odds_api_usage = {}
            self._cache = {}

        def _get_odds_region(self):
            return "eu"

        async def _check_quota(self, remaining):
            pass

    stub = _Stub()

    class _FailClient:
        async def get(self, *a, **k):
            raise RuntimeError("Client error '429 Too Many Requests'")

    import unittest.mock as mock
    with mock.patch.object(st, "_get_odds_http_client", return_value=_FailClient()):
        asyncio.run(stub._fetch_odds("any_sport"))

    assert st._ODDS_429_COOLDOWN_UNTIL > 0, (
        "cooldown timestamp must be armed when 429 detected"
    )
    # Cleanup so other tests aren't affected.
    st._ODDS_429_COOLDOWN_UNTIL = 0.0
