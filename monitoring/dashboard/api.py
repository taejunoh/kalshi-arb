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
        strategies = {}
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
        settled = [t for t in all_trades if t.get("status") == "paper_settled"]
        unsettled = [t for t in all_trades if t.get("status") == "paper_filled"]
        total_pnl = sum(t.get("pnl", 0) for t in settled)
        by_strategy = {}
        for t in settled:
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
            "settled_count": len(settled),
            "unsettled_count": len(unsettled),
            "total_realized_pnl": round(total_pnl, 2),
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
