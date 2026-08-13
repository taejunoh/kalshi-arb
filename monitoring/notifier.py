import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("notifier")

RATE_LIMIT_SECONDS = 60.0

# Discrete events — each occurrence is a distinct piece of info the user needs
# to see. Rate-limiting these hides fills/settlements during active minutes,
# which is the opposite of what we want. Repeatable/spammy types (errors,
# status, summary) stay rate-limited to avoid flooding.
UNTHROTTLED_MSG_TYPES = frozenset({"trade", "settlement", "balance_reject"})

# 2026-05-25 — Quiet hours: 02:00-09:00 UTC = 22:00-05:00 EDT (user is in NYC
# and sleeps in this window; confirmed in feedback_user_in_nyc_not_korea).
# Non-CRITICAL messages are suppressed in this window; CRITICAL types break
# through (balance/cap/dispatch failures).
QUIET_HOURS_UTC_START = 2
QUIET_HOURS_UTC_END   = 9
CRITICAL_MSG_TYPES = frozenset({
    "cap_trip", "balance_low", "balance_reject", "dispatch_failure",
    "circuit_breaker", "live_audit",  # daily audit is at 13 UTC anyway
    # 2026-07-28 — live_fade post-fix gate. Fires only on a verdict change, and
    # its EARLY-KILL verdict means a LIVE strategy is provably past rescue.
    # Suppressing that for the 7-hour quiet window would keep real money on a
    # position we already know cannot reach breakeven. The oneshot timer also
    # advances its state file after send(), so a suppressed message would be
    # lost outright rather than retried.
    "livefade_gate",
    # 2026-08-13 — user asked for fills and settlements overnight. Being in
    # UNTHROTTLED_MSG_TYPES was not enough: _should_send() checks quiet hours
    # BEFORE the unthrottled exemption, so they were silent 22:00-05:00 NYC.
    #
    # settle_digest is the one that matters and is easy to miss: nothing calls
    # notify_settlement(), so no message ever goes out as msg_type "settlement"
    # — core/settlement.py buffers into the hourly digest instead. Exempting
    # only "trade"/"settlement" would have left settlements silent overnight
    # while looking done. "settlement" is listed anyway so the dead path cannot
    # come back with the opposite behaviour.
    "trade", "settlement", "settle_digest",
})

# Hourly digest for LIVE settlements (2026-05-25). Buffered + flushed once/h
# via flush_settle_digest_loop() task — replaces per-settle notify_settlement.
SETTLE_DIGEST_INTERVAL_S = 3600


class TelegramNotifier:
    """Send trade alerts and status updates via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._last_sent: dict[str, float] = {}
        # LIVE settle digest buffer
        self._settle_buffer: list[dict] = []
        self._settle_buffer_lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return bool(self._bot_token) and bool(self._chat_id)

    def _is_quiet_hours(self) -> bool:
        h = datetime.now(timezone.utc).hour
        return QUIET_HOURS_UTC_START <= h < QUIET_HOURS_UTC_END

    def _should_send(self, msg_type: str) -> bool:
        # Quiet hours: skip everything except CRITICAL types.
        if self._is_quiet_hours() and msg_type not in CRITICAL_MSG_TYPES:
            return False
        if msg_type in UNTHROTTLED_MSG_TYPES:
            return True
        last = self._last_sent.get(msg_type)
        if last is None:
            return True
        return (time.time() - last) >= RATE_LIMIT_SECONDS

    async def send(self, text: str, msg_type: str = "general") -> bool:
        """True only if Telegram accepted the message.

        Suppression (quiet hours, rate limit) and transport failure (non-200,
        connection error) both return False, because to a caller that discards
        state once the message is "out" they are the same event.

        Any such caller must check this. flush_settle_digest() does. The other
        one, scripts/livefade_postfix_gate.py, advances its state file
        unconditionally and leans on CRITICAL_MSG_TYPES instead — which covers
        quiet hours but not a non-200 or a dropped connection.
        """
        if not self.is_enabled:
            return False
        if not self._should_send(msg_type):
            if self._is_quiet_hours() and msg_type not in CRITICAL_MSG_TYPES:
                logger.debug(f"Quiet hours — suppressed {msg_type}")
            else:
                logger.debug(f"Rate-limited: {msg_type}")
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    self._last_sent[msg_type] = time.time()
                    logger.debug(f"Telegram sent: {msg_type}")
                    return True
                logger.warning(f"Telegram API error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
        return False

    # ─────────────────────────────────────────────
    # LIVE settle digest — buffered, flushed hourly
    # ─────────────────────────────────────────────
    async def buffer_settlement(self, ticker: str, strategy: str, direction: str,
                                 price: float, pnl: float, bankroll: float):
        """Append a LIVE settle to the buffer. Flush happens hourly."""
        if not self.is_enabled:
            return
        async with self._settle_buffer_lock:
            self._settle_buffer.append({
                "ticker": ticker, "strategy": strategy, "direction": direction,
                "price": price, "pnl": pnl, "bankroll": bankroll,
                "ts": time.time(),
            })

    async def flush_settle_digest(self):
        """Build + send a digest of buffered LIVE settles. No-op if empty.

        The buffer is only allowed to drain once Telegram has accepted the
        message. It used to clear first and send afterwards, so anything that
        stopped the send threw real settlement data away instead of deferring
        it — quiet hours above all (every LIVE settle resolving 22:00-05:00 NYC,
        up to 7 hourly digests a night), but a non-200 or a dropped connection
        lost them the same way. A suppressed digest now rolls into the next
        flush, so a night's settles arrive as one message once the window ends.
        """
        async with self._settle_buffer_lock:
            if not self._settle_buffer:
                return
            settles = self._settle_buffer[:]
            self._settle_buffer.clear()
        # Aggregate by strategy
        by_strat: dict[str, dict] = {}
        for s in settles:
            d = by_strat.setdefault(s["strategy"], {"n": 0, "wins": 0, "pnl": 0.0})
            d["n"] += 1
            d["pnl"] += s["pnl"]
            if s["pnl"] > 0:
                d["wins"] += 1
        total_n = sum(d["n"] for d in by_strat.values())
        total_pnl = sum(d["pnl"] for d in by_strat.values())
        total_wins = sum(d["wins"] for d in by_strat.values())
        bankroll = settles[-1]["bankroll"] if settles else 0
        emoji = "📈" if total_pnl > 0 else ("📉" if total_pnl < 0 else "➖")
        # a carried digest can span the whole quiet window, so say what it covers
        # rather than the one interval it used to be capped at by data loss
        span_h = round((time.time() - min(s["ts"] for s in settles)) / 3600)
        covers = "last hour" if span_h <= 1 else f"last {span_h}h"
        lines = [
            f"{emoji} <b>LIVE settle digest — {covers}</b>",
            f"Net: <b>${total_pnl:+.2f}</b> on {total_n} settles "
            f"({total_wins}W / {total_n - total_wins}L)",
            "",
        ]
        for strat, d in sorted(by_strat.items(), key=lambda kv: -kv[1]["pnl"]):
            wl = f"{d['wins']}W/{d['n'] - d['wins']}L"
            lines.append(f"  {strat}: ${d['pnl']:+.2f} ({wl})")
        lines.append(f"\nBankroll: ${bankroll:.2f}")
        msg = "\n".join(lines)
        if not await self.send(msg, msg_type="settle_digest"):
            # undelivered — put them back ahead of anything buffered while we
            # were awaiting, so the next flush carries them in arrival order
            async with self._settle_buffer_lock:
                self._settle_buffer[:0] = settles

    async def settle_digest_loop(self):
        """Background task — flushes settle digest every SETTLE_DIGEST_INTERVAL_S."""
        while True:
            await asyncio.sleep(SETTLE_DIGEST_INTERVAL_S)
            try:
                await self.flush_settle_digest()
            except Exception as e:
                logger.error(f"settle_digest_loop flush failed: {e}")

    def format_trade(self, strategy, market_id, direction, price, size, expected_profit) -> str:
        return (
            f"<b>Trade Executed</b>\n"
            f"Strategy: {strategy}\n"
            f"Market: {market_id}\n"
            f"Direction: {direction}\n"
            f"Price: {price:.3f}\n"
            f"Size: ${size:.2f}\n"
            f"Expected Profit: ${expected_profit:.2f}"
        )

    def format_error(self, error_msg: str) -> str:
        return f"<b>Error</b>\n{error_msg}"

    def format_bot_status(self, status: str, bankroll: float) -> str:
        return f"<b>Bot {status}</b>\nBankroll: ${bankroll:.2f}"

    async def notify_trade(self, strategy, market_id, direction, price, size, expected_profit):
        msg = self.format_trade(strategy, market_id, direction, price, size, expected_profit)
        await self.send(msg, msg_type="trade")

    async def notify_error(self, error_msg: str):
        msg = self.format_error(error_msg)
        await self.send(msg, msg_type="error")

    async def notify_bot_status(self, status: str, bankroll: float):
        msg = self.format_bot_status(status, bankroll)
        await self.send(msg, msg_type="bot_status")

    async def notify_settlement(self, ticker: str, strategy: str, direction: str,
                                 price: float, pnl: float, bankroll: float):
        result = "WIN" if pnl > 0 else "LOSS"
        emoji = "✅" if pnl > 0 else "❌"
        msg = (
            f"{emoji} <b>Settlement: {result}</b>\n"
            f"Strategy: {strategy}\n"
            f"Market: {ticker}\n"
            f"Direction: {direction} @ {price:.3f}\n"
            f"PnL: <b>${pnl:+.2f}</b>\n"
            f"Bankroll: ${bankroll:.2f}"
        )
        await self.send(msg, msg_type="settlement")
