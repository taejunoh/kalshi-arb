"""Ship ERROR+ log records to Telegram with per-signature exponential backoff."""
import asyncio
import logging
import time

# Exponential backoff schedule: seconds between alerts for same signature
# Index = occurrence number (0-indexed). Beyond index 4, use last value.
BACKOFF_SCHEDULE = [0, 60, 300, 1800, 7200]  # 0s, 1min, 5min, 30min, 2hr

# Loggers whose errors are excluded (prevent feedback loops)
EXCLUDED_LOGGERS = {"notifier", "telegram_error_handler"}


class TelegramErrorHandler(logging.Handler):
    """Logging handler that dispatches ERROR+ records to Telegram.

    Uses exponential backoff keyed on (logger_name, message_prefix) so each
    distinct error stream has its own alert cadence. Attach to root logger:

        handler = TelegramErrorHandler(notifier)
        handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(handler)
    """

    def __init__(self, notifier, min_level: int = logging.ERROR):
        super().__init__(level=min_level)
        self._notifier = notifier
        # signature -> (last_sent_ts, occurrence_count)
        self._state: dict[str, tuple[float, int]] = {}

    def _signature(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()[:100]
        return f"{record.name}|{msg}"

    def _next_delay(self, count: int) -> int:
        idx = min(count, len(BACKOFF_SCHEDULE) - 1)
        return BACKOFF_SCHEDULE[idx]

    def _should_send(self, signature: str, now: float) -> tuple[bool, int]:
        entry = self._state.get(signature)
        if entry is None:
            # First occurrence — always send
            return True, 0
        last_sent, count = entry
        delay = self._next_delay(count)
        if now - last_sent >= delay:
            return True, count
        return False, count

    def emit(self, record: logging.LogRecord):
        if record.name in EXCLUDED_LOGGERS:
            return
        try:
            if self._notifier is None or not getattr(self._notifier, "is_enabled", False):
                return
            signature = self._signature(record)
            now = time.time()
            send_now, prev_count = self._should_send(signature, now)
            if not send_now:
                return
            msg = record.getMessage()[:400]
            text = f"⚠️ <b>ERROR</b> in <code>{record.name}</code>\n{_html_escape(msg)}"
            # notifier.send is async — schedule on running event loop if present
            self._dispatch_async(text, msg_type=f"err_{hash(signature) & 0xFFFF:04x}")
            self._state[signature] = (now, prev_count + 1)
        except Exception:
            # Never raise from a logging handler
            pass

    def _dispatch_async(self, text: str, msg_type: str):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — can't ship. Drop silently (avoid startup-time issues).
            return
        loop.create_task(self._notifier.send(text, msg_type=msg_type))


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
