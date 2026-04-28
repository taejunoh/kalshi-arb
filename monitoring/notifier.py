import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("notifier")

RATE_LIMIT_SECONDS = 60.0

# Discrete events — each occurrence is a distinct piece of info the user needs
# to see. Rate-limiting these hides fills/settlements during active minutes,
# which is the opposite of what we want. Repeatable/spammy types (errors,
# status, summary) stay rate-limited to avoid flooding.
UNTHROTTLED_MSG_TYPES = frozenset({"trade", "settlement", "balance_reject"})


class TelegramNotifier:
    """Send trade alerts and status updates via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._last_sent: dict[str, float] = {}

    @property
    def is_enabled(self) -> bool:
        return bool(self._bot_token) and bool(self._chat_id)

    def _should_send(self, msg_type: str) -> bool:
        if msg_type in UNTHROTTLED_MSG_TYPES:
            return True
        last = self._last_sent.get(msg_type)
        if last is None:
            return True
        return (time.time() - last) >= RATE_LIMIT_SECONDS

    async def send(self, text: str, msg_type: str = "general"):
        if not self.is_enabled:
            return
        if not self._should_send(msg_type):
            logger.debug(f"Rate-limited: {msg_type}")
            return

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    self._last_sent[msg_type] = time.time()
                    logger.debug(f"Telegram sent: {msg_type}")
                else:
                    logger.warning(f"Telegram API error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

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
