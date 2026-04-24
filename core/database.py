import aiosqlite
from datetime import datetime, timezone
from typing import Optional

from core.season import is_playoff


class Database:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    pnl REAL DEFAULT 0,
                    status TEXT NOT NULL,
                    minutes_to_start REAL
                )
            """)
            await self._add_column_if_missing(conn, "trades", "minutes_to_start", "REAL")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    order_id TEXT NOT NULL UNIQUE,
                    market_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS clv_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    snapshot_at TEXT NOT NULL,
                    snapshot_time TEXT NOT NULL,
                    yes_ask REAL,
                    no_ask REAL,
                    pinnacle_prob REAL,
                    UNIQUE(trade_id, snapshot_at)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value REAL,
                    updated_at TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ticker_settlements (
                    kalshi_ticker TEXT PRIMARY KEY,
                    result TEXT NOT NULL,
                    close_yes_ask REAL,
                    close_no_ask REAL,
                    settled_at TEXT,
                    fetched_at TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS odds_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    kalshi_ticker TEXT NOT NULL,
                    series TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    minutes_to_start REAL,
                    home_team TEXT,
                    away_team TEXT,
                    yes_sub_title TEXT,
                    is_yes_home INTEGER,
                    kalshi_yes_ask REAL,
                    kalshi_no_ask REAL,
                    us_yes_prob REAL,
                    eu_yes_prob REAL,
                    pinnacle_yes_prob REAL,
                    pinnacle_age_s INTEGER,
                    is_playoff INTEGER
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status_pnl ON trades(status, pnl)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_clv_trade ON clv_snapshots(trade_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_time ON odds_observations(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_ticker_time ON odds_observations(kalshi_ticker, timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_sport_phase ON odds_observations(sport, phase)")
            await self._add_column_if_missing(conn, "clv_snapshots", "us_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "trades", "realized_fill_price", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "eu_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "pinnacle_age_s", "INTEGER")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_bid", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_ask", "REAL")
            # Season classification (playoff vs regular). Computed on insert
            # going forward; backfill below populates historical NULL rows.
            await self._add_column_if_missing(conn, "trades", "is_playoff", "INTEGER")
            await self._backfill_is_playoff(conn)
            await conn.commit()

    async def close(self):
        pass

    @staticmethod
    async def _add_column_if_missing(conn, table: str, column: str, col_type: str):
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        if column not in existing:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    @staticmethod
    async def _backfill_is_playoff(conn):
        """One-shot backfill of is_playoff for trades that pre-date the
        column. Idempotent — only updates rows where the value is NULL."""
        cursor = await conn.execute(
            "SELECT id, market_id, timestamp FROM trades WHERE is_playoff IS NULL"
        )
        rows = await cursor.fetchall()
        if not rows:
            return
        for trade_id, market_id, ts in rows:
            flag = is_playoff(market_id, ts)
            # Store None as NULL (unknown sport / parse failure), True/False as 1/0.
            value = None if flag is None else (1 if flag else 0)
            await conn.execute(
                "UPDATE trades SET is_playoff = ? WHERE id = ?", (value, trade_id),
            )

    async def record_trade(self, strategy, market_id, direction, price, size, fee, status,
                           pnl=0.0, minutes_to_start: Optional[float] = None,
                           realized_fill_price: Optional[float] = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        playoff = is_playoff(market_id, now)
        playoff_int = None if playoff is None else (1 if playoff else 0)
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """INSERT INTO trades
                   (timestamp, strategy, market_id, direction, price, size, fee, pnl, status,
                    minutes_to_start, realized_fill_price, is_playoff)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, strategy, market_id, direction, price, size, fee, pnl, status,
                 minutes_to_start, realized_fill_price, playoff_int),
            )
            await conn.commit()
            return cursor.lastrowid

    async def record_order(self, order_id, market_id, strategy, side, price, size, order_type, status):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO orders (timestamp, order_id, market_id, strategy, side, price, size, order_type, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, order_id, market_id, strategy, side, price, size, order_type, status),
            )
            await conn.commit()

    async def update_order_status(self, order_id, status):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
            await conn.commit()

    async def get_trades(self, strategy=None):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            if strategy:
                cursor = await conn.execute("SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp DESC", (strategy,))
            else:
                cursor = await conn.execute("SELECT * FROM trades ORDER BY timestamp DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_open_orders(self, limit: int = 100):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM orders WHERE status IN ('open', 'paper_open') ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_paper_open_orders(self):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM orders WHERE status = 'paper_open' ORDER BY id"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_unsettled_trades(self):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM trades "
                "WHERE pnl = 0 AND status IN ('paper_filled', 'paper_open', 'filled')"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def settle_trade(self, trade_id: int, pnl: float, status: str = "paper_settled"):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE trades SET pnl = ?, status = ? WHERE id = ?", (pnl, status, trade_id)
            )
            await conn.commit()

    async def record_clv_snapshot(
        self,
        trade_id: int,
        snapshot_at: str,
        yes_ask: Optional[float],
        no_ask: Optional[float],
        pinnacle_prob: Optional[float] = None,
        us_consensus_prob: Optional[float] = None,
        eu_consensus_prob: Optional[float] = None,
        pinnacle_age_s: Optional[int] = None,
        kalshi_yes_bid: Optional[float] = None,
        kalshi_yes_ask: Optional[float] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO clv_snapshots
                   (trade_id, snapshot_at, snapshot_time, yes_ask, no_ask, pinnacle_prob,
                    us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                    kalshi_yes_bid, kalshi_yes_ask)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, snapshot_at, now, yes_ask, no_ask, pinnacle_prob,
                 us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                 kalshi_yes_bid, kalshi_yes_ask),
            )
            await conn.commit()

    async def get_clv_positive_rate(self, strategy: str, days: int = 30,
                                     mode: Optional[str] = None) -> float:
        """Fraction of trades where closing line moved favorably vs entry.

        YES direction: CLV+ when close_yes_ask > entry_yes_ask.
        NO direction: CLV+ when close_no_ask > entry_no_ask.

        Prefers 'pre_close' snapshot (captured minutes before game start —
        the true closing line) over 'closing' snapshot (captured at
        settlement, where yes_ask is 0 or 1 and CLV+ ≈ win rate).
        Only trades with both an entry AND some close-side snapshot count.

        `mode`: None → all trades. 'paper' → paper_* only. 'live' → filled/settled only.
        """
        status_clause = ""
        if mode == "paper":
            status_clause = " AND t.status LIKE 'paper%'"
        elif mode == "live":
            status_clause = " AND t.status IN ('filled', 'settled')"
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                f"""
                SELECT t.direction,
                       e.yes_ask AS entry_yes, e.no_ask AS entry_no,
                       COALESCE(pc.yes_ask, cl.yes_ask) AS close_yes,
                       COALESCE(pc.no_ask, cl.no_ask) AS close_no
                FROM trades t
                JOIN clv_snapshots e ON e.trade_id = t.id AND e.snapshot_at = 'entry'
                LEFT JOIN clv_snapshots pc ON pc.trade_id = t.id AND pc.snapshot_at = 'pre_close'
                LEFT JOIN clv_snapshots cl ON cl.trade_id = t.id AND cl.snapshot_at = 'closing'
                WHERE t.strategy = ?
                  AND t.timestamp >= datetime('now', ?)
                  AND (pc.id IS NOT NULL OR cl.id IS NOT NULL)
                  {status_clause}
                """,
                (strategy, f"-{int(days)} days"),
            )
            rows = await cursor.fetchall()
        if not rows:
            return 0.0
        pos = 0
        total = 0
        for r in rows:
            if (r["direction"] or "").lower() == "yes":
                entry, close = r["entry_yes"], r["close_yes"]
            else:
                entry, close = r["entry_no"], r["close_no"]
            if entry is None or close is None:
                continue
            total += 1
            if close > entry:
                pos += 1
        return pos / total if total > 0 else 0.0

    async def set_bot_state(self, key: str, value: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, now),
            )
            await conn.commit()

    async def get_bot_state(self, key: str) -> Optional[float]:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def record_odds_observation(
        self,
        kalshi_ticker: str,
        series: str,
        sport: str,
        phase: str,
        minutes_to_start: Optional[float],
        home_team: Optional[str],
        away_team: Optional[str],
        yes_sub_title: Optional[str],
        is_yes_home: Optional[bool],
        kalshi_yes_ask: Optional[float],
        kalshi_no_ask: Optional[float],
        us_yes_prob: Optional[float],
        eu_yes_prob: Optional[float],
        pinnacle_yes_prob: Optional[float],
        pinnacle_age_s: Optional[int],
    ):
        now = datetime.now(timezone.utc).isoformat()
        playoff = is_playoff(kalshi_ticker, now)
        playoff_int = None if playoff is None else (1 if playoff else 0)
        is_yes_home_int = None if is_yes_home is None else (1 if is_yes_home else 0)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO odds_observations
                   (timestamp, kalshi_ticker, series, sport, phase, minutes_to_start,
                    home_team, away_team, yes_sub_title, is_yes_home,
                    kalshi_yes_ask, kalshi_no_ask,
                    us_yes_prob, eu_yes_prob, pinnacle_yes_prob, pinnacle_age_s,
                    is_playoff)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, kalshi_ticker, series, sport, phase, minutes_to_start,
                 home_team, away_team, yes_sub_title, is_yes_home_int,
                 kalshi_yes_ask, kalshi_no_ask,
                 us_yes_prob, eu_yes_prob, pinnacle_yes_prob, pinnacle_age_s,
                 playoff_int),
            )
            await conn.commit()

    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?", (f"{today}%",)
            )
            row = await cursor.fetchone()
            return row[0]
