from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
import os


def create_app(database, bankroll: float = 0.0, kalshi_client=None, config=None) -> FastAPI:
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

    @app.get("/api/performance")
    async def get_performance():
        all_trades = await database.get_trades()
        # Always show all strategies, even with 0 trades
        all_strategy_names = [
            "sum_arb", "maker_preorder", "market_maker",
            "shutdown_fade", "spread_scanner", "mentions_trader", "sports_trader",
        ]
        strategies = {s: {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0} for s in all_strategy_names}
        for trade in all_trades:
            strat = trade["strategy"]
            if strat not in strategies:
                strategies[strat] = {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0}
            strategies[strat]["trade_count"] += 1
            strategies[strat]["total_pnl"] += trade.get("pnl", 0)
            strategies[strat]["total_size"] += trade.get("size", 0)
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
            "sum_arb", "maker_preorder", "market_maker",
            "shutdown_fade", "spread_scanner", "mentions_trader", "sports_trader",
        ]
        return {
            s: {"mode": "LIVE" if s in live_strategies else "PAPER"}
            for s in all_strategies
        }

    # Serve static frontend files if they exist
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
