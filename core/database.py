import aiosqlite
from datetime import datetime, timezone
from typing import Optional

from core.season import is_playoff


class Database:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
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
                    is_playoff INTEGER,
                    starter_home TEXT,
                    starter_away TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ladder_violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    series TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    ladder_type TEXT NOT NULL,
                    leg_a_ticker TEXT NOT NULL,
                    leg_b_ticker TEXT,
                    inversion_amount REAL NOT NULL,
                    leg_a_depth REAL,
                    leg_b_depth REAL,
                    action_taken TEXT NOT NULL,
                    sub_strategy TEXT NOT NULL,
                    walked_edge_usdc REAL,
                    walked_legs_json TEXT,
                    top_vs_walked_max_gap_cents REAL,
                    would_fire INTEGER DEFAULT 0,
                    phase TEXT DEFAULT 'phase0_paper'
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lv_strategy_ts "
                "ON ladder_violations(sub_strategy, ts)"
            )
            # Idempotent column additions (Phase 0 resurrection 2026-05-19).
            # SQLite has no ADD COLUMN IF NOT EXISTS — use try/except per column.
            for col_def in (
                "walked_edge_usdc REAL",
                "walked_legs_json TEXT",
                "top_vs_walked_max_gap_cents REAL",
                "would_fire INTEGER DEFAULT 0",
                "phase TEXT DEFAULT 'phase0_paper'",
            ):
                try:
                    await conn.execute(f"ALTER TABLE ladder_violations ADD COLUMN {col_def}")
                except aiosqlite.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        raise
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_consensus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    prob REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(event_key, bucket, source)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mc_event_ts "
                "ON macro_consensus(event_key, fetched_at)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_event_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    consensus REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mes_ticker_ts "
                "ON macro_event_signals(ticker, ts)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_iv (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset TEXT NOT NULL,
                    expiry_date TEXT NOT NULL,
                    atm_iv REAL NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(asset, expiry_date)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_civ_asset_ts "
                "ON crypto_iv(asset, fetched_at)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_fair_value_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    spot REAL NOT NULL,
                    strike REAL NOT NULL,
                    vol REAL NOT NULL,
                    vol_source TEXT NOT NULL,
                    time_to_close_secs INTEGER NOT NULL,
                    fair_prob REAL NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cfvs_ticker_ts "
                "ON crypto_fair_value_signals(ticker, ts)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS polymarket_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    yes_price REAL NOT NULL,
                    no_price REAL NOT NULL,
                    volume_24h REAL,
                    fetched_at TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pmq_slug_ts "
                "ON polymarket_quotes(slug, fetched_at)"
            )
            # 2026-05-25: depth fields (best_bid/best_ask/spread/liquidity_num).
            # ALTER TABLE used since CREATE IF NOT EXISTS doesn't add columns
            # to pre-existing tables. Idempotent via try/except.
            for col_sql in (
                "ALTER TABLE polymarket_quotes ADD COLUMN best_bid REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN best_ask REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN spread REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN liquidity_num REAL",
                # 2026-05-25 CLOB depth (yes side / no side).
                "ALTER TABLE polymarket_quotes ADD COLUMN yes_bid_size REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN yes_ask_size REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN no_bid_size REAL",
                "ALTER TABLE polymarket_quotes ADD COLUMN no_ask_size REAL",
            ):
                try:
                    await conn.execute(col_sql)
                except aiosqlite.OperationalError:
                    pass  # column already exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS cross_venue_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    kalshi_implied REAL NOT NULL,
                    polymarket_implied REAL NOT NULL,
                    deviation REAL NOT NULL,
                    side TEXT,
                    expected_size_usdc REAL,
                    action_taken TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cvs_ticker_ts "
                "ON cross_venue_signals(ticker, ts)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_observer_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    observer TEXT NOT NULL,
                    series TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    predicted_outcome TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    actual_outcome TEXT,
                    resolution_ts TEXT
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cos_observer_ts "
                "ON crypto_observer_signals(observer, ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cos_market_ts "
                "ON crypto_observer_signals(market_id, ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cos_unsettled "
                "ON crypto_observer_signals(actual_outcome) "
                "WHERE actual_outcome IS NULL"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS polymarket_totals_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    game_date TEXT,
                    away_team_raw TEXT,
                    home_team_raw TEXT,
                    market_type TEXT NOT NULL,
                    line REAL NOT NULL,
                    spread_side TEXT,
                    yes_price REAL,
                    no_price REAL,
                    volume_24h REAL,
                    question TEXT,
                    kalshi_event_ticker TEXT
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ptot_slug_ts "
                "ON polymarket_totals_observations(slug, timestamp)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ptot_sport_date "
                "ON polymarket_totals_observations(sport, game_date)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ptot_kalshi_ts "
                "ON polymarket_totals_observations(kalshi_event_ticker, timestamp)"
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status_pnl ON trades(status, pnl)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_clv_trade ON clv_snapshots(trade_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_time ON odds_observations(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_ticker_time ON odds_observations(kalshi_ticker, timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_sport_phase ON odds_observations(sport, phase)")
            # 3-way market support (soccer): TIE leg gets is_yes_draw=1 with
            # is_yes_home=0; pinnacle_yes_prob stores draw_prob for those rows.
            # 2-way sports (baseball/basketball/hockey) leave is_yes_draw NULL.
            await self._add_column_if_missing(conn, "odds_observations", "is_yes_draw", "INTEGER")
            # Bookmaker coverage signal — same eu_yes_prob means very different
            # things at n_books=2 (NHL afternoon games, MLS minor) vs n_books=23
            # (EPL, UCL). NPB always ~9 books (Pinnacle covers Asian baseball
            # but other sharps don't). has_pinnacle flag distinguishes "Pinnacle
            # absent for this game" from "leg-detection failed".
            await self._add_column_if_missing(conn, "odds_observations", "n_books_eu", "INTEGER")
            await self._add_column_if_missing(conn, "odds_observations", "n_books_us", "INTEGER")
            await self._add_column_if_missing(conn, "odds_observations", "has_pinnacle", "INTEGER")
            # Kalshi liquidity / activity (free from market response — no extra
            # API calls). Lets analyses filter by depth/activity post-hoc.
            # Note: Kalshi only exposes YES-side top-of-book sizing; NO-side
            # depth is the inverted complement (NO ask price = 1 - YES bid,
            # sits at yes_bid_size). The kalshi_no_*_size columns stay NULL
            # for now — kept in schema so future server-side NO sizing won't
            # need a migration.
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_yes_bid", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_no_bid", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_yes_bid_size", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_yes_ask_size", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_no_bid_size", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_no_ask_size", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_volume_24h", "REAL")
            await self._add_column_if_missing(conn, "odds_observations", "kalshi_open_interest", "REAL")
            # Odds API endpoint slug at scan time (2026-05-11). Enables
            # differentiation of Italian Open (clay M1000) from French Open
            # (clay GS BO5) etc. — both appear with sport=ATP otherwise.
            await self._add_column_if_missing(conn, "odds_observations", "tournament_slug", "TEXT")
            await self._add_column_if_missing(conn, "odds_observations", "starter_home", "TEXT")
            await self._add_column_if_missing(conn, "odds_observations", "starter_away", "TEXT")
            await self._add_column_if_missing(conn, "clv_snapshots", "us_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "trades", "realized_fill_price", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "eu_consensus_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "pinnacle_age_s", "INTEGER")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_bid", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_ask", "REAL")
            # Book depth at entry time (2026-04-24). Useful for chalk_shadow
            # hypothesis testing with MIN_DEPTH_USDC=0 — we record depth but
            # don't gate on it, enabling post-hoc "what if depth≥$5?" filters.
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_yes_depth", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "kalshi_no_depth", "REAL")
            # Side-adjusted prob columns (2026-04-26). The legacy
            # us_consensus_prob/eu_consensus_prob/pinnacle_prob columns store
            # HOME-team probability per _dual_region_consensus docstring,
            # which makes side_prob analysis cumbersome (need home↔YES
            # mapping from observer). New *_yes_prob columns mirror the
            # observer's convention — already side-adjusted to "prob YES side
            # of this ticker wins". is_yes_home stored alongside for
            # downstream verification. Forward-only fill; old rows stay NULL.
            await self._add_column_if_missing(conn, "clv_snapshots", "is_yes_home", "INTEGER")
            await self._add_column_if_missing(conn, "clv_snapshots", "us_yes_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "eu_yes_prob", "REAL")
            await self._add_column_if_missing(conn, "clv_snapshots", "pinnacle_yes_prob", "REAL")
            await self._backfill_clv_yes_side(conn)
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
    async def _backfill_clv_yes_side(conn):
        """One-shot backfill of is_yes_home + yes_side_*_prob columns in
        clv_snapshots from odds_observations (which has is_yes_home for every
        captured ticker). Idempotent — only updates rows where is_yes_home
        is currently NULL. Skips rows whose ticker isn't in odds_observations.
        """
        cursor = await conn.execute(
            """SELECT c.id, t.market_id,
                      c.us_consensus_prob, c.eu_consensus_prob, c.pinnacle_prob
               FROM clv_snapshots c JOIN trades t ON c.trade_id = t.id
               WHERE c.is_yes_home IS NULL AND c.snapshot_at = 'entry'"""
        )
        rows = await cursor.fetchall()
        if not rows:
            return
        for clv_id, market_id, us_p, eu_p, pin_p in rows:
            ih_cursor = await conn.execute(
                "SELECT is_yes_home FROM odds_observations "
                "WHERE kalshi_ticker = ? AND is_yes_home IS NOT NULL LIMIT 1",
                (market_id,),
            )
            ih_row = await ih_cursor.fetchone()
            if not ih_row:
                continue
            is_yes_home = bool(ih_row[0])
            us_yes = (us_p if is_yes_home else 1 - us_p) if us_p is not None else None
            eu_yes = (eu_p if is_yes_home else 1 - eu_p) if eu_p is not None else None
            # pinnacle_prob in clv was already YES-side adjusted by caller; copy as-is
            pin_yes = pin_p
            await conn.execute(
                """UPDATE clv_snapshots
                   SET is_yes_home = ?, us_yes_prob = ?, eu_yes_prob = ?, pinnacle_yes_prob = ?
                   WHERE id = ?""",
                (1 if is_yes_home else 0, us_yes, eu_yes, pin_yes, clv_id),
            )

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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute(
                """INSERT INTO orders (timestamp, order_id, market_id, strategy, side, price, size, order_type, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, order_id, market_id, strategy, side, price, size, order_type, status),
            )
            await conn.commit()

    async def update_order_status(self, order_id, status):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
            await conn.commit()

    async def update_trade_post_fill(
        self, trade_id: int, *,
        status: str,
        size: Optional[float] = None,
        fee: Optional[float] = None,
        realized_fill_price: Optional[float] = None,
    ) -> None:
        """Update a 'placing' trade row to its final state (filled/cancelled).
        Built 2026-05-11 for P2 write-before-place pattern: trade row is
        INSERTed at status='placing' before the Kalshi order is sent, then
        UPDATEd here when Kalshi confirms fill or TTL expires.

        Only updates fields that are passed (non-None); status is mandatory.
        """
        sets = ["status = ?"]
        params: list = [status]
        if size is not None:
            sets.append("size = ?")
            params.append(size)
        if fee is not None:
            sets.append("fee = ?")
            params.append(fee)
        if realized_fill_price is not None:
            sets.append("realized_fill_price = ?")
            params.append(realized_fill_price)
        params.append(trade_id)
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute(
                f"UPDATE trades SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            await conn.commit()

    async def get_trades(self, strategy=None):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            if strategy:
                cursor = await conn.execute("SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp DESC", (strategy,))
            else:
                cursor = await conn.execute("SELECT * FROM trades ORDER BY timestamp DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_last_trade_for_ticker(
        self, strategy: str, market_id: str,
    ) -> Optional[dict]:
        """Return the most recent non-cancelled trade for (strategy, ticker).

        Used by Phase 1 traders for DB-persisted per-ticker cooldown:
        in-memory `_processed` set doesn't survive bot restarts, so the
        same ticker at the same price can re-fire from different bot
        processes within seconds. DB-side check survives restarts.

        Returns None if no prior trade exists. Excludes cancelled/failed
        so an aborted placing-row doesn't block legitimate re-entry.
        """
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """SELECT timestamp, price FROM trades
                   WHERE strategy = ? AND market_id = ?
                     AND status NOT IN ('cancelled', 'failed')
                   ORDER BY timestamp DESC LIMIT 1""",
                (strategy, market_id),
            )
            row = await cur.fetchone()
            return dict(row) if row is not None else None

    async def count_strategy_fires_for_event(
        self, strategy: str, event_key: str,
    ) -> int:
        """Count attempted trades for (strategy, event_key prefix).

        event_key is the market_id prefix shared across all strikes of a
        single event (e.g. 'KXBTC-26MAY2112' covers B76950..B78050). Used
        by Phase 1 traders for per-event fire cap — defends against
        single-event cascades where one underlying drift triggers fires
        across many adjacent strikes (see [[cme_basis_fade_v1_bleed_2026-05-21]]).

        2026-05-22: include 'cancelled' in the count. Originally excluded
        because the intent was "fills" not "attempts", but LIVE FOK rejects
        land in status='cancelled' (Kalshi declined to fill). When the cap
        ignored those, a thin near-expiry book produced 22+ fires/min on
        the same ticker (KXETH-26MAY2317-B2080 on 2026-05-22), each
        consuming a Kalshi API call and risking 429 bursts. Attempts-based
        cap is the right semantics for the underlying problem: bounded
        API/risk exposure per event, regardless of fill outcome.
        """
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                """SELECT COUNT(*) FROM trades
                   WHERE strategy = ? AND market_id LIKE ?
                     AND status != 'failed'""",
                (strategy, f"{event_key}-%"),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def has_existing_trade(self, strategy: str, market_id: str) -> bool:
        """Return True if a non-cancelled trade already exists for
        (strategy, market_id). Used by order_manager as a DB-side dedup
        guard so paper-twin / restart-corrupted in-memory state can't
        produce duplicate entries on a one-shot ticker (sports games).

        Counts any status other than 'cancelled'/'failed' as live —
        paper_filled/paper_settled/filled/settled all block re-entry.
        """
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cursor = await conn.execute(
                """SELECT 1 FROM trades
                   WHERE strategy = ? AND market_id = ?
                     AND status NOT IN ('cancelled', 'failed')
                   LIMIT 1""",
                (strategy, market_id),
            )
            return (await cursor.fetchone()) is not None

    async def record_ladder_violation(
        self, *, series: str, event_key: str, ladder_type: str,
        leg_a_ticker: str, leg_b_ticker: str | None,
        inversion_amount: float,
        leg_a_depth: float | None, leg_b_depth: float | None,
        action_taken: str, sub_strategy: str,
        # NEW Phase 0 fields (default None = backward compatible)
        walked_edge_usdc: float | None = None,
        walked_legs_json: str | None = None,
        top_vs_walked_max_gap_cents: float | None = None,
        would_fire: bool = False,
        phase: str = "phase0_paper",
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO ladder_violations "
                "(ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker, "
                " inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy, "
                " walked_edge_usdc, walked_legs_json, top_vs_walked_max_gap_cents, "
                " would_fire, phase) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, series, event_key, ladder_type, leg_a_ticker, leg_b_ticker,
                 inversion_amount, leg_a_depth, leg_b_depth, action_taken, sub_strategy,
                 walked_edge_usdc, walked_legs_json, top_vs_walked_max_gap_cents,
                 1 if would_fire else 0, phase),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_ladder_violations(
        self, sub_strategy: str, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM ladder_violations WHERE sub_strategy = ? "
                "ORDER BY ts DESC LIMIT ?",
                (sub_strategy, limit),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def record_macro_consensus(
        self, event_key: str, bucket: str, prob: float, source: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO macro_consensus "
                "(event_key, bucket, prob, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(event_key, bucket, source) DO UPDATE SET "
                "prob = excluded.prob, fetched_at = excluded.fetched_at",
                (event_key, bucket, prob, source, ts),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_macro_consensus(
        self, event_key: str, bucket: str, source: str | None = None,
    ) -> dict | None:
        """Returns the most recent consensus row for (event_key, bucket).
        If source is given, restrict to that source; else return any."""
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            if source:
                cur = await conn.execute(
                    "SELECT * FROM macro_consensus "
                    "WHERE event_key = ? AND bucket = ? AND source = ? "
                    "ORDER BY fetched_at DESC LIMIT 1",
                    (event_key, bucket, source),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM macro_consensus "
                    "WHERE event_key = ? AND bucket = ? "
                    "ORDER BY fetched_at DESC LIMIT 1",
                    (event_key, bucket),
                )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def record_macro_event_signal(
        self, *, ticker: str, event_key: str, bucket: str,
        kalshi_implied: float, consensus: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO macro_event_signals "
                "(ts, ticker, event_key, bucket, kalshi_implied, consensus, "
                " deviation, side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, event_key, bucket, kalshi_implied, consensus,
                 deviation, side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_macro_event_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM macro_event_signals ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def record_crypto_iv(
        self, asset: str, expiry_date: str, atm_iv: float,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO crypto_iv (asset, expiry_date, atm_iv, fetched_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(asset, expiry_date) DO UPDATE SET "
                "atm_iv = excluded.atm_iv, fetched_at = excluded.fetched_at",
                (asset, expiry_date, atm_iv, ts),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_nearest_crypto_iv(self, asset: str) -> dict | None:
        """Returns earliest expiry row for `asset`. ISO yyyy-mm-dd sorts correctly."""
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM crypto_iv WHERE asset = ? "
                "ORDER BY expiry_date ASC LIMIT 1",
                (asset,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def record_crypto_fair_value_signal(
        self, *, ticker: str, asset: str, spot: float, strike: float,
        vol: float, vol_source: str, time_to_close_secs: int,
        fair_prob: float, kalshi_implied: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO crypto_fair_value_signals "
                "(ts, ticker, asset, spot, strike, vol, vol_source, "
                " time_to_close_secs, fair_prob, kalshi_implied, deviation, "
                " side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, asset, spot, strike, vol, vol_source,
                 time_to_close_secs, fair_prob, kalshi_implied, deviation,
                 side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_crypto_fair_value_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM crypto_fair_value_signals "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def record_polymarket_quote(
        self, slug: str, yes_price: float, no_price: float,
        volume_24h: float | None = None,
        best_bid: float | None = None,
        best_ask: float | None = None,
        spread: float | None = None,
        liquidity_num: float | None = None,
        yes_bid_size: float | None = None,
        yes_ask_size: float | None = None,
        no_bid_size: float | None = None,
        no_ask_size: float | None = None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO polymarket_quotes "
                "(slug, yes_price, no_price, volume_24h, fetched_at, "
                " best_bid, best_ask, spread, liquidity_num, "
                " yes_bid_size, yes_ask_size, no_bid_size, no_ask_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET "
                "yes_price = excluded.yes_price, "
                "no_price = excluded.no_price, "
                "volume_24h = excluded.volume_24h, "
                "fetched_at = excluded.fetched_at, "
                "best_bid = excluded.best_bid, "
                "best_ask = excluded.best_ask, "
                "spread = excluded.spread, "
                "liquidity_num = excluded.liquidity_num, "
                "yes_bid_size = excluded.yes_bid_size, "
                "yes_ask_size = excluded.yes_ask_size, "
                "no_bid_size  = excluded.no_bid_size, "
                "no_ask_size  = excluded.no_ask_size",
                (slug, yes_price, no_price, volume_24h, ts,
                 best_bid, best_ask, spread, liquidity_num,
                 yes_bid_size, yes_ask_size, no_bid_size, no_ask_size),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_polymarket_quote(self, slug: str) -> dict | None:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM polymarket_quotes WHERE slug = ?",
                (slug,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def record_polymarket_totals_observation(
        self, *, slug: str, sport: str, game_date: str | None,
        away_team_raw: str | None, home_team_raw: str | None,
        market_type: str, line: float, spread_side: str | None,
        yes_price: float | None, no_price: float | None,
        volume_24h: float | None, question: str | None,
        kalshi_event_ticker: str | None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO polymarket_totals_observations "
                "(timestamp, slug, sport, game_date, away_team_raw, home_team_raw, "
                " market_type, line, spread_side, yes_price, no_price, volume_24h, "
                " question, kalshi_event_ticker) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, slug, sport, game_date, away_team_raw, home_team_raw,
                 market_type, line, spread_side, yes_price, no_price, volume_24h,
                 question, kalshi_event_ticker),
            )
            await conn.commit()
            return cur.lastrowid

    async def record_cross_venue_signal(
        self, *, ticker: str, slug: str,
        kalshi_implied: float, polymarket_implied: float, deviation: float,
        side: str | None, expected_size_usdc: float | None,
        action_taken: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                "INSERT INTO cross_venue_signals "
                "(ts, ticker, slug, kalshi_implied, polymarket_implied, "
                " deviation, side, expected_size_usdc, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, slug, kalshi_implied, polymarket_implied,
                 deviation, side, expected_size_usdc, action_taken),
            )
            await conn.commit()
            return cur.lastrowid

    async def fetch_recent_cross_venue_signals(
        self, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM cross_venue_signals ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def record_crypto_observer_signal(
        self, *, observer: str, series: str, event_key: str,
        market_id: str, predicted_outcome: str, features_json: str,
    ) -> int:
        """Insert a crypto observer signal. actual_outcome / resolution_ts
        are filled later by the settlement backfill cron.

        2026-05-26: 60s timeout + explicit busy_timeout PRAGMA + retry-once
        on "database is locked". Was failing ~12x/24h previously due to
        WAL checkpoint contention with concurrent collectors.
        """
        ts = datetime.now(timezone.utc).isoformat()
        last_err = None
        for attempt in range(2):
            try:
                async with aiosqlite.connect(self.db_path, timeout=60.0) as conn:
                    await conn.execute("PRAGMA busy_timeout = 60000")
                    cur = await conn.execute(
                        "INSERT INTO crypto_observer_signals "
                        "(ts, observer, series, event_key, market_id, "
                        " predicted_outcome, features_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ts, observer, series, event_key, market_id,
                         predicted_outcome, features_json),
                    )
                    await conn.commit()
                    return cur.lastrowid
            except Exception as e:
                last_err = e
                if "database is locked" in str(e).lower() and attempt == 0:
                    import asyncio
                    await asyncio.sleep(0.5)
                    continue
                raise
        raise last_err

    async def fetch_recent_crypto_observer_signals(
        self, observer: str | None = None, limit: int = 100,
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            if observer:
                cur = await conn.execute(
                    "SELECT * FROM crypto_observer_signals WHERE observer = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (observer, limit),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM crypto_observer_signals "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit,),
                )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_open_orders(self, limit: int = 100):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM orders WHERE status IN ('open', 'paper_open') ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_paper_open_orders(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM orders WHERE status = 'paper_open' ORDER BY id"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_trades_settled_since(self, since_iso: str, strategy: Optional[str] = None):
        """Trades whose underlying Kalshi ticker settled at-or-after `since_iso`.
        Joins trades.market_id → ticker_settlements.kalshi_ticker so the time
        filter uses the actual Kalshi settlement timestamp, not the bot's entry
        timestamp (entry can lead settlement by hours-to-days for sports).
        Used by the daily report's "Realized PnL Today" section.
        """
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            sql = (
                "SELECT t.* FROM trades t "
                "JOIN ticker_settlements s ON t.market_id = s.kalshi_ticker "
                "WHERE t.status = 'settled' AND s.settled_at >= ?"
            )
            params: list = [since_iso]
            if strategy is not None:
                sql += " AND t.strategy = ?"
                params.append(strategy)
            sql += " ORDER BY s.settled_at DESC"
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_unsettled_trades(self):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM trades "
                "WHERE pnl = 0 AND status IN ('paper_filled', 'paper_open', 'filled')"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def settle_trade(self, trade_id: int, pnl: float, status: str = "paper_settled"):
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
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
        kalshi_yes_depth: Optional[float] = None,
        kalshi_no_depth: Optional[float] = None,
        is_yes_home: Optional[bool] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        # Compute side-adjusted YES-side probabilities. The us/eu/pinnacle prob
        # arrives as HOME-team probability (per _dual_region_consensus). When
        # is_yes_home is True the YES side IS the home side, so prob unchanged;
        # else flip. Stays NULL when is_yes_home unknown.
        def _yes_side(home_prob):
            if home_prob is None or is_yes_home is None:
                return None
            return home_prob if is_yes_home else (1.0 - home_prob)
        us_yes_prob = _yes_side(us_consensus_prob)
        eu_yes_prob = _yes_side(eu_consensus_prob)
        # NOTE: the legacy `pinnacle_prob` column was already YES-side adjusted
        # by the caller (sports_trader._dispatch_entry computes pin_prob_for_side).
        # The new pinnacle_yes_prob mirrors it for column consistency with
        # us/eu_yes_prob, so analysis can pick from one set without checking
        # which column is which orientation.
        pin_yes_prob = pinnacle_prob
        is_yes_home_int = None if is_yes_home is None else (1 if is_yes_home else 0)
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute(
                """INSERT INTO clv_snapshots
                   (trade_id, snapshot_at, snapshot_time, yes_ask, no_ask, pinnacle_prob,
                    us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                    kalshi_yes_bid, kalshi_yes_ask,
                    kalshi_yes_depth, kalshi_no_depth,
                    is_yes_home, us_yes_prob, eu_yes_prob, pinnacle_yes_prob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, snapshot_at, now, yes_ask, no_ask, pinnacle_prob,
                 us_consensus_prob, eu_consensus_prob, pinnacle_age_s,
                 kalshi_yes_bid, kalshi_yes_ask,
                 kalshi_yes_depth, kalshi_no_depth,
                 is_yes_home_int, us_yes_prob, eu_yes_prob, pin_yes_prob),
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
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
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute(
                """INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, now),
            )
            await conn.commit()

    async def get_bot_state(self, key: str) -> Optional[float]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
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
        is_yes_draw: Optional[bool] = None,
        n_books_eu: Optional[int] = None,
        n_books_us: Optional[int] = None,
        has_pinnacle: Optional[bool] = None,
        kalshi_yes_bid: Optional[float] = None,
        kalshi_no_bid: Optional[float] = None,
        kalshi_yes_bid_size: Optional[float] = None,
        kalshi_yes_ask_size: Optional[float] = None,
        kalshi_no_bid_size: Optional[float] = None,
        kalshi_no_ask_size: Optional[float] = None,
        kalshi_volume_24h: Optional[float] = None,
        kalshi_open_interest: Optional[float] = None,
        tournament_slug: Optional[str] = None,
        starter_home: Optional[str] = None,
        starter_away: Optional[str] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        playoff = is_playoff(kalshi_ticker, now)
        playoff_int = None if playoff is None else (1 if playoff else 0)
        is_yes_home_int = None if is_yes_home is None else (1 if is_yes_home else 0)
        is_yes_draw_int = None if is_yes_draw is None else (1 if is_yes_draw else 0)
        has_pinnacle_int = None if has_pinnacle is None else (1 if has_pinnacle else 0)
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            await conn.execute(
                """INSERT INTO odds_observations
                   (timestamp, kalshi_ticker, series, sport, phase, minutes_to_start,
                    home_team, away_team, yes_sub_title, is_yes_home, is_yes_draw,
                    kalshi_yes_ask, kalshi_no_ask,
                    us_yes_prob, eu_yes_prob, pinnacle_yes_prob, pinnacle_age_s,
                    is_playoff,
                    n_books_eu, n_books_us, has_pinnacle,
                    kalshi_yes_bid, kalshi_no_bid,
                    kalshi_yes_bid_size, kalshi_yes_ask_size,
                    kalshi_no_bid_size, kalshi_no_ask_size,
                    kalshi_volume_24h, kalshi_open_interest,
                    tournament_slug, starter_home, starter_away)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, kalshi_ticker, series, sport, phase, minutes_to_start,
                 home_team, away_team, yes_sub_title, is_yes_home_int, is_yes_draw_int,
                 kalshi_yes_ask, kalshi_no_ask,
                 us_yes_prob, eu_yes_prob, pinnacle_yes_prob, pinnacle_age_s,
                 playoff_int,
                 n_books_eu, n_books_us, has_pinnacle_int,
                 kalshi_yes_bid, kalshi_no_bid,
                 kalshi_yes_bid_size, kalshi_yes_ask_size,
                 kalshi_no_bid_size, kalshi_no_ask_size,
                 kalshi_volume_24h, kalshi_open_interest,
                 tournament_slug, starter_home, starter_away),
            )
            await conn.commit()

    async def total_open_exposure(self, market_id: str) -> float:
        """Sum of `size` across all strategies for open trades on this ticker.

        Open = status in ('paper_filled', 'paper_open', 'filled', 'open').
        Excludes settled / closed / failed trades.
        """
        OPEN_STATUSES = ("paper_filled", "paper_open", "filled", "open")
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cur = await conn.execute(
                f"SELECT COALESCE(SUM(size), 0.0) FROM trades "
                f"WHERE market_id = ? AND status IN ({','.join('?' * len(OPEN_STATUSES))})",
                (market_id, *OPEN_STATUSES),
            )
            row = await cur.fetchone()
            return float(row[0]) if row else 0.0

    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path, timeout=30.0) as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?", (f"{today}%",)
            )
            row = await cursor.fetchone()
            return row[0]
