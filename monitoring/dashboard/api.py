from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional
import os


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

    @app.get("/api/performance")
    async def get_performance():
        all_trades = await database.get_trades()
        all_strategy_names = [
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_trader", "sports_longshot", "sports_midshot", "sports_underdog",
            "econ_trader",
        ]

        def empty_stats():
            return {"trade_count": 0, "total_pnl": 0.0, "total_size": 0.0, "wins": 0, "losses": 0}

        strategies = {s: {"live": empty_stats(), "paper": empty_stats()} for s in all_strategy_names}
        for trade in all_trades:
            strat = trade["strategy"]
            if strat not in strategies:
                strategies[strat] = {"live": empty_stats(), "paper": empty_stats()}
            is_paper = trade.get("status", "").startswith("paper")
            bucket = strategies[strat]["paper" if is_paper else "live"]
            bucket["trade_count"] += 1
            bucket["total_pnl"] += trade.get("pnl", 0)
            bucket["total_size"] += trade.get("size", 0)
            pnl = trade.get("pnl", 0)
            if pnl > 0:
                bucket["wins"] += 1
            elif pnl < 0:
                bucket["losses"] += 1
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
            "maker_preorder", "market_maker", "shutdown_fade",
            "spread_scanner", "mentions_trader",
            "sports_trader", "sports_longshot", "sports_midshot", "sports_underdog",
            "econ_trader",
        ]
        return {
            s: {"mode": "LIVE" if s in live_strategies else "PAPER"}
            for s in all_strategies
        }

    @app.get("/api/settlement-history")
    async def get_settlement_history():
        """Return win/loss stats from Kalshi settlement data."""
        if not kalshi_client:
            return {"sports": {}, "spread": {}, "mentions": {}}
        import asyncio
        loop = asyncio.get_event_loop()
        settlements = await loop.run_in_executor(None, kalshi_client.get_settlements)

        def categorize(ticker):
            if any(s in ticker for s in ["NBAGAME", "MLBGAME", "NHLGAME"]):
                return "sports"
            if any(s in ticker for s in ["KXBTC", "KXETH", "KXSOL", "KXXRP"]):
                return "spread"
            if "FEDMENTION" in ticker:
                return "mentions"
            return "other"

        stats = {}
        for cat in ["sports", "spread", "mentions", "other"]:
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
        if sports_trader and hasattr(sports_trader, "odds_api_usage"):
            return sports_trader.odds_api_usage
        return {"used": 0, "remaining": 0, "last": 0}

    # Serve static frontend files if they exist
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
