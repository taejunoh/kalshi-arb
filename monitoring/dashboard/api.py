from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
from zoneinfo import ZoneInfo
import os
import sqlite3


def _today_cutoff_utc() -> str:
    """Most recent 06:00 ET boundary as UTC ISO string.
    Chosen over midnight so the boundary lands in a quiet window between
    west-coast late games (end ~01:30 ET) and next-day pre-game activity
    (starts ~08:00 ET) — no games get split across the cutoff."""
    et = datetime.now(ZoneInfo("America/New_York"))
    if et.hour < 6:
        start = (et - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        start = et.replace(hour=6, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


_DASHBOARD_HIDDEN_SPORTS: tuple = ()
"""Sports excluded from Regime card + Observer signals panel.
Empty as of 2026-05-03: NPB/KBO/BUN re-enabled after the prior
'untradeable' classification was found to use OI as the gate metric.
Top-of-book depth is what fires actual trades; NPB p50 $122 (100% pass),
KBO p50 $90 (100% pass), BUN p50 $20 (51% pass). All collecting data
for 5/9 promotion eval."""

_DASHBOARD_TRACKED_SPORTS: tuple = (
    "NBA", "MLB", "NHL",
    "NPB", "KBO",
    "MLS", "EPL", "LAL", "UCL", "BUN",
)
"""Sports the Regime card always shows — even with 0 obs in the last
60 min — so users can see the sport is monitored but currently waiting
on data (typical for KBO/NPB/UCL where Odds API publishes games only
within ~24-48h of kickoff). Without this, regional sports between game
days silently disappear from the dashboard and look like a bug."""


def create_app(database, bankroll: float = 0.0, kalshi_client=None, config=None, sports_trader=None) -> FastAPI:
    app = FastAPI(title="Kalshi Arb Bot Dashboard")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/status")
    async def get_status():
        daily_pnl = await database.get_daily_pnl()
        trades = await database.get_trades()
        return {
            "bankroll": bankroll,
            "daily_pnl": daily_pnl,
            "trade_count": len(trades),
        }

    @app.get("/api/trades")
    async def get_trades(strategy: Optional[str] = Query(None)):
        trades = await database.get_trades(strategy=strategy)
        return trades

    @app.get("/api/orders")
    async def get_open_orders():
        orders = await database.get_open_orders()
        return orders

    def _categorize_live_ticker(ticker: str) -> Optional[str]:
        """Map Kalshi ticker to a strategy name for Live bucket accounting.
        Kalshi has no concept of our strategy names, so we infer from ticker
        prefix. Returns None if ticker doesn't belong to any known Live
        strategy (caller skips)."""
        if "FEDMENTION" in ticker:
            return "mentions_trader"
        if any(s in ticker for s in ["KXCPI", "KXGDP", "KXFED"]):
            # Legacy: econ_trader placed live CPI/GDP/FED trades before being
            # disabled. Positions still tracked on Kalshi.
            return "econ_trader"
        if any(s in ticker for s in ["WNBAGAME", "NBAGAME", "MLBGAME", "NHLGAME",
                                      "NPBGAME", "KBOGAME",
                                      "MLSGAME", "EPLGAME", "LALIGAGAME",
                                      "UCLGAME", "BUNDESLIGAGAME"]):
            # Sports subclasses (longshot/midshot/underdog) cannot be
            # disambiguated from ticker alone. Attribute to generic
            # sports_trader. Currently none are Live anyway.
            # WNBAGAME must come before NBAGAME (substring collision).
            return "sports_trader"
        return None

    @app.get("/api/performance")
    async def get_performance():
        """Return performance stats per strategy. Paper bucket is reconstructed
        from the local DB (source of truth for Paper sim). Live bucket is
        reconstructed from Kalshi API (positions + settlements) — source of
        truth for real money, regardless of local DB state."""
        all_trades = await database.get_trades()
        all_strategy_names = [
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_longshot", "sports_midshot", "sports_underdog",
            "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
            "sports_longshot_v32", "sports_midshot_v32", "sports_underdog_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            "sports_longshot_v34", "sports_midshot_v34", "sports_underdog_v34",
            "sports_longshot_v35", "sports_midshot_v35", "sports_underdog_v35",
            "sports_playoff_underdog_v1", "sports_playoff_underdog_v1_us",
            "sports_soccer_underdog_v1",
            "sports_soccer_weekend_v1",
            "sports_consensus_aligned_live_v1",
            "sports_soccer_draw_v1",
            "sports_mls_fade_favorite_v1",
            "sports_npb_underdog_v1",
            "sports_underdog_v36",
            "sports_underdog_v37",
            "sports_favorite_v40",
            "sports_tennis_pinnacle_arb_v1",
            "sports_atp_gs_bo5_chalk_v1",
            "sports_tennis_bid_imbalance_v1",
            "sports_ufc_chalk_fade_v1",
            "sports_tennis_surface_skew_v1",
            "sports_tennis_live_fade_v1",
            # 2026-05-26 additions — paper-only Phase 0
            "sports_tennis_chalk_v1",
            "sports_tennis_pinnacle_fade_v1",
            "sports_npb_sharp_follow_v1",
            "sports_lal_sharp_follow_v1",
            "sports_mlb_pitcher_edge_v1",
            "sports_mlb_pitcher_edge_v2",
            "sports_mlb_pitcher_edge_v2_1",
            "sports_pinnacle_edge", "sports_v4_edge", "sports_chalk_shadow",
            # Crypto Phase 1 traders (paper-only unless promoted via .env).
            # Listed here so they render with empty stats before first fire.
            "deribit_tail_fade_v1", "bid_imbalance_v1", "bid_imbalance_fade_v1",
            "open_no_touch_v1", "atm_hold_v1",
            # 2026-05-18 additions
            "late_close_tail_v1", "deribit_atm_fade_v1",
            "cme_basis_fade_v1",
            # 2026-05-21: sister of deribit_tail_fade_v1 for high-ya zone
            "deribit_tail_fade_high_v1",
            # Archived — historical data surfaced in dashboard's Archived section.
            "sports_trader", "sports_liquidity_filtered", "sports_consolidated",
            "econ_trader",
        ]

        today_cutoff = _today_cutoff_utc()

        def empty_today():
            # trade_count = total entries in window (settled + pending).
            # settled_count = subset with realized pnl (paper_settled / settled).
            # pending = trade_count - settled_count, derived in the frontend.
            return {"trade_count": 0, "settled_count": 0,
                    "total_pnl": 0.0, "wins": 0, "losses": 0}

        def empty_stats():
            # open_count/open_exposure are CURRENTLY open positions (any entry
            # date), distinct from today.trade_count which is entries since
            # the most recent 06:00 ET. A position entered yesterday and still
            # unsettled shows in open_count but not in today.*.
            return {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0, "wins": 0, "losses": 0,
                    "clv_positive_rate": 0.0, "open_count": 0, "open_exposure": 0.0,
                    "today": empty_today()}

        def empty_split():
            return {"trade_count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

        SPORTS_PREFIX = "sports_"
        # Crypto Phase 1 strategies use the DB strategy column for Live
        # attribution. _categorize_live_ticker can't disambiguate which
        # crypto trader (atm_hold vs atm_fade vs deribit_tail_fade vs
        # late_close_tail vs bid_imbalance, etc.) bought a given KXBTC/
        # KXETH ticker — multiple Phase 1 strategies share the same
        # ticker pattern. Mirrors the sports_* DB-attribution path that
        # exists for the same reason (v31/v32/v33 subtypes collide on
        # *GAME tickers). Pre-fix (2026-05-20) crypto LIVE trades fell
        # through both paths and rendered as 0 on the dashboard despite
        # populating trades.status='settled' rows correctly.
        CRYPTO_DB_ATTRIBUTED = {
            "deribit_tail_fade_v1", "deribit_tail_fade_high_v1",
            "atm_hold_v1",
            "bid_imbalance_v1", "bid_imbalance_fade_v1",
            "open_no_touch_v1", "late_close_tail_v1",
            "deribit_atm_fade_v1", "cme_basis_fade_v1",
            "crypto_fair_value", "crypto_fair_value_mid_v1",
        }
        # Retired strategies — still rendered but flagged via `archived: true`
        # so the frontend can show them in a separate Archived section.
        RETIRED = {
            # Older infra retirements
            "maker_preorder", "shutdown_fade",
            # V3 baseline (replaced by V3.1 trio 2026-04-17)
            "sports_longshot", "sports_midshot", "sports_underdog",
            # Sports experiments closed
            "sports_trader", "sports_liquidity_filtered", "sports_consolidated",
            "sports_pinnacle_edge", "sports_chalk_shadow",
            # V3.2 / V3.3 retired 2026-04-24 (5/6 underperform v31)
            "sports_longshot_v32", "sports_midshot_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            # V3.4 longshot retired 2026-04-26 (duplicate of v35 paper twin)
            "sports_longshot_v34",
            # Non-sports retired
            "econ_trader",
            # 2026-05-20: crypto_fair_value pair retired — both deeply -EV.
            # parent -$870/104 (41% WR), mid_v1 -$1,017/55 (20% WR). Lognormal
            # fair systematically overstates short-dated tail prob;
            # deribit_tail_fade_v1 already captures the inverse.
            "crypto_fair_value", "crypto_fair_value_mid_v1",
        }

        def _stats_with_split(strat_name: str) -> dict:
            """Sports strategies get pre/live sub-buckets for minutes_to_start split."""
            s = empty_stats()
            if strat_name.startswith(SPORTS_PREFIX):
                s["pre"] = empty_split()
                s["live"] = empty_split()
            return s

        strategies = {}
        for s in all_strategy_names:
            strategies[s] = {"live": _stats_with_split(s), "paper": _stats_with_split(s),
                             "archived": s in RETIRED}

        # Paper bucket — from local DB trades (only paper_* statuses)
        for trade in all_trades:
            status = trade.get("status", "")
            if not status.startswith("paper"):
                continue
            strat = trade["strategy"]
            if strat not in strategies:
                strategies[strat] = {"live": _stats_with_split(strat), "paper": _stats_with_split(strat),
                                     "archived": strat in RETIRED}
            bucket = strategies[strat]["paper"]
            pnl = trade.get("pnl", 0) or 0
            size = trade.get("size", 0) or 0
            bucket["trade_count"] += 1
            bucket["total_pnl"] += pnl
            bucket["total_size"] += size
            if pnl > 0:
                bucket["wins"] += 1
            elif pnl < 0:
                bucket["losses"] += 1
            # Currently-open paper positions (any entry date). paper_stale
            # is excluded — it marks abandoned/expired paper rows that won't
            # settle. Settled rows have pnl set and live in trade_count above.
            if status in ("paper_open", "paper_filled"):
                bucket["open_count"] += 1
                bucket["open_exposure"] += size
            # Today (since most recent 06:00 ET) — based on entry timestamp
            if trade.get("timestamp", "") >= today_cutoff:
                t = bucket["today"]
                t["trade_count"] += 1
                t["total_pnl"] += pnl
                if status == "paper_settled":
                    t["settled_count"] += 1
                if pnl > 0:
                    t["wins"] += 1
                elif pnl < 0:
                    t["losses"] += 1
            # Sports pre/live split
            if strat.startswith(SPORTS_PREFIX):
                mts = trade.get("minutes_to_start")
                if mts is not None:
                    sub = bucket["pre"] if mts > 0 else bucket["live"]
                    sub["trade_count"] += 1
                    sub["total_pnl"] += pnl
                    if pnl > 0:
                        sub["wins"] += 1
                    elif pnl < 0:
                        sub["losses"] += 1

        # Live pre/live split — from local DB rows with non-paper status. Mirrors
        # the cumulative live aggregation below (settled trades regardless of
        # current LIVE_STRATEGIES membership), so demoted strategies (e.g.,
        # 2026-04-28 sports_longshot_v35) keep their historical pre/live split
        # consistent with the cumulative count instead of going to 0/0 while
        # the top-line still shows the prior live trades.
        for trade in all_trades:
            strat = trade["strategy"]
            if strat in RETIRED:
                continue
            if not strat.startswith(SPORTS_PREFIX):
                continue
            if trade.get("status", "").startswith("paper"):
                # Paper rows belong to the paper bucket above, not the live split.
                continue
            if strat not in strategies:
                strategies[strat] = {"live": _stats_with_split(strat), "paper": _stats_with_split(strat),
                                     "archived": strat in RETIRED}
            bucket = strategies[strat]["live"]
            if "pre" not in bucket:  # bucket was init'd before strat was known — augment now
                bucket["pre"] = empty_split()
                bucket["live"] = empty_split()
            mts = trade.get("minutes_to_start")
            if mts is None:
                continue
            pnl = trade.get("pnl", 0) or 0
            sub = bucket["pre"] if mts > 0 else bucket["live"]
            sub["trade_count"] += 1
            sub["total_pnl"] += pnl
            if pnl > 0:
                sub["wins"] += 1
            elif pnl < 0:
                sub["losses"] += 1

        # Live bucket — from Kalshi API (positions for open, settlements for closed)
        if kalshi_client:
            import asyncio
            loop = asyncio.get_event_loop()
            # Run both Kalshi API calls concurrently — they're independent and
            # each takes ~100-300ms; gather halves the wall time.
            pos_task = loop.run_in_executor(None, kalshi_client.get_positions)
            set_task = loop.run_in_executor(None, kalshi_client.get_settlements)
            results = await asyncio.gather(pos_task, set_task, return_exceptions=True)
            positions = results[0] if not isinstance(results[0], Exception) else []
            settlements = results[1] if not isinstance(results[1], Exception) else []

            # Settled trades: wins/losses/realized PnL
            for s in settlements:
                ticker = s.get("ticker", "")
                strat = _categorize_live_ticker(ticker)
                if not strat or strat not in strategies:
                    continue
                # Sports tickers can't disambiguate longshot/midshot/underdog
                # — attribution is done from the DB below instead.
                if strat.startswith(SPORTS_PREFIX) or strat == "sports_trader":
                    continue
                revenue = float(s.get("revenue", 0)) / 100  # cents → dollars
                yes_cost = float(s.get("yes_total_cost_dollars", "0") or 0)
                no_cost = float(s.get("no_total_cost_dollars", "0") or 0)
                cost = yes_cost + no_cost
                fees = float(s.get("fee_cost", "0") or 0)
                net = revenue - cost - fees
                bucket = strategies[strat]["live"]
                bucket["trade_count"] += 1
                bucket["total_size"] += cost
                bucket["total_pnl"] += net
                if revenue > cost:
                    bucket["wins"] += 1
                elif cost > 0:
                    bucket["losses"] += 1

            # Sports + crypto Phase 1 live aggregates: use DB strategy column
            # since Kalshi ticker alone can't distinguish subtypes (sports v31
            # vs v32 etc., crypto atm_hold vs atm_fade etc.).
            for trade in all_trades:
                if trade.get("status") != "settled":
                    continue
                strat = trade["strategy"]
                if strat in RETIRED:
                    continue
                if not (strat.startswith(SPORTS_PREFIX) or strat in CRYPTO_DB_ATTRIBUTED):
                    continue
                if strat not in strategies:
                    continue
                bucket = strategies[strat]["live"]
                pnl = trade.get("pnl", 0) or 0
                size = trade.get("size", 0) or 0
                bucket["trade_count"] += 1
                bucket["total_pnl"] += pnl
                bucket["total_size"] += size
                if pnl > 0:
                    bucket["wins"] += 1
                elif pnl < 0:
                    bucket["losses"] += 1
                if trade.get("timestamp", "") >= today_cutoff:
                    t = bucket["today"]
                    t["trade_count"] += 1
                    t["total_pnl"] += pnl
                    t["settled_count"] += 1  # reached this branch only on settled rows
                    if pnl > 0:
                        t["wins"] += 1
                    elif pnl < 0:
                        t["losses"] += 1

            # Also count live filled (open) as entries in today's sub-bucket — they
            # aren't settled yet so PnL stays 0 until finalization, but the user
            # sees a signal was taken today. Sports + crypto Phase 1 share the
            # same DB-attribution path (Kalshi can't disambiguate subtypes).
            for trade in all_trades:
                if trade.get("status") != "filled":
                    continue
                if trade.get("timestamp", "") < today_cutoff:
                    continue
                strat = trade["strategy"]
                if strat in RETIRED:
                    continue
                if not (strat.startswith(SPORTS_PREFIX) or strat in CRYPTO_DB_ATTRIBUTED):
                    continue
                if strat not in strategies:
                    continue
                strategies[strat]["live"]["today"]["trade_count"] += 1

            # Open positions: count and exposure, PnL unrealized (stays 0 until settled)
            for p in positions:
                try:
                    pos_fp = float(p.get("position_fp", 0) or 0)
                except (TypeError, ValueError):
                    pos_fp = 0
                if pos_fp == 0:
                    continue  # closed / zero position, skip
                ticker = p.get("ticker", "")
                strat = _categorize_live_ticker(ticker)
                if not strat or strat not in strategies:
                    continue
                try:
                    exp = float(p.get("market_exposure_dollars", 0) or 0)
                except (TypeError, ValueError):
                    exp = 0
                bucket = strategies[strat]["live"]
                bucket["trade_count"] += 1
                bucket["total_size"] += exp

            # Live open_count/open_exposure — DB filled rows are the source of
            # truth for per-strategy attribution (Kalshi positions API can't
            # disambiguate sports v31/v37/etc. or crypto subtypes that share
            # tickers). Mirrors the DB-attribution path used for live settled
            # rows above.
            for trade in all_trades:
                if trade.get("status") != "filled":
                    continue
                strat = trade.get("strategy")
                if not strat or strat in RETIRED or strat not in strategies:
                    continue
                bucket = strategies[strat]["live"]
                bucket["open_count"] += 1
                bucket["open_exposure"] += trade.get("size", 0) or 0

        # CLV+ rate per (strategy, mode). Batched single query — the prior
        # per-strategy loop ran ~120 sequential SQLite connections (~1.2s on
        # the bot DB) and dominated /api/performance latency.
        try:
            clv_rates = await database.get_clv_positive_rates_batch(days=30)
        except Exception:
            clv_rates = {}
        for strat_name in strategies:
            for mode in ("paper", "live"):
                strategies[strat_name][mode]["clv_positive_rate"] = clv_rates.get(
                    (strat_name, mode), 0.0
                )

        return strategies

    @app.get("/api/settlement")
    async def get_settlement():
        all_trades = await database.get_trades()
        all_orders = await database.get_open_orders()

        # Separate live vs paper
        live_strategies = set(config.LIVE_STRATEGIES) if config else set()

        paper_settled = [t for t in all_trades if t.get("status") == "paper_settled"]
        paper_unsettled = [t for t in all_trades if t.get("status") in ("paper_filled", "paper_open")]
        paper_pnl = sum(t.get("pnl", 0) for t in paper_settled)
        # Live realized PnL: DB rows with status='settled' carry strategy-
        # attributed PnL from our settlement pass. Kalshi's settlements API
        # can't disambiguate sports v31 subtypes, so DB is the source here.
        live_settled = [t for t in all_trades if t.get("status") == "settled"]
        live_pnl = sum(t.get("pnl", 0) for t in live_settled)

        # Live: calculate from Kalshi positions
        live_fees = 0.0
        live_positions = 0
        live_exposure = 0.0
        if kalshi_client:
            import asyncio
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, kalshi_client.get_positions)
            for p in positions:
                pos = float(p.get("position_fp", 0))
                if pos != 0:
                    live_positions += 1
                    live_exposure += float(p.get("market_exposure_dollars", 0))
                    live_fees += float(p.get("fees_paid_dollars", 0))

        by_strategy = {}
        for t in paper_settled:
            strat = t["strategy"]
            if strat not in by_strategy:
                by_strategy[strat] = {"settled": 0, "pnl": 0.0, "wins": 0, "losses": 0}
            by_strategy[strat]["settled"] += 1
            by_strategy[strat]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                by_strategy[strat]["wins"] += 1
            elif t.get("pnl", 0) < 0:
                by_strategy[strat]["losses"] += 1

        return {
            "paper": {
                "settled_count": len(paper_settled),
                "unsettled_count": len(paper_unsettled),
                "realized_pnl": round(paper_pnl, 2),
                "by_strategy": by_strategy,
            },
            "live": {
                "positions": live_positions,
                "exposure": round(live_exposure, 2),
                "fees_paid": round(live_fees, 2),
                "settled_count": len(live_settled),
                "realized_pnl": round(live_pnl, 2),
            },
            # Keep backward compat
            "settled_count": len(paper_settled),
            "unsettled_count": len(paper_unsettled),
            "total_realized_pnl": round(paper_pnl, 2),
            "by_strategy": by_strategy,
        }

    @app.get("/api/live")
    async def get_live():
        if not kalshi_client:
            return {"balance": None, "positions": []}
        import asyncio
        loop = asyncio.get_event_loop()
        balance = await loop.run_in_executor(None, kalshi_client.get_balance)
        positions = await loop.run_in_executor(None, kalshi_client.get_positions)
        return {
            "balance": balance,
            "positions": positions,
        }

    @app.get("/api/strategy-status")
    async def get_strategy_status():
        live_strategies = list(config.LIVE_STRATEGIES) if config else []
        # Last fire (ISO UTC) per strategy — used to render idle warnings
        # in the dashboard card header. NULL for strategies that never fired.
        last_fire_at: dict = {}
        try:
            import aiosqlite
            async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
                cur = await conn.execute(
                    "SELECT strategy, MAX(timestamp) FROM trades "
                    "WHERE strategy IS NOT NULL GROUP BY strategy"
                )
                for s, ts in await cur.fetchall():
                    if s and ts:
                        last_fire_at[s] = ts
        except Exception:
            last_fire_at = {}
        all_strategies = [
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_longshot", "sports_midshot", "sports_underdog",
            "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
            "sports_longshot_v32", "sports_midshot_v32", "sports_underdog_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            "sports_longshot_v34", "sports_midshot_v34", "sports_underdog_v34",
            "sports_longshot_v35", "sports_midshot_v35", "sports_underdog_v35",
            "sports_playoff_underdog_v1", "sports_playoff_underdog_v1_us",
            "sports_soccer_underdog_v1",
            "sports_soccer_weekend_v1",
            "sports_consensus_aligned_live_v1",
            "sports_soccer_draw_v1",
            "sports_mls_fade_favorite_v1",
            "sports_npb_underdog_v1",
            "sports_underdog_v36",
            "sports_underdog_v37",
            "sports_favorite_v40",
            "sports_tennis_pinnacle_arb_v1",
            "sports_atp_gs_bo5_chalk_v1",
            "sports_tennis_bid_imbalance_v1",
            "sports_ufc_chalk_fade_v1",
            "sports_tennis_surface_skew_v1",
            "sports_tennis_live_fade_v1",
            # 2026-05-26 additions — paper-only Phase 0
            "sports_tennis_chalk_v1",
            "sports_tennis_pinnacle_fade_v1",
            "sports_npb_sharp_follow_v1",
            "sports_lal_sharp_follow_v1",
            "sports_mlb_pitcher_edge_v1",
            "sports_mlb_pitcher_edge_v2",
            "sports_mlb_pitcher_edge_v2_1",
            "sports_pinnacle_edge", "sports_v4_edge", "sports_chalk_shadow",
            # Phase 1 paper traders promoted from crypto_observer 2026-05-14.
            "deribit_tail_fade_v1", "bid_imbalance_v1",
            "open_no_touch_v1", "atm_hold_v1",
            "bid_imbalance_fade_v1",
            # 2026-05-18 additions
            "late_close_tail_v1", "deribit_atm_fade_v1",
            "cme_basis_fade_v1",
            # 2026-05-21: sister of deribit_tail_fade_v1 for high-ya zone
            "deribit_tail_fade_high_v1",
            # Archived — surfaced in dashboard Archived section with reason.
            "sports_trader", "sports_liquidity_filtered", "sports_consolidated",
            "econ_trader",
        ]
        archived_set = {
            # Older infra retirements
            "maker_preorder", "shutdown_fade",
            # V3 baseline (replaced by V3.1 trio 2026-04-17)
            "sports_longshot", "sports_midshot", "sports_underdog",
            # Sports experiments closed
            "sports_trader", "sports_liquidity_filtered", "sports_consolidated",
            "sports_pinnacle_edge", "sports_chalk_shadow",
            # V3.2 / V3.3 retired 2026-04-24 (5/6 underperform v31)
            "sports_longshot_v32", "sports_midshot_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            # V3.4 longshot retired 2026-04-26 (duplicate of v35 paper twin)
            "sports_longshot_v34",
            # Non-sports retired
            "econ_trader",
            # 2026-05-20: crypto_fair_value pair retired — both deeply -EV.
            # parent -$870/104 (41% WR), mid_v1 -$1,017/55 (20% WR). Lognormal
            # fair systematically overstates short-dated tail prob;
            # deribit_tail_fade_v1 already captures the inverse.
            "crypto_fair_value", "crypto_fair_value_mid_v1",
        }
        # Strategies removed from main.py _strategy_loop (no env flag to toggle).
        # Keep in sync with main.py; see commits 59f7e44 (sports_trader) and
        # e901f2b (maker_preorder).
        loop_disabled = {"sports_trader", "maker_preorder", "shutdown_fade",
                         "sports_liquidity_filtered", "sports_consolidated",
                         "sports_longshot", "sports_midshot", "sports_underdog",
                         "sports_pinnacle_edge", "mentions_trader",
                         # Retired 2026-04-24 — pre-game-only re-eval (commit 67f1f99)
                         "sports_longshot_v32", "sports_midshot_v32",
                         "sports_longshot_v33", "sports_midshot_v33",
                         "sports_underdog_v33",
                         # Retired 2026-04-26 — n=45 / -18.5% ROI paper close
                         "sports_chalk_shadow",
                         # Retired 2026-04-26 — duplicate of v35's paper twin
                         # after (MLB,pre) cell removal
                         "sports_longshot_v34"}
        # env-flag mapping: which config attribute gates each strategy
        flag_map = {
            "market_maker": "MM_ENABLED",
            "shutdown_fade": "SHUTDOWN_FADE_ENABLED",
            "spread_scanner": "SPREAD_SCANNER_ENABLED",
            "mentions_trader": "MENTIONS_ENABLED",
            "econ_trader": "ECON_ENABLED",
            "sports_longshot": "SPORTS_ENABLED",
            "sports_midshot": "SPORTS_ENABLED",
            "sports_underdog": "SPORTS_ENABLED",
            "sports_longshot_v31": "SPORTS_ENABLED",
            "sports_midshot_v31": "SPORTS_ENABLED",
            "sports_underdog_v31": "SPORTS_ENABLED",
            "sports_longshot_v32": "SPORTS_ENABLED",
            "sports_midshot_v32": "SPORTS_ENABLED",
            "sports_underdog_v32": "SPORTS_ENABLED",
            "sports_longshot_v33": "SPORTS_ENABLED",
            "sports_midshot_v33": "SPORTS_ENABLED",
            "sports_underdog_v33": "SPORTS_ENABLED",
            "sports_longshot_v34": "SPORTS_ENABLED",
            "sports_midshot_v34": "SPORTS_ENABLED",
            "sports_underdog_v34": "SPORTS_ENABLED",
            "sports_longshot_v35": "SPORTS_ENABLED",
            "sports_midshot_v35": "SPORTS_ENABLED",
            "sports_underdog_v35": "SPORTS_ENABLED",
            "sports_playoff_underdog_v1": "SPORTS_ENABLED",
            "sports_playoff_underdog_v1_us": "SPORTS_ENABLED",
            "sports_soccer_underdog_v1": "SPORTS_ENABLED",
            "sports_soccer_weekend_v1": "SPORTS_ENABLED",
            "sports_consensus_aligned_live_v1": "SPORTS_ENABLED",
            "sports_soccer_draw_v1": "SPORTS_ENABLED",
            "sports_mls_fade_favorite_v1": "SPORTS_ENABLED",
            "sports_npb_underdog_v1": "SPORTS_ENABLED",
            "sports_underdog_v36": "SPORTS_ENABLED",
            "sports_underdog_v37": "SPORTS_ENABLED",
            "sports_favorite_v40": "SPORTS_ENABLED",
            "sports_tennis_pinnacle_arb_v1": "SPORTS_ENABLED",
            "sports_atp_gs_bo5_chalk_v1": "SPORTS_ENABLED",
            "sports_tennis_bid_imbalance_v1": "SPORTS_ENABLED",
            "sports_ufc_chalk_fade_v1": "SPORTS_ENABLED",
            "sports_tennis_surface_skew_v1": "SPORTS_ENABLED",
            "sports_pinnacle_edge": "SPORTS_ENABLED",
            "sports_v4_edge": "SPORTS_ENABLED",
            "sports_consolidated": "SPORTS_ENABLED",
            "sports_trader": "SPORTS_ENABLED",
        }
        # Per-strategy retirement explanation for dashboard tooltip. Machine-
        # parseable reason (e.g. 'removed_from_loop') still exposed below for
        # backward-compat assertions; detail goes into reason_detail.
        retirement_detail = {
            "shutdown_fade":
                "DEPRECATED (2026-04-15): V1 의 thesis 결함(이질 마켓 단일 heuristic) + "
                "실행 결함(in-memory state 로 재시작 시 중복 진입). 121건 체결로 상한의 13배 초과. "
                "재설계 대기 — docs/future-work/shutdown-fade-redesign.md",
            "sports_liquidity_filtered":
                "Retired (2026-04-15): sports_midshot_v31 과 ticker 100% 중복. "
                "$3 fixed trade size 로 depth filter 가 무력화됨 → 별도 alpha 없이 "
                "paper exposure 만 2배. Live sizing 재설계 시 부활 검토.",
            "sports_consolidated":
                "Retired (2026-04-15): sports_underdog_v31 과 ticker 55/55 중복, "
                "direction 96% 일치. consensus 검사 없는 순수 price filter 라 V31 보다 "
                "median 35분 일찍, $0.06 비싸게 진입 → per-trade edge $1.36 vs V31 $2.36.",
            "sports_trader":
                "Disabled (2026-04-11): V2 consensus/line-movement 전략. 50건 paper 에서 "
                "-$19.45, 63% WR 이지만 계약 비싸서 net edge 0. V3/V31 에 대체. "
                "클래스는 sports_* 전략들의 base 로 남아있음.",
            "maker_preorder":
                "Disabled: 봇 시작 시 ~400 limit orders 를 4초 내 발사해 bankroll 드레인. "
                "env flag 없이 scan loop 에서만 제거. 재설계 필요.",
            "sports_longshot":
                "Disabled (2026-04-17): V3 longshot. Overlap 분석 결과 V31 이 V3 의 "
                "93% 부분집합. V3-only 57건 ROI -34.6% 로 drag. V31 에 일원화.",
            "sports_midshot":
                "Disabled (2026-04-17): V3 midshot. V31 이 V3 의 100% 부분집합 (방향 "
                "100% 일치, entry price 동일). V3-only 54건 ROI -12.8% 로 drag. "
                "V31 paper +21.4% vs V3 +10.1% → V31 로 일원화.",
            "sports_underdog":
                "Disabled (2026-04-17): V3 underdog. V31 이 V3 의 100% 부분집합 "
                "(방향 100% 일치). V3-only 51건 ROI -13.7% 로 drag. V31 paper "
                "+30.8% vs V3 +13.0% → V31 로 일원화.",
            "mentions_trader":
                "Disabled (2026-04-17): Sports v31 이 메인 live 전략으로 승격되어 "
                "mentions 쪽 capital 추가 투입 중단. 기존 Kalshi 포지션 110개 "
                "(~$200 tied) 는 4월 FOMC settle 때 자동 해소. Silent drift 버그 "
                "(limit fill → record_trade 누락으로 DB trades 0행) 있음. "
                "재개 고려 시 버그 fix 우선.",
            "sports_pinnacle_edge":
                "Disabled (2026-04-17): 37 settled -37.5% ROI. 4/16 (80ad42e) "
                "freshness gate 추가됐지만 post-gate 표본 부족. gate 이후 충분한 "
                "데이터 쌓이면 재평가 후 부활 검토.",
            "econ_trader":
                "Archived: CPI/GDP/FED 계열 live 포지션 남아있지만 loop 에서 제거된 "
                "상태. 새 entry 없음, 기존 포지션 settle 대기.",
            "sports_longshot_v32":
                "Retired (2026-04-24): pre-game-only 재분석 결과 -13.9% (n=24) — "
                "v31 longshot +29.3% 대비 한참 열위. Edge-scaled sizing 이 "
                "lucky-shot 패턴에 안 맞음 (적은 winners 를 사이즈로 키우면 noise 증폭).",
            "sports_midshot_v32":
                "Retired (2026-04-24): pre-game-only +2.1% (n=56) → v31 midshot "
                "+18.9% 대비 명백한 열위. Edge-scaling 이 midshot 에서는 effect 없음.",
            "sports_longshot_v33":
                "Retired (2026-04-24): pre-game-only -23.8% (n=23) — 최악. "
                "$12 per-game cap 이 longshot 의 적은 winners 를 우선적으로 차단 "
                "(FIFO 순서 → 좋은 신호도 무차별 cut). v31 +29.3% 와 비교 불가.",
            "sports_midshot_v33":
                "Retired (2026-04-24): pre-game-only -2.8% (n=55). v31 +18.9% 대비 "
                "cap 이 net 손해 — entry 수 줄었는데 나간 것들이 평균 winner 였음.",
            "sports_underdog_v33":
                "Retired (2026-04-24): pre-game-only +21.5% (n=91) → v31 underdog "
                "+19.7% 와 거의 동률. 추가 mechanism (cap) 정당화 안 됨, 단순 v31 유지가 "
                "더 깔끔. v32 underdog (+27.1%) 가 동일 슬롯에서 명백히 우월하므로 거기 집중.",
        }
        # Demotion details for strategies that are NOT retired but have been
        # removed from LIVE_STRATEGIES env (still in main.py loop, paper-only).
        # Used for the DEMOTED badge tooltip on the dashboard so users know
        # why the strategy stopped trading live.
        demotion_detail = {
            "sports_longshot_v35":
                "Demoted to paper 2026-04-28: NBA pre us underdog 패턴 -25% ROI. "
                "현재 NBA PLAYOFF_BLOCKLIST 에도 걸려서 paper 도 0건 (4/30 이후). "
                "NBA 정규시즌 복귀 (~2026-10) 까지 휴면.",
            "sports_midshot_v35":
                "Demoted to paper 2026-04-30: -89% ROI n=51, NBA pre us 모든 변종이 "
                "playoff 에서 손실. 같은 4/28 longshot_v35 demote 패턴. "
                "NBA 정규시즌 복귀까지 휴면.",
            "sports_underdog_v35":
                "Demoted to paper 2026-04-30: NBA pre us 패턴 손실. "
                "ALLOWED_CELLS=(NBA, pre) + PLAYOFF_BLOCKLIST cascade 로 paper 도 정지. "
                "NBA 정규시즌 복귀까지 휴면.",
            "econ_trader":
                "Demoted historical: CPI/GDP/FED 계열 전략 비활성화 (loop 에서 제거). "
                "기존 settled trades 만 DB 에 남음, 신규 entry 없음.",
            "late_close_tail_v1":
                "Demoted 2026-05-21 (LIVE 26분 만에 revert): cap 적용 후 cohort 가 "
                "wipe-zone (NO 0.90+) 내부 — slip+fee 가 paper alpha 흡수. Promo "
                "Config C sim (+$23.63/day) vs paper actual (−$71/25h) 44× 차이로 "
                "sim over-fit 판명. Paper 만 유지하여 데이터 수집 계속.",
        }
        # Per-strategy gate config (entry-price + game phase + region +
        # sport blocklist). Read class-level attributes so frontend renders
        # what's actually in effect without drift risk.
        price_gates: dict = {}
        phase_gates: dict = {}
        regions: dict = {}
        sport_blocks: dict = {}
        playoff_blocks: dict = {}  # PLAYOFF_BLOCKLIST per strategy (NBA suspend etc.)
        playoff_only_set: dict = {}  # PLAYOFF_ONLY=True markers
        allowed_cells: dict = {}
        sport_phases_map: dict = {}
        base_sizes: dict = {}
        min_depths: dict = {}
        max_depths: dict = {}
        depth_dead_zones: dict = {}
        badges: dict = {}  # BADGE_LABEL + BADGE_COLOR class attrs (display config)
        try:
            from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
            from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
            from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
            from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
            from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
            from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
            from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
            from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
            from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy
            from strategies.sports_longshot_v34 import SportsLongshotV34Strategy
            from strategies.sports_midshot_v34 import SportsMidshotV34Strategy
            from strategies.sports_underdog_v34 import SportsUnderdogV34Strategy
            from strategies.sports_longshot_v35 import SportsLongshotV35Strategy
            from strategies.sports_midshot_v35 import SportsMidshotV35Strategy
            from strategies.sports_underdog_v35 import SportsUnderdogV35Strategy
            from strategies.sports_playoff_underdog_v1 import SportsPlayoffUnderdogV1Strategy
            from strategies.sports_playoff_underdog_v1_us import SportsPlayoffUnderdogV1USStrategy
            from strategies.sports_soccer_underdog_v1 import SportsSoccerUnderdogV1Strategy
            from strategies.sports_soccer_weekend_v1 import SportsSoccerWeekendV1Strategy
            from strategies.sports_consensus_aligned_live_v1 import SportsConsensusAlignedLiveV1Strategy
            from strategies.sports_soccer_draw_v1 import SportsSoccerDrawV1Strategy
            from strategies.sports_mls_fade_favorite_v1 import SportsMLSFadeFavoriteV1Strategy
            from strategies.sports_npb_underdog_v1 import SportsNpbUnderdogV1Strategy
            from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
            from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
            from strategies.sports_favorite_v40 import SportsFavoriteV40Strategy
            from strategies.sports_tennis_pinnacle_arb_v1 import SportsTennisPinnacleArbV1Strategy
            from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy
            from strategies.sports_tennis_bid_imbalance_v1 import SportsTennisBidImbalanceV1Strategy
            from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy
            from strategies.sports_tennis_surface_skew_v1 import SportsTennisSurfaceSkewV1Strategy
            from strategies.sports_v4_edge import SportsV4EdgeStrategy
            from strategies.sports_chalk_shadow import SportsChalkShadowStrategy
            _gate_classes = {
                "sports_longshot_v31": SportsLongshotV31Strategy,
                "sports_midshot_v31":  SportsMidshotV31Strategy,
                "sports_underdog_v31": SportsUnderdogV31Strategy,
                "sports_longshot_v32": SportsLongshotV32Strategy,
                "sports_midshot_v32":  SportsMidshotV32Strategy,
                "sports_underdog_v32": SportsUnderdogV32Strategy,
                "sports_longshot_v33": SportsLongshotV33Strategy,
                "sports_midshot_v33":  SportsMidshotV33Strategy,
                "sports_underdog_v33": SportsUnderdogV33Strategy,
                "sports_longshot_v34": SportsLongshotV34Strategy,
                "sports_midshot_v34":  SportsMidshotV34Strategy,
                "sports_underdog_v34": SportsUnderdogV34Strategy,
                "sports_longshot_v35": SportsLongshotV35Strategy,
                "sports_midshot_v35":  SportsMidshotV35Strategy,
                "sports_underdog_v35": SportsUnderdogV35Strategy,
                "sports_playoff_underdog_v1": SportsPlayoffUnderdogV1Strategy,
                "sports_playoff_underdog_v1_us": SportsPlayoffUnderdogV1USStrategy,
                "sports_soccer_underdog_v1": SportsSoccerUnderdogV1Strategy,
                "sports_soccer_weekend_v1": SportsSoccerWeekendV1Strategy,
                "sports_consensus_aligned_live_v1": SportsConsensusAlignedLiveV1Strategy,
                "sports_soccer_draw_v1": SportsSoccerDrawV1Strategy,
                "sports_mls_fade_favorite_v1": SportsMLSFadeFavoriteV1Strategy,
                "sports_npb_underdog_v1": SportsNpbUnderdogV1Strategy,
                "sports_underdog_v36": SportsUnderdogV36Strategy,
                "sports_underdog_v37": SportsUnderdogV37Strategy,
                "sports_favorite_v40": SportsFavoriteV40Strategy,
                "sports_tennis_pinnacle_arb_v1": SportsTennisPinnacleArbV1Strategy,
                "sports_atp_gs_bo5_chalk_v1":    SportsAtpGsBo5ChalkV1Strategy,
                "sports_tennis_bid_imbalance_v1": SportsTennisBidImbalanceV1Strategy,
                "sports_ufc_chalk_fade_v1":      SportsUfcChalkFadeV1Strategy,
                "sports_tennis_surface_skew_v1": SportsTennisSurfaceSkewV1Strategy,
                "sports_v4_edge":      SportsV4EdgeStrategy,
                "sports_chalk_shadow": SportsChalkShadowStrategy,
            }
            default_region = getattr(config, "ODDS_API_REGION", "eu") if config else "eu"
            # Derive a unified per-sport "NBA: pre, MLB: live" view from
            # whichever gate system the strategy uses. Sports ordered NBA,
            # MLB, NHL for consistent display. None = blocked/not traded.
            # Soccer leagues — used as the displayed sport list when the strategy
            # blocks all major US sports (the strategy is soccer-focused).
            _SOCCER_LEAGUES = ("MLS", "EPL", "LAL", "UCL", "BUN")
            _US_MAJOR_SPORTS = ("NBA", "MLB", "NHL")

            def _wnba_tradeable(cls) -> bool:
                """WNBA tradeable iff no gate blocks it (2026-05-13 expansion)."""
                obs_only = getattr(cls, "OBSERVATION_ONLY_SPORTS", frozenset())
                block = getattr(cls, "SPORT_BLOCKLIST", frozenset())
                cells = getattr(cls, "ALLOWED_CELLS", None)
                if "WNBA" in obs_only or "WNBA" in block:
                    return False
                if cells is not None:
                    return any(s == "WNBA" for s, _ in cells)
                return True

            def _sport_phases(cls) -> dict[str, Optional[str]]:
                pre_only = getattr(cls, "PRE_GAME_ONLY", False)
                # Explicit DISPLAY_SPORTS override (preferred path — no heuristic).
                # Each entry: blocked if in SPORT_BLOCKLIST, else pre/pre+live.
                # ALLOWED_CELLS narrows phases (e.g. ("WNBA","pre")).
                display = getattr(cls, "DISPLAY_SPORTS", None)
                if display is not None:
                    block = getattr(cls, "SPORT_BLOCKLIST", frozenset())
                    obs_only = getattr(cls, "OBSERVATION_ONLY_SPORTS", frozenset())
                    cells = getattr(cls, "ALLOWED_CELLS", None)
                    cell_map: dict[str, str] = {}
                    if cells is not None:
                        for sport, phase in cells:
                            cell_map[sport] = (phase if sport not in cell_map
                                               else "pre+live")
                    out: dict[str, Optional[str]] = {}
                    for sport in display:
                        if sport in block or sport in obs_only:
                            out[sport] = None
                        elif cells is not None:
                            out[sport] = cell_map.get(sport)  # None if not in cells
                        else:
                            out[sport] = "pre" if pre_only else "pre+live"
                    return out
                # Fallback: heuristic derivation from ALLOWED_CELLS / SPORT_BLOCKLIST.
                cells = getattr(cls, "ALLOWED_CELLS", None)
                if cells is not None:
                    # Derive base sport list from the cells' sports + US majors
                    # so a soccer-cell strategy shows MLS/EPL/LAL.
                    cell_sports = {s for s, _ in cells}
                    if cell_sports.issubset(set(_SOCCER_LEAGUES)):
                        base = _SOCCER_LEAGUES
                    else:
                        base = _US_MAJOR_SPORTS
                    out: dict[str, Optional[str]] = {s: None for s in base}
                    for sport, phase in cells:
                        out.setdefault(sport, None)
                        out[sport] = phase if out[sport] is None else "pre+live"
                    # WNBA awareness — surface if tradeable but not already in cells.
                    if _wnba_tradeable(cls) and "WNBA" not in out:
                        out["WNBA"] = "pre" if pre_only else "pre+live"
                    return out
                # Legacy path: PRE_GAME_ONLY + SPORT_BLOCKLIST
                block = getattr(cls, "SPORT_BLOCKLIST", frozenset())
                # Soccer detection: blocks all 3 US majors AND blocks WNBA.
                # WNBA-focused strategies (e.g., v37) block US majors too but
                # leave WNBA tradeable — those should show US majors, not soccer.
                is_soccer = (all(s in block for s in _US_MAJOR_SPORTS)
                             and not _wnba_tradeable(cls))
                shown = _SOCCER_LEAGUES if is_soccer else _US_MAJOR_SPORTS
                legacy: dict[str, Optional[str]] = {}
                for s in shown:
                    if s in block:
                        legacy[s] = None
                    elif pre_only:
                        legacy[s] = "pre"
                    else:
                        legacy[s] = "pre+live"
                # WNBA awareness — surface if tradeable per its gates.
                if _wnba_tradeable(cls):
                    legacy["WNBA"] = "pre" if pre_only else "pre+live"
                return legacy

            for name, cls in _gate_classes.items():
                floor = getattr(cls, "MIN_ENTRY_PRICE", None)
                ceiling = getattr(cls, "MAX_ENTRY_PRICE", None)
                dz = getattr(cls, "ENTRY_PRICE_DEAD_ZONE", None)
                # Window gate (chalk_shadow): PRICE_MIN + PRICE_MAX pair
                # that restricts entry to a price band. Only render when
                # BOTH are set to non-default values (sports_trader base
                # has neither).
                p_min = getattr(cls, "PRICE_MIN", None)
                p_max = getattr(cls, "PRICE_MAX", None)
                if floor is not None and ceiling is not None:
                    price_gates[name] = {"type": "band", "floor": floor, "ceiling": ceiling}
                    if dz is not None:
                        price_gates[name]["dead_zone"] = list(dz)
                elif floor is not None:
                    price_gates[name] = {"type": "floor", "floor": floor}
                    if dz is not None:
                        price_gates[name]["type"] = "floor_with_dead_zone"
                        price_gates[name]["dead_zone"] = list(dz)
                elif ceiling is not None:
                    price_gates[name] = {"type": "ceiling", "ceiling": ceiling}
                    if dz is not None:
                        price_gates[name]["dead_zone"] = list(dz)
                elif dz is not None:
                    price_gates[name] = {"type": "dead_zone", "dead_zone": list(dz)}
                elif p_min is not None and p_max is not None:
                    price_gates[name] = {"type": "window", "window": [p_min, p_max]}
                if getattr(cls, "PRE_GAME_ONLY", False):
                    phase_gates[name] = "pre-game only"
                # Region: explicit override wins, else the config default.
                regions[name] = getattr(cls, "REGION_OVERRIDE", None) or default_region
                # Sport blocklist: convert the frozenset to a sorted list for JSON.
                blocklist = getattr(cls, "SPORT_BLOCKLIST", frozenset())
                if blocklist:
                    sport_blocks[name] = sorted(blocklist)
                # Playoff blocklist: sport-specific suspend during their playoff
                # window. Auto-deactivates when sport returns to regular season.
                pb_list = getattr(cls, "PLAYOFF_BLOCKLIST", frozenset())
                if pb_list:
                    playoff_blocks[name] = sorted(pb_list)
                # Playoff-only marker (e.g., sports_playoff_underdog_v1).
                if getattr(cls, "PLAYOFF_ONLY", False):
                    playoff_only_set[name] = True
                # Allowed cells matrix (V3.4+). Sorted for stable rendering.
                cells = getattr(cls, "ALLOWED_CELLS", None)
                if cells is not None:
                    allowed_cells[name] = sorted(
                        [list(c) for c in cells],
                        key=lambda p: (p[0], p[1]),
                    )
                # Unified per-sport phase view for the dashboard.
                sport_phases_map[name] = _sport_phases(cls)
                # Badge config: BADGE_LABEL + BADGE_COLOR class attrs.
                # Each strategy declares its own; eliminates frontend CSS+JS drift.
                badge_label = getattr(cls, "BADGE_LABEL", None)
                badge_color = getattr(cls, "BADGE_COLOR", None)
                if badge_label and badge_color:
                    badges[name] = {"label": badge_label, "color": badge_color}
                # Sizing introspection — base contracts (unscaled) + depth gate.
                # _get_trade_size needs an instance + a fake order_manager. Use
                # signal edge=2.0 so v32's payoff multiplier is exactly 1.0,
                # giving the unscaled base. v32 displays ×0.5-2.0 separately
                # via class.__name__ check on the frontend.
                try:
                    inst = object.__new__(cls)
                    inst._order_manager = type("MockOM", (), {"bankroll": 1e9})()
                    base = cls._get_trade_size(inst, {"edge": 2.0})
                    base_sizes[name] = float(base)
                except Exception:
                    base_sizes[name] = None
                min_depths[name] = float(getattr(cls, "MIN_DEPTH_USDC", 0) or 0)
                # MAX_DEPTH_USDC gate (2026-05-12). Thick-book block.
                max_d = getattr(cls, "MAX_DEPTH_USDC", None)
                if max_d is not None:
                    max_depths[name] = float(max_d)
                # DEPTH_DEAD_ZONE gate (2026-05-12). v31 family $2k-10k.
                dz = getattr(cls, "DEPTH_DEAD_ZONE", None)
                if dz is not None:
                    depth_dead_zones[name] = list(dz)
        except Exception:
            pass  # best-effort — missing gate info shouldn't break the endpoint

        result = {}
        for s in all_strategies:
            mode = "LIVE" if s in live_strategies else "PAPER"
            in_loop = s not in loop_disabled
            flag_attr = flag_map.get(s)
            flag_on = True if (not flag_attr or not config) else bool(getattr(config, flag_attr, True))
            active = in_loop and flag_on
            if active:
                reason = None
                reason_detail = None
            elif s == "shutdown_fade":
                reason = "DEPRECATED — see docs/future-work/shutdown-fade-redesign.md"
                reason_detail = retirement_detail[s]
            elif not in_loop:
                reason = "removed_from_loop"
                reason_detail = retirement_detail.get(s, "Strategy removed from scan loop (no detail available).")
            else:
                reason = f"{flag_attr}=false"
                reason_detail = retirement_detail.get(s, f"Disabled via env flag {flag_attr}=false.")
            # Demoted classification: explicit allowlist of strategies that
            # WERE producing live trades but no longer do (removed from
            # LIVE_STRATEGIES env or scan loop). Excludes paper-only experiments
            # that never went live (e.g. market_maker, v34 paper trio).
            DEMOTED_FROM_LIVE = {
                "sports_longshot_v35",
                "sports_midshot_v35",
                "sports_underdog_v35",
                "mentions_trader",
                "econ_trader",
                "late_close_tail_v1",
            }
            is_demoted = s in DEMOTED_FROM_LIVE
            # Paper-twin flag: LIVE strategies that ALSO record paper-twin
            # trades (1:1 alongside every live entry). Dashboard uses this
            # to render the strategy in BOTH the LIVE and PAPER perf
            # sections so users can compare paper-vs-live execution.
            try:
                from core.order_manager import PAPER_TWIN_STRATEGIES
                has_paper_twin = s in PAPER_TWIN_STRATEGIES
            except Exception:
                has_paper_twin = False
            result[s] = {"mode": mode, "active": active, "reason": reason,
                         "reason_detail": reason_detail, "archived": s in archived_set,
                         "demoted": is_demoted,
                         "demotion_detail": demotion_detail.get(s) if is_demoted else None,
                         "has_paper_twin": has_paper_twin,
                         "price_gate": price_gates.get(s),
                         "phase_gate": phase_gates.get(s),
                         "region": regions.get(s),
                         "sport_blocklist": sport_blocks.get(s),
                         "playoff_blocklist": playoff_blocks.get(s),
                         "playoff_only": playoff_only_set.get(s, False),
                         "allowed_cells": allowed_cells.get(s),
                         "sport_phases": sport_phases_map.get(s),
                         "base_size": base_sizes.get(s),
                         "min_depth": min_depths.get(s),
                         "max_depth": max_depths.get(s),
                         "depth_dead_zone": depth_dead_zones.get(s),
                         "badge": badges.get(s),
                         "last_fire_at": last_fire_at.get(s)}
        return result

    @app.get("/api/settlement-history")
    async def get_settlement_history():
        """Return win/loss stats from Kalshi settlement data."""
        if not kalshi_client:
            return {"sports": {}, "spread": {}, "mentions": {}}
        import asyncio
        loop = asyncio.get_event_loop()
        settlements = await loop.run_in_executor(None, kalshi_client.get_settlements)

        def categorize(ticker):
            """Map ticker → strategy name. Keep in sync with
            _categorize_live_ticker above and ticker patterns used by
            each strategy's scan loop."""
            if any(s in ticker for s in ["WNBAGAME", "NBAGAME", "MLBGAME", "NHLGAME",
                                          "NPBGAME", "KBOGAME",
                                          "MLSGAME", "EPLGAME", "LALIGAGAME",
                                          "UCLGAME", "BUNDESLIGAGAME"]):
                return "sports_trader"
            if any(s in ticker for s in ["KXBTC", "KXETH", "KXSOL", "KXXRP"]):
                return "spread_scanner"
            if "FEDMENTION-26APR-SHUT" in ticker or "NUMSHUTDOWNS" in ticker:
                return "shutdown_fade"
            if "FEDMENTION" in ticker:
                return "mentions_trader"
            if any(s in ticker for s in ["KXCPI", "KXGDP"]) or (
                ticker.startswith("KXFED") and "MENTION" not in ticker
            ):
                return "econ_trader"
            return "other"

        categories = [
            "sports_trader", "spread_scanner", "mentions_trader",
            "econ_trader", "shutdown_fade", "other",
        ]
        stats = {}
        for cat in categories:
            stats[cat] = {"wins": 0, "losses": 0, "revenue": 0, "cost": 0, "fees": 0, "trades": []}

        for s in settlements:
            ticker = s.get("ticker", "")
            cat = categorize(ticker)
            revenue = float(s.get("revenue", 0)) / 100  # cents to dollars
            yes_cost = float(s.get("yes_total_cost_dollars", "0"))
            no_cost = float(s.get("no_total_cost_dollars", "0"))
            cost = yes_cost + no_cost
            fees = float(s.get("fee_cost", "0"))
            net = revenue - cost - fees
            result = s.get("market_result", "")

            if revenue > cost:
                stats[cat]["wins"] += 1
            elif cost > 0:
                stats[cat]["losses"] += 1

            stats[cat]["revenue"] += revenue
            stats[cat]["cost"] += cost
            stats[cat]["fees"] += fees
            stats[cat]["trades"].append({
                "ticker": ticker,
                "result": result,
                "revenue": round(revenue, 2),
                "cost": round(cost, 2),
                "fees": round(fees, 2),
                "net": round(net, 2),
                "settled_time": s.get("settled_time", ""),
            })

        # Summary without individual trades
        summary = {}
        for cat, d in stats.items():
            total = d["wins"] + d["losses"]
            summary[cat] = {
                "wins": d["wins"],
                "losses": d["losses"],
                "win_rate": round(d["wins"] / total * 100) if total > 0 else 0,
                "total_revenue": round(d["revenue"], 2),
                "total_cost": round(d["cost"], 2),
                "total_fees": round(d["fees"], 2),
                "net_pnl": round(d["revenue"] - d["cost"] - d["fees"], 2),
                "trades": d["trades"],
            }
        return summary

    @app.get("/api/equity-curve")
    async def get_equity_curve(days: int = Query(30, ge=1, le=180)):
        """Daily cumulative PnL per strategy over the last `days`.
        Settled trades only (paper_settled or settled). Bucket by entry-day
        UTC — close enough for a sparkline; matches the daily-bucket lens
        the rest of the dashboard already uses."""
        import aiosqlite
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """SELECT strategy,
                          SUBSTR(timestamp, 1, 10) AS day,
                          SUM(pnl) AS daily_pnl
                   FROM trades
                   WHERE status IN ('paper_settled', 'settled')
                     AND timestamp >= ?
                   GROUP BY strategy, day
                   ORDER BY strategy, day""",
                (cutoff,),
            )
            rows = await cursor.fetchall()
        out: dict[str, list] = {}
        cum: dict[str, float] = {}
        for r in rows:
            strat = r["strategy"]
            cum[strat] = cum.get(strat, 0.0) + float(r["daily_pnl"] or 0)
            out.setdefault(strat, []).append({
                "day": r["day"],
                "pnl": round(float(r["daily_pnl"] or 0), 2),
                "cum_pnl": round(cum[strat], 2),
            })
        return out

    @app.get("/api/positions-edge")
    async def get_positions_edge():
        """Live Kalshi positions augmented with most-recent observer edge.
        Joins on ticker via the latest odds_observations row — gives the
        user a "is this trade still +EV right now?" view without leaving
        the dashboard. Edge = pinnacle_yes_prob - kalshi_yes_ask (signed,
        YES-side); flips sign for NO positions in the frontend."""
        if not kalshi_client:
            return []
        import asyncio, aiosqlite
        loop = asyncio.get_event_loop()
        try:
            positions = await loop.run_in_executor(None, kalshi_client.get_positions)
        except Exception:
            positions = []
        live_pos = [p for p in positions if float(p.get("position_fp", 0) or 0) != 0]
        if not live_pos:
            return []
        tickers = list({p.get("ticker", "") for p in live_pos if p.get("ticker")})
        if not tickers:
            return live_pos
        # Latest observer row per ticker (within last 30 min — older than that
        # is stale enough we'd rather show null than mislead)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        latest: dict[str, dict] = {}
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            placeholders = ",".join("?" * len(tickers))
            cursor = await conn.execute(
                f"""SELECT o.kalshi_ticker, o.kalshi_yes_ask, o.kalshi_no_ask,
                          o.us_yes_prob, o.eu_yes_prob, o.pinnacle_yes_prob,
                          o.timestamp, o.minutes_to_start, o.phase
                   FROM odds_observations o
                   INNER JOIN (
                       SELECT kalshi_ticker, MAX(timestamp) AS mx
                       FROM odds_observations
                       WHERE kalshi_ticker IN ({placeholders})
                         AND timestamp >= ?
                       GROUP BY kalshi_ticker
                   ) m ON o.kalshi_ticker = m.kalshi_ticker
                      AND o.timestamp = m.mx""",
                (*tickers, cutoff),
            )
            for r in await cursor.fetchall():
                latest[r["kalshi_ticker"]] = dict(r)
        # Also pull entry trade for context (most recent live-side trade per
        # ticker — might mismatch positions sourced from earlier sessions, but
        # good enough for an "entry @ X" hint).
        entry_by_ticker: dict[str, dict] = {}
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            placeholders = ",".join("?" * len(tickers))
            cursor = await conn.execute(
                f"""SELECT market_id, strategy, price, direction, timestamp
                   FROM trades
                   WHERE market_id IN ({placeholders})
                     AND status NOT LIKE 'paper%'
                   ORDER BY timestamp DESC""",
                tickers,
            )
            for r in await cursor.fetchall():
                if r["market_id"] not in entry_by_ticker:
                    entry_by_ticker[r["market_id"]] = dict(r)
        out = []
        for p in live_pos:
            t = p.get("ticker", "")
            obs = latest.get(t)
            entry = entry_by_ticker.get(t)
            pos_fp = float(p.get("position_fp", 0) or 0)
            side = "YES" if pos_fp > 0 else "NO"
            edge = None
            cur_ask = None
            if obs:
                pin = obs.get("pinnacle_yes_prob")
                yes_ask = obs.get("kalshi_yes_ask")
                no_ask = obs.get("kalshi_no_ask")
                if pin is not None:
                    if side == "YES" and yes_ask is not None:
                        edge = round(float(pin) - float(yes_ask), 4)
                        cur_ask = float(yes_ask)
                    elif side == "NO" and no_ask is not None:
                        edge = round((1.0 - float(pin)) - float(no_ask), 4)
                        cur_ask = float(no_ask)
            out.append({
                **p,
                "side": side,
                "entry_price": entry.get("price") if entry else None,
                "entry_strategy": entry.get("strategy") if entry else None,
                "entry_ts": entry.get("timestamp") if entry else None,
                "current_ask": cur_ask,
                "edge_now": edge,
                "obs_age_s": (
                    int((datetime.now(timezone.utc)
                        - datetime.fromisoformat(obs["timestamp"].replace("Z", "+00:00"))
                        ).total_seconds())
                    if obs else None
                ),
            })
        return out

    @app.get("/api/observer-signals")
    async def get_observer_signals(
        limit: int = Query(20, ge=1, le=100),
        min_edge: float = Query(0.05, ge=0.0, le=1.0),
        minutes: int = Query(60, ge=5, le=1440),
    ):
        """Recent observer-side signals with high pinnacle edge that may or
        may not have fired (selection-bias monitor — see memory `Observer
        replay before gate changes`). Returns rows where the absolute
        difference between pinnacle YES prob and Kalshi YES ask exceeds
        `min_edge`. Joined to trades to flag whether we actually entered
        within ±10min on the same ticker."""
        import aiosqlite
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        hidden_ph = ",".join("?" * len(_DASHBOARD_HIDDEN_SPORTS))
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                f"""SELECT timestamp, kalshi_ticker, sport, phase,
                          minutes_to_start,
                          kalshi_yes_ask, kalshi_no_ask,
                          us_yes_prob, eu_yes_prob, pinnacle_yes_prob,
                          pinnacle_age_s
                   FROM odds_observations
                   WHERE timestamp >= ?
                     AND pinnacle_yes_prob IS NOT NULL
                     AND kalshi_yes_ask IS NOT NULL
                     AND ABS(pinnacle_yes_prob - kalshi_yes_ask) >= ?
                     AND (sport IS NULL OR sport NOT IN ({hidden_ph}))
                   ORDER BY ABS(pinnacle_yes_prob - kalshi_yes_ask) DESC
                   LIMIT ?""",
                (cutoff, min_edge, *_DASHBOARD_HIDDEN_SPORTS, limit),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
            # Annotate "fired" — any trade on same ticker within ±10 min.
            fired = set()
            for r in rows:
                t_iso = r["timestamp"]
                cur = await conn.execute(
                    """SELECT 1 FROM trades
                       WHERE market_id = ?
                         AND ABS(strftime('%s', timestamp) - strftime('%s', ?)) <= 600
                       LIMIT 1""",
                    (r["kalshi_ticker"], t_iso),
                )
                if await cur.fetchone():
                    fired.add((r["timestamp"], r["kalshi_ticker"]))
        for r in rows:
            r["edge"] = round(float(r["pinnacle_yes_prob"]) - float(r["kalshi_yes_ask"]), 4)
            r["fired"] = (r["timestamp"], r["kalshi_ticker"]) in fired
        return rows

    @app.get("/api/strategy-gates")
    async def get_strategy_gates():
        """Current gate configuration for each Phase 1 crypto trader.

        Snapshot of the config knobs that decide whether a fresh observer
        signal becomes a live trade. Used by the dashboard so you can see
        at-a-glance what bands/depth thresholds are active without SSH'ing
        in to grep `core/config.py`.

        Values are read from the live `config` object — they reflect the
        current process's env vars, not whatever `.env` says on disk."""
        if config is None:
            return {"strategies": [], "as_of": datetime.now(timezone.utc).isoformat()}

        # (display_name, attr_prefix, gates_spec, has_depth_gate)
        # gates_spec = list of (label, format_fn). Format fn reads from config.
        SPECS = [
            ("deribit_tail_fade_v1",  "DERIBIT_TAIL_FADE",
             "BUY NO on Kalshi tail strike when ya overprices vs Deribit lognormal",
             True),
            ("deribit_tail_fade_high_v1", "DERIBIT_TAIL_FADE_HIGH",
             "Sister of tail_fade_v1 for high-ya near-expiry near-strike zone (paper)",
             True),
            ("late_close_tail_v1",    "LATE_CLOSE_TAIL",
             "Tail fade variant for near-close cohort",
             True),
            ("deribit_atm_fade_v1",   "DERIBIT_ATM_FADE",
             "Inverse of tail_fade for high-ya (ITM) strikes",
             True),
            ("cme_basis_fade_v1",     "CME_BASIS_FADE",
             "BUY NO when Kalshi pricing diverges from CME futures basis, "
             "fee-positive ya band only",
             True),
            ("open_no_touch_v1",      "OPEN_NO_TOUCH",
             "BUY NO at event open on ATM±1% buckets",
             False),
            ("atm_hold_v1",           "ATM_HOLD",
             "BUY YES on ATM bucket near close",
             False),
            ("bid_imbalance_v1",      "BID_IMBALANCE",
             "BUY predicted side on top-of-book imbalance, rich-end only",
             False),
            ("bid_imbalance_fade_v1", "BID_IMBALANCE_FADE",
             "Flipped bid_imbalance for low-ya zone",
             False),
        ]

        live_set = config.LIVE_STRATEGIES if hasattr(config, "LIVE_STRATEGIES") else set()
        out = []
        for name, prefix, desc, has_depth in SPECS:
            def g(k, default=None):
                return getattr(config, f"{prefix}_{k}", default)
            gates = []
            size = g("SIZE_USDC")
            if size is not None:
                gates.append({"label": "size", "value": f"${size:g}"})
            min_ya = g("MIN_YA")
            max_ya = g("MAX_YA")
            if min_ya is not None or max_ya is not None:
                lo = "—" if min_ya is None else f"{min_ya:.2f}"
                hi = "—" if max_ya is None else f"{max_ya:.2f}"
                gates.append({"label": "ya band", "value": f"{lo} ≤ ya ≤ {hi}"})
            min_price = g("MIN_PRICE")
            if min_price is not None:
                gates.append({"label": "min price", "value": f"≥ {min_price:.2f}"})
            if has_depth:
                mfc = g("MIN_FIRE_CONTRACTS")
                if mfc is not None:
                    gates.append({"label": "depth", "value": f"≥ {mfc:g} contracts"})
            max_fires = g("MAX_FIRES_PER_EVENT")
            if max_fires is not None:
                gates.append({"label": "fires/event", "value": f"≤ {max_fires:g}"})
            fresh = g("SIGNAL_FRESHNESS_S")
            if fresh is not None:
                gates.append({"label": "freshness", "value": f"≤ {fresh:g}s"})
            out.append({
                "name": name,
                "live": name in live_set,
                "desc": desc,
                "gates": gates,
            })
        return {
            "strategies": out,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/regime")
    async def get_regime():
        """Current trading-universe state — sport phase mix, playoff flag,
        ticker count last hour. Sourced from odds_observations (1-min cadence,
        all open sports markets, gate-independent), so this reflects what the
        scanner *sees*, not what fired.

        INDEXED BY idx_obs_time forces the optimizer to range-scan the
        timestamp index first (3-4k rows in the last hour) instead of
        scanning the entire idx_obs_sport_phase B-tree — without the hint
        SQLite picked the wrong index and the query took ~10s."""
        import aiosqlite
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        hidden_ph = ",".join("?" * len(_DASHBOARD_HIDDEN_SPORTS))
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                f"""SELECT sport,
                          phase,
                          MAX(is_playoff) AS any_playoff,
                          COUNT(DISTINCT kalshi_ticker) AS tickers
                   FROM odds_observations INDEXED BY idx_obs_time
                   WHERE timestamp >= ?
                     AND (sport IS NULL OR sport NOT IN ({hidden_ph}))
                   GROUP BY sport, phase
                   ORDER BY sport, phase""",
                (cutoff, *_DASHBOARD_HIDDEN_SPORTS),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
        # Aggregate per sport — seed with all tracked sports at 0 so
        # leagues between game days (e.g. KBO when Odds API hasn't yet
        # published the next-day fixtures) still appear on the card
        # rather than silently disappearing.
        by_sport: dict[str, dict] = {
            sp: {"sport": sp, "phases": {}, "tickers": 0, "playoff": False}
            for sp in _DASHBOARD_TRACKED_SPORTS
            if sp not in _DASHBOARD_HIDDEN_SPORTS
        }
        for r in rows:
            sp = r["sport"] or "?"
            d = by_sport.setdefault(sp, {"sport": sp, "phases": {}, "tickers": 0,
                                         "playoff": False})
            d["phases"][r["phase"] or "?"] = int(r["tickers"])
            d["tickers"] += int(r["tickers"])
            if r["any_playoff"]:
                d["playoff"] = True
        # Sort: active (>0) first by ticker count desc, then idle (=0)
        # alphabetically so the dashboard shows the busy leagues first.
        sports_sorted = sorted(
            by_sport.values(),
            key=lambda s: (s["tickers"] == 0, -s["tickers"], s["sport"]),
        )
        return {"sports": sports_sorted,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "window_minutes": 60}

    @app.get("/api/crypto-observer")
    async def get_crypto_observer(hours: int = Query(168, ge=1, le=720)):
        """Per-observer fire rate, settled count, WR + 95% binomial CI.

        Phase 0 prevalence scanner for the 8-hypothesis crypto observer
        portfolio ([[crypto_observer_portfolio_2026-05-13]]). Looks back
        `hours` (default 168 = 7 days). actual_outcome is populated by
        scripts/crypto_observer_settlement_backfill.py — rows still NULL
        are "open" (market hasn't settled yet).

        For observers promoted to Phase 1 (paper traders that emit real
        paper market_orders), each row also includes `phase1_strategy`,
        `phase1_paper_trades`, `phase1_paper_settled`, `phase1_paper_wins`,
        `phase1_paper_pnl`. See [[crypto_observer_phase1_promotion_2026-05-14]].
        """
        import aiosqlite
        import math
        # Observer name → list of Phase 1 strategy names sharing that observer.
        # 2026-05-21: extended from 1:1 dict to 1:N list to support sister
        # traders gated on disjoint ya zones of the same observer (e.g.,
        # deribit_tail_skew → [low gate parent, high gate sister]). Rows are
        # expanded one-per-strategy below; Phase 0 columns are identical
        # across siblings (one observer → one signal pool).
        PHASE1_BY_OBSERVER = {
            "deribit_tail_skew":  ["deribit_tail_fade_v1", "deribit_tail_fade_high_v1"],
            "bid_imbalance":      ["bid_imbalance_v1"],
            "bid_imbalance_fade": ["bid_imbalance_fade_v1"],
            "open_no_touch":      ["open_no_touch_v1"],
            "atm_hold":           ["atm_hold_v1"],
            "late_close_tail":    ["late_close_tail_v1"],
            "deribit_atm_skew":   ["deribit_atm_fade_v1"],
            "cme_basis_fade":     ["cme_basis_fade_v1"],
            # Direct traders that write Phase 0 signals under their own name
            # (observer == phase 1 strategy). See
            # [[crypto_fair_value_mid_v1_2026-05-19]].
            "crypto_fair_value":        ["crypto_fair_value"],
            "crypto_fair_value_mid_v1": ["crypto_fair_value_mid_v1"],
        }
        # All known observers — seed so observers that haven't fired in the
        # window still render with n=0 instead of being silently dropped.
        # Order here drives the default rendering order in the dashboard.
        KNOWN_OBSERVERS = (
            "tail_decay", "atm_fade", "cluster_sum", "bvt_consistency",
            "spot_drift_momentum", "open_widening", "deribit_tail_skew",
            "bid_imbalance",
            "open_no_touch", "atm_hold", "bid_imbalance_fade",
            "late_close_tail", "deribit_atm_skew",
            "mid_otm_tail",
            "btc_eth_lead_lag", "cross_asset_corr_break", "kalshi_book_lag",
            # Successor to retired crypto_fair_value Phase 0 logging.
            "lognormal_fair_dev",
            # Full-ladder YES-overpricing (sum_ya ≥ 1.10, sell-YES basket).
            # Redesigned 5/21 — original 4-kind logic retired after 0% WR.
            "strike_ladder_literal",
            # Historical (retired strategies; rows pre-2026-05-20 may still match).
            "crypto_fair_value", "crypto_fair_value_mid_v1",
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """SELECT observer,
                          COUNT(*) AS n_total,
                          SUM(CASE WHEN actual_outcome IS NULL THEN 1 ELSE 0 END) AS n_open,
                          SUM(CASE WHEN actual_outcome IS NOT NULL THEN 1 ELSE 0 END) AS n_settled,
                          SUM(CASE WHEN actual_outcome = predicted_outcome THEN 1 ELSE 0 END) AS wins,
                          MAX(ts) AS last_ts
                   FROM crypto_observer_signals
                   WHERE ts >= ?
                   GROUP BY observer
                   ORDER BY observer ASC""",
                (cutoff,),
            )
            rows = [dict(r) for r in await cur.fetchall()]

            # Seed any known observer that didn't appear in the window
            # (zero signals) so the dashboard always lists the full set.
            by_name = {r["observer"]: r for r in rows}
            for name in KNOWN_OBSERVERS:
                if name not in by_name:
                    rows.append({
                        "observer": name, "n_total": 0, "n_open": 0,
                        "n_settled": 0, "wins": 0, "last_ts": None,
                    })
            rows.sort(key=lambda r: r["observer"])

            # Per-Phase-1-strategy paper trade stats from trades table.
            phase1_stats: dict = {}
            if PHASE1_BY_OBSERVER:
                strat_names = [s for v in PHASE1_BY_OBSERVER.values() for s in v]
                placeholders = ",".join("?" * len(strat_names))
                cur2 = await conn.execute(
                    f"""SELECT strategy,
                              COUNT(*) AS n_trades,
                              SUM(CASE WHEN status='paper_settled' THEN 1 ELSE 0 END) AS n_settled,
                              SUM(CASE WHEN status='paper_settled' AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
                              ROUND(COALESCE(SUM(pnl), 0), 2) AS pnl_sum,
                              ROUND(COALESCE(SUM(CASE WHEN status='paper_settled' THEN size ELSE 0 END), 0), 2) AS settled_stake
                       FROM trades
                       WHERE strategy IN ({placeholders}) AND timestamp >= ?
                       GROUP BY strategy""",
                    (*strat_names, cutoff),
                )
                for r in await cur2.fetchall():
                    phase1_stats[r["strategy"]] = dict(r)

        # Phase 0 columns (signals/WR/CI) are observer-level so compute once
        # per observer, then expand into one output row per Phase 1 strategy
        # (or one row with no Phase 1 attribution if the observer isn't
        # promoted yet).
        expanded: list[dict] = []
        for r in rows:
            settled = r["n_settled"] or 0
            wins = r["wins"] or 0
            if settled > 0:
                wr = wins / settled
                # Wilson 95% CI — better than normal approximation at small n.
                z = 1.96
                denom = 1.0 + z * z / settled
                center = (wr + z * z / (2.0 * settled)) / denom
                margin = (z * math.sqrt(
                    (wr * (1.0 - wr) + z * z / (4.0 * settled)) / settled
                )) / denom
                r["wr"] = round(wr, 4)
                r["wr_ci_lo"] = round(max(0.0, center - margin), 4)
                r["wr_ci_hi"] = round(min(1.0, center + margin), 4)
            else:
                r["wr"] = None
                r["wr_ci_lo"] = None
                r["wr_ci_hi"] = None

            strat_names = PHASE1_BY_OBSERVER.get(r["observer"], [])
            if not strat_names:
                r["phase1_strategy"] = None
                expanded.append(r)
                continue
            for strat_name in strat_names:
                rr = dict(r)
                rr["phase1_strategy"] = strat_name
                st = phase1_stats.get(strat_name)
                if st:
                    rr["phase1_paper_trades"] = st["n_trades"]
                    rr["phase1_paper_settled"] = st["n_settled"]
                    rr["phase1_paper_wins"] = st["wins"]
                    rr["phase1_paper_pnl"] = st["pnl_sum"]
                    stake = st["settled_stake"] or 0.0
                    rr["phase1_paper_stake"] = stake
                    rr["phase1_paper_roi"] = (
                        round(100.0 * st["pnl_sum"] / stake, 2) if stake > 0 else None
                    )
                else:
                    rr["phase1_paper_trades"] = 0
                    rr["phase1_paper_settled"] = 0
                    rr["phase1_paper_wins"] = 0
                    rr["phase1_paper_pnl"] = 0.0
                    rr["phase1_paper_stake"] = 0.0
                    rr["phase1_paper_roi"] = None
                expanded.append(rr)
        return {
            "observers": expanded,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "window_hours": hours,
        }

    @app.get("/api/odds-usage")
    async def get_odds_usage():
        # Read from the shared module-level cache: every sports_* subclass
        # writes the latest (x-requests-used, remaining, last) headers here on
        # each fetch. The `sports_trader` instance itself is deprecated and no
        # longer runs, so reading its attribute returned stale zeros.
        from strategies.sports_trader import _ODDS_CACHE
        if _ODDS_CACHE:
            _, _, usage = max(_ODDS_CACHE.values(), key=lambda v: v[0])
            return usage
        if sports_trader and hasattr(sports_trader, "odds_api_usage"):
            return sports_trader.odds_api_usage
        return {"used": 0, "remaining": 0, "last": 0}

    @app.get("/api/strategy-monitor/{strategy}")
    async def get_strategy_monitor(strategy: str, since: Optional[str] = Query(None)):
        """LIVE vs paper-twin monitoring for a single strategy. Designed for
        post-LIVE-promotion oversight (cme_basis_fade_v1 5/21 etc.) — returns
        rollback-trigger booleans alongside the underlying numbers so a remote
        agent can decide without re-running SQL.

        `since` = ISO UTC timestamp (default: 24h ago). All counts/stats are
        bounded to (since, now). For a freshly-promoted strategy pass the
        promotion timestamp to scope LIVE history correctly."""
        import aiosqlite
        if not since:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        async def _strategy_stats(conn, status: str) -> dict:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n, "
                "  COALESCE(SUM(pnl),0) AS sum_pnl, "
                "  COALESCE(AVG(pnl),0) AS avg_pnl, "
                "  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, "
                "  SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) AS zeros, "
                "  MIN(pnl) AS worst "
                "FROM trades "
                "WHERE strategy = ? AND status = ? AND timestamp >= ?",
                (strategy, status, since),
            )
            row = await cur.fetchone()
            if not row or row[0] == 0:
                return {"n": 0, "sum_pnl": 0.0, "avg_pnl": 0.0, "wins": 0,
                        "wr_pct": None, "wipe_zero_pct": None, "worst_pnl": None}
            n = int(row[0])
            wins = int(row[3] or 0)
            zeros = int(row[4] or 0)
            return {
                "n": n,
                "sum_pnl": round(float(row[1]), 2),
                "avg_pnl": round(float(row[2]), 3),
                "wins": wins,
                "wr_pct": round(100.0 * wins / n, 1),
                "wipe_zero_pct": round(100.0 * zeros / n, 1),
                "worst_pnl": round(float(row[5]), 2) if row[5] is not None else None,
            }

        async with aiosqlite.connect(database.db_path, timeout=30.0) as conn:
            live = await _strategy_stats(conn, "settled")
            paper = await _strategy_stats(conn, "paper_settled")

            cur = await conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE strategy = ? AND status = 'filled' AND timestamp >= ?",
                (strategy, since),
            )
            row = await cur.fetchone()
            live_unsettled = int(row[0]) if row else 0

            cur = await conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE strategy = ? AND status = 'cancelled' AND timestamp >= ?",
                (strategy, since),
            )
            row = await cur.fetchone()
            live_cancelled = int(row[0]) if row else 0

            cur = await conn.execute(
                "SELECT timestamp, market_id, ROUND(price,3), ROUND(realized_fill_price,3), "
                "  ROUND(pnl,2) "
                "FROM trades "
                "WHERE strategy = ? AND status = 'settled' AND timestamp >= ? AND pnl < 0 "
                "ORDER BY pnl ASC LIMIT 3",
                (strategy, since),
            )
            top_losses = [
                {"ts": r[0], "ticker": r[1], "signal_price": r[2],
                 "realized_price": r[3], "pnl": r[4]}
                for r in await cur.fetchall()
            ]

            cur = await conn.execute(
                "SELECT "
                "  CASE "
                "    WHEN price < 0.70 THEN '<0.70' "
                "    WHEN price < 0.75 THEN '0.70-0.75' "
                "    WHEN price < 0.80 THEN '0.75-0.80' "
                "    WHEN price < 0.85 THEN '0.80-0.85' "
                "    WHEN price < 0.90 THEN '0.85-0.90 (wipe-risk)' "
                "    ELSE '>=0.90 (wipe-zone)' "
                "  END AS bucket, "
                "  COUNT(*) AS n, "
                "  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, "
                "  ROUND(SUM(pnl),2) AS net "
                "FROM trades "
                "WHERE strategy = ? AND status = 'settled' AND timestamp >= ? "
                "GROUP BY bucket ORDER BY bucket",
                (strategy, since),
            )
            price_buckets = [
                {"bucket": r[0], "n": int(r[1]),
                 "wins": int(r[2] or 0),
                 "wr_pct": round(100.0 * (r[2] or 0) / r[1], 1) if r[1] else None,
                 "net": float(r[3] or 0)}
                for r in await cur.fetchall()
            ]

        live_net = live["sum_pnl"]
        live_n = live["n"]
        live_wr = live["wr_pct"]
        wipe_pct = live["wipe_zero_pct"]
        triggers = {
            "net_le_neg_50": live_net <= -50.0,
            "wr_lt_70_at_n_ge_30": (live_n >= 30 and live_wr is not None and live_wr < 70.0),
            "wipe_zero_gt_20pct": (live_n >= 10 and wipe_pct is not None and wipe_pct > 20.0),
        }
        triggers["any_rollback_signal"] = any(triggers.values())

        ratio = None
        if paper["sum_pnl"] and paper["sum_pnl"] != 0:
            ratio = round(live_net / paper["sum_pnl"], 3)

        return {
            "strategy": strategy,
            "since": since,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "live": live,
            "paper_twin": paper,
            "live_vs_paper_ratio": ratio,
            "live_unsettled": live_unsettled,
            "live_cancelled": live_cancelled,
            "fill_rate_pct": (round(100.0 * (live_n + live_unsettled) /
                                   max(1, live_n + live_unsettled + live_cancelled), 1)
                              if (live_n + live_unsettled + live_cancelled) else None),
            "price_distribution": price_buckets,
            "top_losses": top_losses,
            "rollback_triggers": triggers,
        }

    @app.get("/api/clv-trend")
    async def get_clv_trend():
        """CLV trend per strategy — last N entry-vs-closing snapshot deltas.

        For each strategy with CLV data in last 30 days, returns an ordered
        series of CLV values (newest last) for sparkline rendering. Plus
        aggregate stats: 30d avg, 7d avg, n.

        Powers the at-a-glance "is this strategy's alpha still working"
        sparkline column in the dashboard.
        """
        db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
        live_strats = set(getattr(config, "LIVE_STRATEGIES", set()) or set())
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Pull last 30 days of CLV-eligible trades per strategy
        cur.execute("""
            SELECT t.strategy, t.timestamp, t.direction,
                   e.yes_ask, e.no_ask, c.yes_ask, c.no_ask
            FROM trades t
            JOIN clv_snapshots e ON e.trade_id = t.id AND e.snapshot_at = 'entry'
            JOIN clv_snapshots c ON c.trade_id = t.id AND c.snapshot_at = 'closing'
            WHERE t.status IN ('settled','filled')
              AND t.timestamp >= datetime('now','-30 days')
              AND e.yes_ask IS NOT NULL AND c.yes_ask IS NOT NULL
              AND t.strategy LIKE 'sports_%'
            ORDER BY t.timestamp ASC
        """)
        by_strat: dict[str, list] = {}
        for strategy, ts, direction, e_yes, e_no, c_yes, c_no in cur.fetchall():
            clv = (c_yes - e_yes) if direction == "yes" else (c_no - e_no)
            by_strat.setdefault(strategy, []).append({"ts": ts, "clv": round(clv, 4)})
        conn.close()

        rows = []
        for strat, series in by_strat.items():
            n = len(series)
            avg30 = sum(s["clv"] for s in series) / n if n else 0
            recent = [s for s in series if s["ts"] >= datetime.now(timezone.utc).strftime("%Y-%m-%d") and False]
            # Last 7-day window
            cutoff7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            d7 = [s for s in series if s["ts"] >= cutoff7]
            avg7 = sum(s["clv"] for s in d7) / len(d7) if d7 else None
            rows.append({
                "strategy": strat,
                "is_live": strat in live_strats,
                "n_30d": n,
                "avg_clv_30d": round(avg30, 4),
                "n_7d": len(d7),
                "avg_clv_7d": round(avg7, 4) if avg7 is not None else None,
                # Sparkline: last 20 points
                "series": [s["clv"] for s in series[-20:]],
            })
        rows.sort(key=lambda r: (not r["is_live"], -r["avg_clv_30d"]))
        return {"rows": rows}

    @app.get("/api/polymarket/gaps")
    async def get_polymarket_gaps():
        """Live PM vs Kalshi gap monitor for mapped sports markets.

        Returns per-mapping current gap (PM_yes - Kalshi_yes). Highlights
        when |gap| >= XVENUE_MIN_DEVIATION (xvenue strategy would fire).

        Reads cached PM quotes from DB (polymarket_quote_cron refreshes
        every ~1min). Fetches live Kalshi yes_ask per ticker. Sports
        mappings only (skips FOMC/macro).
        """
        import json as _json
        db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
        min_dev = getattr(config, "XVENUE_MIN_DEVIATION", 0.10)

        # Load mapping
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        mapping_path = os.path.join(repo_root, "data", "polymarket_kalshi_mapping.json")
        try:
            with open(mapping_path) as f:
                mapping = _json.load(f)
        except Exception as e:
            return {"error": f"mapping load failed: {e}", "rows": []}

        sports_prefixes = ("KXNBA-", "KXNHL-", "KXMENWORLDCUP-",
                           "KXATPMATCH", "KXWTAMATCH", "KXUFCFIGHT")
        sports_maps = [m for m in mapping
                       if any(m["kalshi_ticker_prefix"].startswith(p) for p in sports_prefixes)]

        # Cached PM quotes
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        pm_data = {}
        for m in sports_maps:
            slug = m["polymarket_slug"]
            cur.execute(
                "SELECT yes_price, best_bid, best_ask, fetched_at, "
                " yes_bid_size, yes_ask_size, no_bid_size, no_ask_size "
                "FROM polymarket_quotes WHERE slug = ?",
                (slug,),
            )
            row = cur.fetchone()
            if row:
                pm_data[slug] = {"yes_price": row[0], "best_bid": row[1],
                                 "best_ask": row[2], "fetched_at": row[3],
                                 "yes_bid_size": row[4], "yes_ask_size": row[5],
                                 "no_bid_size": row[6], "no_ask_size": row[7]}
        conn.close()

        # Fetch live Kalshi prices via shared client (passed to create_app)
        if not kalshi_client:
            return {"error": "kalshi client unavailable", "rows": []}
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        rows = []
        for m in sports_maps:
            slug = m["polymarket_slug"]
            kx = m["kalshi_ticker_prefix"]
            pm = pm_data.get(slug)
            if not pm:
                continue
            try:
                r = await loop.run_in_executor(
                    None, kalshi_client._get, f"/trade-api/v2/markets/{kx}"
                )
                mk = (r or {}).get("market") or {}
                ya = float(mk.get("yes_ask_dollars") or 0)
                if ya <= 0:
                    continue
            except Exception:
                continue
            pm_yes = pm["yes_price"]
            gap = pm_yes - ya
            rows.append({
                "ticker": kx,
                "slug": slug,
                "outcome_match": m.get("outcome_match", "direct"),
                "pm_yes": pm_yes,
                "kalshi_yes": ya,
                "gap": gap,
                "fires_arb": abs(gap) >= min_dev,
                "pm_fetched_at": pm.get("fetched_at"),
                "pm_yes_bid_size": pm.get("yes_bid_size"),
                "pm_yes_ask_size": pm.get("yes_ask_size"),
                "pm_no_bid_size": pm.get("no_bid_size"),
                "pm_no_ask_size": pm.get("no_ask_size"),
            })

        rows.sort(key=lambda r: abs(r["gap"]), reverse=True)
        return {
            "min_deviation": min_dev,
            "n_mappings": len(rows),
            "fires_count": sum(1 for r in rows if r["fires_arb"]),
            "rows": rows,
        }

    @app.get("/api/snapshot")
    async def get_snapshot():
        """Today's at-a-glance snapshot — daily/7d/30d net PnL + activity.

        Returns LIVE-mode stats. Hides paper-twin noise. Designed for the
        top-of-dashboard summary card so user can see "how am I doing"
        in one row.
        """
        db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
        live_strats = set(getattr(config, "LIVE_STRATEGIES", set()) or set())
        if not live_strats:
            return {"error": "LIVE_STRATEGIES empty"}
        placeholders = ",".join("?" * len(live_strats))
        params = list(live_strats)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        def _window(days: int):
            cur.execute(
                f"""SELECT COUNT(*), ROUND(COALESCE(SUM(pnl),0),2),
                        SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
                    FROM trades
                    WHERE status IN ('settled','filled')
                      AND strategy IN ({placeholders})
                      AND timestamp >= datetime('now', '-{days} days')""",
                params,
            )
            n, net, wins = cur.fetchone()
            return {"n": n or 0, "net": float(net or 0),
                    "wins": wins or 0,
                    "wr_pct": round((wins or 0) * 100.0 / n, 1) if n else None}

        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur.execute(
            f"""SELECT COUNT(*), ROUND(COALESCE(SUM(pnl),0),2),
                    SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
                FROM trades
                WHERE status IN ('settled','filled')
                  AND strategy IN ({placeholders})
                  AND timestamp >= ?""",
            params + [today_utc + "T00:00:00"],
        )
        n_t, net_t, wins_t = cur.fetchone()
        today = {"n": n_t or 0, "net": float(net_t or 0),
                 "wins": wins_t or 0,
                 "wr_pct": round((wins_t or 0) * 100.0 / n_t, 1) if n_t else None}

        # Best/worst trade today
        cur.execute(
            f"""SELECT strategy, pnl FROM trades
                WHERE status IN ('settled','filled')
                  AND strategy IN ({placeholders})
                  AND timestamp >= ?
                ORDER BY pnl DESC LIMIT 1""",
            params + [today_utc + "T00:00:00"],
        )
        best = cur.fetchone()
        cur.execute(
            f"""SELECT strategy, pnl FROM trades
                WHERE status IN ('settled','filled')
                  AND strategy IN ({placeholders})
                  AND timestamp >= ?
                ORDER BY pnl ASC LIMIT 1""",
            params + [today_utc + "T00:00:00"],
        )
        worst = cur.fetchone()

        d7_data = _window(7)
        d30_data = _window(30)

        # 14-day daily cumulative equity curve (LIVE strategies only).
        cur.execute(
            f"""SELECT substr(timestamp,1,10) day, SUM(pnl) daily_pnl
                FROM trades
                WHERE status IN ('settled','filled')
                  AND strategy IN ({placeholders})
                  AND timestamp >= datetime('now','-14 days')
                GROUP BY day ORDER BY day""",
            params,
        )
        equity_daily = []
        cum = 0.0
        for day, daily_pnl in cur.fetchall():
            cum += float(daily_pnl or 0)
            equity_daily.append({"day": day, "pnl": round(float(daily_pnl or 0), 2),
                                  "cum_pnl": round(cum, 2)})
        conn.close()

        return {
            "today_utc": today_utc,
            "live_count": len(live_strats),
            "today": today,
            "d7": d7_data,
            "d30": d30_data,
            "best_today": {"strategy": best[0], "pnl": float(best[1])} if best else None,
            "worst_today": {"strategy": worst[0], "pnl": float(worst[1])} if worst else None,
            "equity_14d": equity_daily,
        }

    def _classify_schedule_gate(gate: dict) -> str:
        """Categorize a schedule gate as 'sports', 'crypto', or 'other'.
        Derived from label + memory fields. Sports patterns checked first
        to avoid 'ladder_crypto' false-matching 'kxbtc' etc.
        """
        text = " ".join([
            gate.get("label", ""),
            gate.get("memory", ""),
            gate.get("details", ""),
        ]).lower()
        sports_keys = (
            "sport", "soccer", "tennis", "ufc", "npb", "kbo", "mlb", "mls",
            "nba", "nhl", "wta", "atp", "underdog", "midshot", "longshot",
            "playoff", "v31", "v32", "v34", "v35", "v36", "v37", "v40",
            "consensus_aligned", "fade_favorite", "chalk_fade", "pinnacle_arb",
            "surface_skew", "gs_bo5", "live_fade",
        )
        crypto_keys = (
            "cme_basis", "deribit", "atm_hold", "atm_fade", "ladder_crypto",
            "late_close", "etf_flow", "settlement_edge", "depth_skew",
            "mid_otm_tail", "cross_venue", "cross_asset", "phase0",
            "kxbtc", "kxeth", "kxxrp", "kxsol", "bid_imbalance",
            "open_no_touch", "universe_expansion", "fed", "fomc",
            "vol_skew", "tail_fade", "lognormal", "fair_value",
        )
        if any(k in text for k in sports_keys):
            return "sports"
        if any(k in text for k in crypto_keys):
            return "crypto"
        return "other"

    @app.get("/api/schedule")
    async def get_schedule():
        """Upcoming eval/promotion gates loaded from data/schedule.json.
        Past gates (date < today UTC) are dropped — the file is treated
        as forward-looking only.  Returns gates sorted by date ascending.

        Each gate is tagged with a derived `category` field: 'sports',
        'crypto', or 'other'. Pattern-matched from label + memory (see
        _classify_schedule_gate).
        """
        import json
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schedule_path = os.path.join(repo_root, "data", "schedule.json")
        try:
            with open(schedule_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {"version": None, "gates": [], "by_category": {}, "error": str(e)}
        today_utc = datetime.now(timezone.utc).date().isoformat()
        future = []
        for g in data.get("gates", []):
            if g.get("date", "") < today_utc:
                continue
            g = dict(g)
            g["category"] = _classify_schedule_gate(g)
            future.append(g)
        future.sort(key=lambda g: (g.get("date", ""), g.get("time_utc", "")))
        by_category: dict[str, list] = {"sports": [], "crypto": [], "other": []}
        for g in future:
            by_category.setdefault(g["category"], []).append(g)
        return {
            "version": data.get("version"),
            "note": data.get("note"),
            "today_utc": today_utc,
            "gates": future,
            "by_category": by_category,
        }

    # Serve static frontend files if they exist
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
