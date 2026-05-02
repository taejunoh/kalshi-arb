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


_DASHBOARD_HIDDEN_SPORTS = ("KBO", "NPB")
"""Sports excluded from Regime card + Observer signals panel.
Data still collected by the observer (don't touch); just hidden from
the dashboard UI per user request — these leagues are scanned but no
strategy currently trades them, so they were diluting the views."""


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
            "sports_longshot_v32", "sports_midshot_v32", "sports_underdog_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            "sports_longshot_v34", "sports_midshot_v34", "sports_underdog_v34",
            "sports_longshot_v35", "sports_midshot_v35", "sports_underdog_v35",
            "sports_underdog_v36_playoff", "sports_underdog_v36_us",
            "sports_pinnacle_edge", "sports_v4_edge", "sports_chalk_shadow",
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
            return {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0, "wins": 0, "losses": 0,
                    "clv_positive_rate": 0.0, "today": empty_today()}

        def empty_split():
            return {"trade_count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

        SPORTS_PREFIX = "sports_"
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
                    t["settled_count"] += 1  # reached this branch only on settled rows
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
        all_strategies = [
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_longshot", "sports_midshot", "sports_underdog",
            "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
            "sports_longshot_v32", "sports_midshot_v32", "sports_underdog_v32",
            "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
            "sports_longshot_v34", "sports_midshot_v34", "sports_underdog_v34",
            "sports_longshot_v35", "sports_midshot_v35", "sports_underdog_v35",
            "sports_underdog_v36_playoff", "sports_underdog_v36_us",
            "sports_pinnacle_edge", "sports_v4_edge", "sports_chalk_shadow",
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
            "sports_underdog_v36_playoff": "SPORTS_ENABLED",
            "sports_underdog_v36_us": "SPORTS_ENABLED",
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
            from strategies.sports_underdog_v36_playoff import SportsUnderdogV36PlayoffStrategy
            from strategies.sports_underdog_v36_us import SportsUnderdogV36USStrategy
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
                "sports_underdog_v36_playoff": SportsUnderdogV36PlayoffStrategy,
                "sports_underdog_v36_us": SportsUnderdogV36USStrategy,
                "sports_v4_edge":      SportsV4EdgeStrategy,
                "sports_chalk_shadow": SportsChalkShadowStrategy,
            }
            default_region = getattr(config, "ODDS_API_REGION", "eu") if config else "eu"
            # Derive a unified per-sport "NBA: pre, MLB: live" view from
            # whichever gate system the strategy uses. Sports ordered NBA,
            # MLB, NHL for consistent display. None = blocked/not traded.
            def _sport_phases(cls) -> dict[str, Optional[str]]:
                cells = getattr(cls, "ALLOWED_CELLS", None)
                if cells is not None:
                    out: dict[str, Optional[str]] = {"NBA": None, "MLB": None, "NHL": None}
                    for sport, phase in cells:
                        out[sport] = phase if out[sport] is None else "pre+live"
                    return out
                # Legacy path: PRE_GAME_ONLY + SPORT_BLOCKLIST
                block = getattr(cls, "SPORT_BLOCKLIST", frozenset())
                pre_only = getattr(cls, "PRE_GAME_ONLY", False)
                legacy: dict[str, Optional[str]] = {}
                for s in ("NBA", "MLB", "NHL"):
                    if s in block:
                        legacy[s] = None
                    elif pre_only:
                        legacy[s] = "pre"
                    else:
                        legacy[s] = "pre+live"
                return legacy

            for name, cls in _gate_classes.items():
                floor = getattr(cls, "MIN_ENTRY_PRICE", None)
                dz = getattr(cls, "ENTRY_PRICE_DEAD_ZONE", None)
                # Window gate (chalk_shadow): PRICE_MIN + PRICE_MAX pair
                # that restricts entry to a price band. Only render when
                # BOTH are set to non-default values (sports_trader base
                # has neither).
                p_min = getattr(cls, "PRICE_MIN", None)
                p_max = getattr(cls, "PRICE_MAX", None)
                if floor is not None:
                    price_gates[name] = {"type": "floor", "floor": floor}
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
                # Playoff-only marker (e.g., sports_underdog_v36_playoff).
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
            result[s] = {"mode": mode, "active": active, "reason": reason,
                         "reason_detail": reason_detail, "archived": s in archived_set,
                         "price_gate": price_gates.get(s),
                         "phase_gate": phase_gates.get(s),
                         "region": regions.get(s),
                         "sport_blocklist": sport_blocks.get(s),
                         "playoff_blocklist": playoff_blocks.get(s),
                         "playoff_only": playoff_only_set.get(s, False),
                         "allowed_cells": allowed_cells.get(s),
                         "sport_phases": sport_phases_map.get(s),
                         "base_size": base_sizes.get(s),
                         "min_depth": min_depths.get(s)}
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

    @app.get("/api/equity-curve")
    async def get_equity_curve(days: int = Query(30, ge=1, le=180)):
        """Daily cumulative PnL per strategy over the last `days`.
        Settled trades only (paper_settled or settled). Bucket by entry-day
        UTC — close enough for a sparkline; matches the daily-bucket lens
        the rest of the dashboard already uses."""
        import aiosqlite
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(database.db_path) as conn:
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
        async with aiosqlite.connect(database.db_path) as conn:
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
        async with aiosqlite.connect(database.db_path) as conn:
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
        async with aiosqlite.connect(database.db_path) as conn:
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
        async with aiosqlite.connect(database.db_path) as conn:
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
        # Aggregate per sport
        by_sport: dict[str, dict] = {}
        for r in rows:
            sp = r["sport"] or "?"
            d = by_sport.setdefault(sp, {"sport": sp, "phases": {}, "tickers": 0,
                                         "playoff": False})
            d["phases"][r["phase"] or "?"] = int(r["tickers"])
            d["tickers"] += int(r["tickers"])
            if r["any_playoff"]:
                d["playoff"] = True
        return {"sports": list(by_sport.values()),
                "as_of": datetime.now(timezone.utc).isoformat(),
                "window_minutes": 60}

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
