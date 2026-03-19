from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
import os


def create_app(database, bankroll: float = 0.0) -> FastAPI:
    app = FastAPI(title="Polymarket Arb Bot Dashboard")

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

    # Serve static frontend files if they exist
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
