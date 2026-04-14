"""Pre-close CLV snapshot infrastructure.

True CLV requires capturing the market state just before the "closing
line" — i.e., just before betting ends for the event. For Kalshi sports
markets, that's ~game start time, which is encoded directly in the
ticker (e.g. `KXMLBGAME-26APR142210NYMLAD` → Apr 14 22:10 ET).

Runs as a background task in main.py every 60s, finds unsettled paper
sports trades whose game starts within the next few minutes, and
records a 'pre_close' CLV snapshot so the CLV+ rate reflects real
closing-line value rather than the degenerate win/loss of settled
markets.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("clv_tracker")

# Kalshi sports ticker: KX(NBA|MLB|NHL)GAME-<YY><MMM><DD><HHMM><TEAMS>-<TEAM>
_TICKER_RE = re.compile(r"^KX(?:NBA|MLB|NHL)GAME-(\d{2})([A-Z]{3})(\d{2})(\d{4})")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_ET = ZoneInfo("America/New_York")  # zoneinfo handles EDT/EST transitions


def _now_utc() -> datetime:
    """Overridable for tests."""
    return datetime.now(timezone.utc)


def parse_game_start_utc(ticker: str) -> Optional[datetime]:
    """Extract game start time from ticker (ET) and return as UTC-aware datetime.
    Returns None for non-sports tickers or malformed formats."""
    m = _TICKER_RE.match(ticker or "")
    if not m:
        return None
    yy, mmm, dd, hhmm = m.groups()
    if mmm not in _MONTHS:
        return None
    try:
        year = 2000 + int(yy)
        month = _MONTHS[mmm]
        day = int(dd)
        hour = int(hhmm[:2])
        minute = int(hhmm[2:])
        local_dt = datetime(year, month, day, hour, minute, tzinfo=_ET)
    except (ValueError, KeyError):
        return None
    return local_dt.astimezone(timezone.utc)


class CLVTracker:
    """Captures pre_close snapshots T-N minutes before each unsettled trade's
    game start. Runs on a timer from main.py."""

    # When to snapshot: game start - N minutes. Wide enough to not miss
    # the window between task ticks, narrow enough to be close to "closing line".
    PRE_CLOSE_WINDOW_MIN_AHEAD = 5.0   # start capturing 5 min before game
    PRE_CLOSE_WINDOW_MIN_BEHIND = 1.0  # still acceptable 1 min after (edge cases)

    def __init__(self, kalshi_client, database):
        self._client = kalshi_client
        self._db = database

    async def capture_pre_close_snapshots(self) -> int:
        """Find paper_filled sports trades whose game starts in the window,
        lacking a pre_close snapshot, and record one. Returns number captured."""
        import aiosqlite
        async with aiosqlite.connect(self._db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("""
                SELECT t.id, t.market_id
                FROM trades t
                LEFT JOIN clv_snapshots c
                    ON c.trade_id = t.id AND c.snapshot_at = 'pre_close'
                WHERE t.status = 'paper_filled'
                  AND t.pnl = 0
                  AND c.id IS NULL
                  AND t.strategy LIKE 'sports_%'
            """)
            candidates = await cursor.fetchall()

        now = _now_utc()
        loop = asyncio.get_event_loop()
        captured = 0
        for row in candidates:
            ticker = row["market_id"]
            start = parse_game_start_utc(ticker)
            if start is None:
                continue
            minutes_until = (start - now).total_seconds() / 60.0
            if minutes_until > self.PRE_CLOSE_WINDOW_MIN_AHEAD:
                continue
            if minutes_until < -self.PRE_CLOSE_WINDOW_MIN_BEHIND:
                continue
            try:
                market = await loop.run_in_executor(None, self._client.get_market, ticker)
                if not market:
                    continue
                yes_ask = _safe_float(market.get("yes_ask_dollars"))
                no_ask = _safe_float(market.get("no_ask_dollars"))
                if yes_ask is None and no_ask is None:
                    continue
                await self._db.record_clv_snapshot(
                    trade_id=row["id"], snapshot_at="pre_close",
                    yes_ask=yes_ask, no_ask=no_ask, pinnacle_prob=None,
                )
                captured += 1
            except Exception as e:
                logger.debug(f"pre_close snapshot failed for {ticker}: {e}")
        if captured > 0:
            logger.info(f"[CLV] Captured {captured} pre-close snapshots")
        return captured


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
