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
        task = asyncio.create_task(s.scan_loop(scan_interval_secs=0.01))
        # let it iterate a few times
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
        task = asyncio.create_task(s.scan_loop(scan_interval_secs=0.01))
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
