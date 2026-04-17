from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
from zoneinfo import ZoneInfo
import os


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
        if any(s in ticker for s in ["NBAGAME", "MLBGAME", "NHLGAME"]):
            # Sports subclasses (longshot/midshot/underdog) cannot be
            # disambiguated from ticker alone. Attribute to generic
            # sports_trader. Currently none are Live anyway.
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
            "sports_pinnacle_edge", "sports_v4_edge",
        ]

        today_cutoff = _today_cutoff_utc()

        def empty_today():
            return {"trade_count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

        def empty_stats():
            return {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0, "wins": 0, "losses": 0,
                    "clv_positive_rate": 0.0, "today": empty_today()}

        def empty_split():
            return {"trade_count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

        SPORTS_PREFIX = "sports_"
        # Removed strategies — keep historical DB rows but hide from dashboard.
        RETIRED = {"sports_liquidity_filtered", "sports_consolidated",
                   "sports_trader", "econ_trader"}

        def _stats_with_split(strat_name: str) -> dict:
            """Sports strategies get pre/live sub-buckets for minutes_to_start split."""
            s = empty_stats()
            if strat_name.startswith(SPORTS_PREFIX):
                s["pre"] = empty_split()
                s["live"] = empty_split()
            return s

        strategies = {}
        for s in all_strategy_names:
            strategies[s] = {"live": _stats_with_split(s), "paper": _stats_with_split(s)}

        # Paper bucket — from local DB trades (only paper_* statuses)
        for trade in all_trades:
            status = trade.get("status", "")
            if not status.startswith("paper"):
                continue
            strat = trade["strategy"]
            if strat in RETIRED:
                continue
            if strat not in strategies:
                strategies[strat] = {"live": _stats_with_split(strat), "paper": _stats_with_split(strat)}
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
            # Today (since most recent 06:00 ET) — based on entry timestamp
            if trade.get("timestamp", "") >= today_cutoff:
                t = bucket["today"]
                t["trade_count"] += 1
                t["total_pnl"] += pnl
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

        # Live pre/live split — from local DB rows where strategy is in LIVE_STRATEGIES.
        # Main live totals still come from Kalshi API (authoritative for real money);
        # this pass only populates the pre/live sub-buckets which need minutes_to_start
        # metadata that the Kalshi settlements endpoint does not expose.
        live_strategies_cfg = set(config.LIVE_STRATEGIES) if config else set()
        for trade in all_trades:
            strat = trade["strategy"]
            if strat in RETIRED or strat not in live_strategies_cfg:
                continue
            if not strat.startswith(SPORTS_PREFIX):
                continue
            if trade.get("status", "").startswith("paper"):
                # These rows predate Live activation; already counted in paper bucket.
                continue
            if strat not in strategies:
                strategies[strat] = {"live": _stats_with_split(strat), "paper": _stats_with_split(strat)}
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
            try:
                positions = await loop.run_in_executor(None, kalshi_client.get_positions)
            except Exception:
                positions = []
            try:
                settlements = await loop.run_in_executor(None, kalshi_client.get_settlements)
            except Exception:
                settlements = []

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

            # Sports live aggregates: use DB strategy column since Kalshi
            # ticker alone can't distinguish v31 subtypes.
            for trade in all_trades:
                if trade.get("status") != "settled":
                    continue
                strat = trade["strategy"]
                if not strat.startswith(SPORTS_PREFIX) or strat in RETIRED:
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
                    if pnl > 0:
                        t["wins"] += 1
                    elif pnl < 0:
                        t["losses"] += 1

            # Also count live filled (open) as entries in today's sub-bucket — they
            # aren't settled yet so PnL stays 0 until finalization, but the user
            # sees a signal was taken today.
            for trade in all_trades:
                if trade.get("status") != "filled":
                    continue
                if trade.get("timestamp", "") < today_cutoff:
                    continue
                strat = trade["strategy"]
                if not strat.startswith(SPORTS_PREFIX) or strat in RETIRED:
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

        # CLV+ rate per strategy, split by mode so paper vs live cards show
        # independent signal quality (same-strategy live trades get their
        # own clv_snapshots rows now that live settlement records closing).
        for strat_name in strategies:
            for mode in ("paper", "live"):
                try:
                    rate = await database.get_clv_positive_rate(
                        strat_name, days=30, mode=mode
                    )
                except Exception:
                    rate = 0.0
                strategies[strat_name][mode]["clv_positive_rate"] = rate

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
        RETIRED = {"sports_liquidity_filtered", "sports_consolidated",
                   "sports_trader", "econ_trader"}
        for t in paper_settled:
            strat = t["strategy"]
            if strat in RETIRED:
                continue
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
        all_strategies = [
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_longshot", "sports_midshot", "sports_underdog",
            "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
            "sports_pinnacle_edge", "sports_v4_edge",
        ]
        # Strategies removed from main.py _strategy_loop (no env flag to toggle).
        # Keep in sync with main.py; see commits 59f7e44 (sports_trader) and
        # e901f2b (maker_preorder).
        loop_disabled = {"sports_trader", "maker_preorder", "shutdown_fade",
                         "sports_liquidity_filtered", "sports_consolidated",
                         "sports_longshot", "sports_midshot", "sports_underdog",
                         "sports_pinnacle_edge"}
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
            "sports_pinnacle_edge":
                "Disabled (2026-04-17): 37 settled -37.5% ROI. 4/16 (80ad42e) "
                "freshness gate 추가됐지만 post-gate 표본 부족. gate 이후 충분한 "
                "데이터 쌓이면 재평가 후 부활 검토.",
        }
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
            result[s] = {"mode": mode, "active": active, "reason": reason, "reason_detail": reason_detail}
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
            if any(s in ticker for s in ["NBAGAME", "MLBGAME", "NHLGAME"]):
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

    # Serve static frontend files if they exist
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
