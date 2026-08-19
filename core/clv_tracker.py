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
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("clv_tracker")

# Kalshi sports ticker carrying a start TIME:
#   KX<SPORT>GAME-<YY><MMM><DD><HHMM><TEAMS>-<TEAM>
# Matches any sport rather than an NBA/MLB/NHL allow-list, so a newly traded
# league is not silently dropped. The trailing `(\d{4})` is what makes this
# time-bearing: date-only tickers (ATP/WTA/WNBA/UFC) fail it by construction
# and fall through to the minutes_to_start estimate.
_TICKER_RE = re.compile(r"^KX[A-Z]+(?:GAME|MATCH|FIGHT)-(\d{2})([A-Z]{3})(\d{2})(\d{4})")
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


def _parse_trade_ts(ts: str) -> Optional[datetime]:
    """Parse a `trades.timestamp` string into a UTC-aware datetime.

    Rows are written with both 'T' and ' ' separators over the table's
    history, so normalise before parsing rather than assuming one form.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace(" ", "T"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def resolve_event_start_utc(ticker: str, trade_ts: str,
                            minutes_to_start) -> tuple[Optional[datetime], bool]:
    """Best available event start, as (start_utc, is_exact).

    Prefers the ticker (exact). Falls back to entry time + minutes_to_start
    for the sports whose tickers carry only a date — see module docstring.
    Returns (None, False) when neither source is usable.
    """
    exact = parse_game_start_utc(ticker)
    if exact is not None:
        return exact, True
    if minutes_to_start is None:
        return None, False
    entered = _parse_trade_ts(trade_ts)
    if entered is None:
        return None, False
    try:
        offset = float(minutes_to_start)
    except (TypeError, ValueError):
        return None, False
    return entered + timedelta(minutes=offset), False


class CLVTracker:
    """Captures pre_close snapshots T-N minutes before each unsettled trade's
    game start. Runs on a timer from main.py."""

    # When to snapshot: game start - N minutes. Wide enough to not miss
    # the window if a tick is delayed (e.g. slate-wide HTTP latency), narrow
    # enough to still be close to the actual "closing line" moment.
    PRE_CLOSE_WINDOW_MIN_AHEAD = 5.0   # start capturing 5 min before game
    PRE_CLOSE_WINDOW_MIN_BEHIND = 3.0  # absorb up to 3 min of tick lag

    # Wider window for starts estimated from minutes_to_start. Tennis is the
    # motivating case: the feed's scheduled time is a "not before" that moves
    # when the previous match on court runs long, so a +/-5 min window would
    # miss most of the slate. Trading coverage we do not have for precision
    # we cannot verify — an approximate pre-close price is still a real market
    # price, whereas the `closing` fallback is a constant 1.0.
    EST_WINDOW_MIN_AHEAD = 8.0
    EST_WINDOW_MIN_BEHIND = 10.0

    def __init__(self, kalshi_client, database):
        self._client = kalshi_client
        self._db = database

    async def capture_pre_close_snapshots(self) -> int:
        """Find paper_filled sports trades whose game starts in the window,
        lacking a pre_close snapshot, and record one. Returns number captured."""
        import aiosqlite
        async with aiosqlite.connect(self._db.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("""
                SELECT t.id, t.market_id, t.timestamp, t.minutes_to_start
                FROM trades t
                LEFT JOIN clv_snapshots c
                    ON c.trade_id = t.id AND c.snapshot_at = 'pre_close'
                WHERE t.status IN ('paper_filled', 'filled')
                  AND t.pnl = 0
                  AND c.id IS NULL
                  AND t.strategy LIKE 'sports_%'
            """)
            candidates = await cursor.fetchall()

        now = _now_utc()
        loop = asyncio.get_event_loop()
        captured = 0
        captured_est = 0
        for row in candidates:
            ticker = row["market_id"]
            start, is_exact = resolve_event_start_utc(
                ticker, row["timestamp"], row["minutes_to_start"])
            if start is None:
                continue
            if is_exact:
                ahead = self.PRE_CLOSE_WINDOW_MIN_AHEAD
                behind = self.PRE_CLOSE_WINDOW_MIN_BEHIND
            else:
                ahead = self.EST_WINDOW_MIN_AHEAD
                behind = self.EST_WINDOW_MIN_BEHIND
            minutes_until = (start - now).total_seconds() / 60.0
            if minutes_until > ahead:
                continue
            if minutes_until < -behind:
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
                if not is_exact:
                    captured_est += 1
            except Exception as e:
                logger.warning(f"pre_close snapshot failed for {ticker}: {e}")
        if captured > 0:
            logger.info(
                f"[CLV] Captured {captured} pre-close snapshots "
                f"({captured - captured_est} from ticker time, "
                f"{captured_est} estimated from minutes_to_start)"
            )
        return captured


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
