import aiosqlite
from datetime import datetime, timezone
from typing import Optional


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
                    status TEXT NOT NULL
                )
            """)
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
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status_pnl ON trades(status, pnl)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_clv_trade ON clv_snapshots(trade_id)")
            await conn.commit()

    async def close(self):
        pass

    async def record_trade(self, strategy, market_id, direction, price, size, fee, status, pnl=0.0) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """INSERT INTO trades (timestamp, strategy, market_id, direction, price, size, fee, pnl, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, strategy, market_id, direction, price, size, fee, pnl, status),
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
                "SELECT * FROM trades WHERE pnl = 0 AND status IN ('paper_filled', 'paper_open')"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def settle_trade(self, trade_id: int, pnl: float, status: str = "paper_settled"):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE trades SET pnl = ?, status = ? WHERE id = ?", (pnl, status, trade_id)
            )
            await conn.commit()

    async def record_clv_snapshot(self, trade_id: int, snapshot_at: str,
                                   yes_ask: Optional[float], no_ask: Optional[float],
                                   pinnacle_prob: Optional[float] = None):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO clv_snapshots
                   (trade_id, snapshot_at, snapshot_time, yes_ask, no_ask, pinnacle_prob)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (trade_id, snapshot_at, now, yes_ask, no_ask, pinnacle_prob),
            )
            await conn.commit()

    async def get_clv_positive_rate(self, strategy: str, days: int = 30) -> float:
        """Fraction of trades (for strategy, within last N days) where the closing price
        moved favorably relative to entry.

        YES direction: CLV+ when closing yes_ask > entry yes_ask.
        NO direction: CLV+ when closing no_ask > entry no_ask.
        Only trades with both entry AND closing snapshots are counted.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT t.direction,
                       e.yes_ask AS entry_yes, e.no_ask AS entry_no,
                       c.yes_ask AS close_yes, c.no_ask AS close_no
                FROM trades t
                JOIN clv_snapshots e ON e.trade_id = t.id AND e.snapshot_at = 'entry'
                JOIN clv_snapshots c ON c.trade_id = t.id AND c.snapshot_at = 'closing'
                WHERE t.strategy = ?
                  AND t.timestamp >= datetime('now', ?)
                """,
                (strategy, f"-{int(days)} days"),
            )
            rows = await cursor.fetchall()
        if not rows:
            return 0.0
        total = len(rows)
        pos = 0
        for r in rows:
            if r["direction"] == "YES":
                entry = r["entry_yes"]
                close = r["close_yes"]
            else:
                entry = r["entry_no"]
                close = r["close_no"]
            if entry is None or close is None:
                continue
            if close > entry:
                pos += 1
        return pos / total

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

    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?", (f"{today}%",)
            )
            row = await cursor.fetchone()
            return row[0]
