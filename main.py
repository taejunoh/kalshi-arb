import asyncio
import logging
import signal
import sys

from core.config import Config
from core.database import Database
from core.price_feed import PriceFeed
from core.kalshi_client import KalshiClient
from core.market_scanner import MarketScanner
from core.order_manager import OrderManager
from strategies.maker_preorder import MakerPreorderStrategy
from strategies.spread_scanner import SpreadScannerStrategy
from strategies.market_maker import MarketMakerStrategy
from strategies.mentions_trader import MentionsTraderStrategy
from strategies.sports_trader import SportsTraderStrategy
from strategies.sports_longshot import SportsLongshotStrategy
from strategies.sports_midshot import SportsMidshotStrategy
from strategies.sports_underdog import SportsUnderdogStrategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
from strategies.sports_pinnacle_edge import SportsPinnacleEdgeStrategy
from strategies.sports_v4_edge import SportsV4EdgeStrategy
from strategies.econ_trader import EconTraderStrategy
from core.settlement import SettlementChecker
from core.clv_tracker import CLVTracker
from monitoring.logger import setup_logger, TradeLogger
from monitoring.notifier import TelegramNotifier
from monitoring.telegram_error_handler import TelegramErrorHandler
from monitoring.dashboard.api import create_app

logger = logging.getLogger("bot")


class Bot:
    def __init__(self):
        self.config = Config()
        self.db = Database()
        self.price_feed = PriceFeed()
        self.kalshi_client = KalshiClient(self.config)
        self.scanner = MarketScanner(self.kalshi_client)
        self.notifier = TelegramNotifier(
            self.config.TELEGRAM_BOT_TOKEN, self.config.TELEGRAM_CHAT_ID
        )
        # TelegramErrorHandler disabled 2026-04-15 — caused silent startup hang
        # (post-setup_logger, no Bot-starting log, scan loops never run). Needs
        # diagnosis before re-enable. See docs/future-work/telegram-error-handler.md
        # _err_handler = TelegramErrorHandler(self.notifier)
        # _err_handler.setLevel(logging.ERROR)
        # logging.getLogger().addHandler(_err_handler)
        self.order_manager = OrderManager(self.config, self.kalshi_client, self.db, self.notifier)
        self.maker_preorder = MakerPreorderStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.spread_scanner = SpreadScannerStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            price_feed=self.price_feed,
        )
        self.market_maker = MarketMakerStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.mentions_trader = MentionsTraderStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.sports_trader = SportsTraderStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_longshot = SportsLongshotStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot = SportsMidshotStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog = SportsUnderdogStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_longshot_v31 = SportsLongshotV31Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot_v31 = SportsMidshotV31Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog_v31 = SportsUnderdogV31Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_pinnacle_edge = SportsPinnacleEdgeStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_v4_edge = SportsV4EdgeStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.econ_trader = EconTraderStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.settlement = SettlementChecker(self.kalshi_client, self.db, self.order_manager, self.notifier)
        self.clv_tracker = CLVTracker(self.kalshi_client, self.db)
        self.trade_logger = TradeLogger()
        self._running = True

    async def start(self):
        setup_logger("bot")
        await self.db.initialize()

        # Restore bankroll from DB if prior run persisted one (paper mode only —
        # full-live mode overrides below with the actual Kalshi balance).
        await self.order_manager.hydrate_bankroll()

        # Sync bankroll with actual Kalshi balance only in pure-live mode.
        # In mixed mode (TRADING_MODE=paper + some live strategies), the shared
        # order_manager.bankroll is the PAPER running state; overwriting it with
        # Kalshi's real balance wipes paper accounting and causes phantom drains
        # when stale paper_filled trades later settle. Live strategies in mixed
        # mode check real Kalshi balance per-order via _live_limit_order anyway.
        if self.config.LIVE_STRATEGIES and not self.config.is_paper_mode:
            real_balance = self.kalshi_client.get_balance()
            if real_balance is not None:
                self.order_manager.bankroll = real_balance
                logger.info(f"Synced bankroll to Kalshi balance: ${real_balance:.2f}")

        mode = "PAPER" if self.config.is_paper_mode else "LIVE"
        live_strats = ", ".join(self.config.LIVE_STRATEGIES) if self.config.LIVE_STRATEGIES else "none"
        logger.info(f"Bot starting in {mode} mode | Bankroll: ${self.order_manager.bankroll:.2f} | Live: {live_strats}")
        await self.notifier.notify_bot_status("Started", self.order_manager.bankroll)

        # Cancel any resting limit orders from a previous session. v31's
        # limit+TTL poller is an asyncio task that dies on restart, so any
        # still-resting order is an orphan that will fill later without the
        # bot knowing (observed 2026-04-16: a 09:02 order filled at 14:21
        # after a mid-day restart, DB had no record, Kalshi notified the user).
        # All current LIVE_STRATEGIES use short-lived limits, so a blanket
        # cancel is safe; if a market-making strategy is added, revisit.
        self._reconcile_resting_orders()

        if not self.config.is_paper_mode:
            logger.info("Running live mode pre-flight checks...")
            if not self.kalshi_client.preflight_check():
                logger.error("Pre-flight checks failed. Aborting.")
                await self.notifier.notify_error("Pre-flight checks failed. Bot not started.")
                sys.exit(1)
            logger.info("Pre-flight checks passed. Starting live trading.")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        tasks = [
            asyncio.create_task(self._price_feed_task()),
            asyncio.create_task(self.scanner.poll_loop()),
            asyncio.create_task(self.scanner.all_markets_poll_loop()),
            asyncio.create_task(self._strategy_loop()),
            asyncio.create_task(self._dashboard_task()),
            asyncio.create_task(self._settlement_check_task()),
            asyncio.create_task(self._clv_tracker_task()),
            asyncio.create_task(self._daily_reset_task()),
            asyncio.create_task(self._freshness_sample_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")

    async def _price_feed_task(self):
        tasks = []
        for symbol, url in self.config.BINANCE_WS_URLS.items():
            tasks.append(asyncio.create_task(self.price_feed.subscribe(symbol, url)))
        await asyncio.gather(*tasks)

    async def _dashboard_task(self):
        """Run the FastAPI dashboard server with auto-restart watchdog."""
        import uvicorn
        import threading
        for name in ("uvicorn.error", "uvicorn", "uvicorn.access"):
            logging.getLogger(name).setLevel(logging.WARNING)

        while self._running:
            app = create_app(self.db, bankroll=self.order_manager.bankroll,
                           kalshi_client=self.kalshi_client, config=self.config,
                           sports_trader=self.sports_trader)
            started = False
            for port in (8080, 8081, 8082):
                config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
                self._dashboard_server = uvicorn.Server(config)
                try:
                    thread = threading.Thread(target=self._dashboard_server.run, daemon=True)
                    thread.start()
                    await asyncio.sleep(2)
                    if self._dashboard_server.started:
                        logger.info(f"Dashboard running at http://localhost:{port}")
                        started = True
                        # Monitor thread — restart if it dies
                        while self._running and thread.is_alive():
                            await asyncio.sleep(5)
                        if self._running:
                            logger.warning("Dashboard thread died, restarting in 5s...")
                            await asyncio.sleep(5)
                        break
                    else:
                        logger.warning(f"Port {port} failed to bind, trying next...")
                except Exception as e:
                    logger.error(f"Dashboard error on port {port}: {e}")
            if not started:
                logger.error("Dashboard could not bind to any port, retrying in 30s...")
                await asyncio.sleep(30)

    async def _strategy_loop(self):
        strategies = [
            # maker_preorder disabled — fires ~400 limit orders in <4s on
            # startup, draining bankroll. No ENABLED flag in config, so
            # removing from loop is the only way. Priority: sports V3
            # sample collection for Live transition decision.
            # shutdown_fade deprecated — see docs/future-work/shutdown-fade-redesign.md
            ("spread_scanner", self.spread_scanner),
            ("market_maker", self.market_maker),
            ("mentions_trader", self.mentions_trader),
            # sports_trader (consensus) disabled — 50 trades confirmed no edge
            # (-$13.63 net, 63% WR but expensive contracts). V3 comparison
            # continues with longshot/midshot/underdog. Instance still exists
            # for inheritance but scan_and_execute is not called.
            # sports_longshot/midshot/underdog (v3) all disabled 2026-04-17
            # based on overlap analysis: V31 is a strict subset of V3 (100%
            # for midshot/underdog, 93% for longshot) with 100% direction
            # agreement on the shared ticker set and ~0¢ entry-price diff.
            # V3-only signals (50-58 per pair) are uniformly negative ROI:
            #   longshot V3-only:  n=57, ROI -34.6%  (V3 total -12.6%, V31 +6.1%)
            #   midshot  V3-only:  n=54, ROI -12.8%  (V3 total +10.1%, V31 +21.4%)
            #   underdog V3-only:  n=51, ROI -13.7%  (V3 total +13.0%, V31 +30.8%)
            # V31 covers the V3 signal space with no alpha loss. Kill V3 to
            # reclaim bankroll and eliminate duplicate fires.
            # ("sports_longshot", self.sports_longshot),
            # ("sports_midshot", self.sports_midshot),
            # ("sports_underdog", self.sports_underdog),
            ("sports_longshot_v31", self.sports_longshot_v31),
            ("sports_midshot_v31", self.sports_midshot_v31),
            ("sports_underdog_v31", self.sports_underdog_v31),
            # sports_pinnacle_edge disabled 2026-04-17 — 37 settled trades at
            # -37.5% ROI. Freshness gate was added 2026-04-16 (80ad42e) but
            # only a handful of post-gate entries accumulated before shutdown;
            # re-enable only after a fresh evaluation window if signal quality
            # materially changes.
            # ("sports_pinnacle_edge", self.sports_pinnacle_edge),
            ("sports_v4_edge", self.sports_v4_edge),
            # sports_liquidity_filtered removed 2026-04-15 — $3 trade size made
            # depth filter inert, 100% trade overlap with sports_midshot_v31
            # (identical PnL $65.99/n=26). Experiment meaningless until live
            # sizing. See /Users/taejunoh/.claude/plans/drifting-finding-galaxy.md
            # sports_consolidated removed 2026-04-15 — 100% ticker overlap
            # with sports_underdog_v31 (55/55), 96% same direction. Pure
            # price/payoff filter (no edge check) fires ~35min earlier than
            # V31 at $0.06 higher entry → per-trade edge $1.36 vs V31's $2.36.
            # No orthogonal signal. See docs/future-work/v3-v31-follow-ups.md
            ("econ_trader", self.econ_trader),
        ]
        while self._running:
            for name, strategy in strategies:
                try:
                    await strategy.scan_and_execute()
                except Exception as e:
                    logger.error(f"{name} loop error: {e}")
                    await self.notifier.notify_error(f"{name}: {e}")
            await asyncio.sleep(0.2)

    def _reconcile_resting_orders(self):
        """Cancel every resting limit order on Kalshi. Called once at startup.

        Rationale: the _live_limit_ttl_order poller is an asyncio task that
        dies on bot restart, leaving orphan orders that can fill hours later
        without any DB record. Since all current LIVE_STRATEGIES place only
        short-lived (≤60s TTL) limits, any still-resting order predates this
        process and is an orphan by definition.
        """
        try:
            resting = self.kalshi_client.get_resting_orders()
        except Exception as e:
            logger.warning(f"[STARTUP] Failed to list resting orders: {e}")
            return
        if not resting:
            logger.info("[STARTUP] No resting orders to reconcile")
            return
        logger.warning(f"[STARTUP] Cancelling {len(resting)} orphaned resting orders")
        for o in resting:
            oid = o.get("order_id") or ""
            ticker = o.get("ticker", "")
            side = o.get("side", "")
            try:
                ok = self.kalshi_client.cancel_order(oid)
                logger.warning(
                    f"[STARTUP] cancel {oid[:12]} {ticker} {side} -> {ok}"
                )
            except Exception as e:
                logger.error(f"[STARTUP] cancel {oid[:12]} failed: {e}")

    async def _settlement_check_task(self):
        """Periodically simulate paper fills and check settlements."""
        while self._running:
            try:
                await self.settlement.simulate_paper_fills()
                await self.settlement.check_and_settle()
            except Exception as e:
                logger.error(f"Settlement check error: {e}")
            await asyncio.sleep(60)

    async def _clv_tracker_task(self):
        """Capture pre-close snapshots ~T-5min before each sports game
        starts, so CLV+ rate reflects the true closing-line value rather
        than the degenerate settled (1.0/0.0) prices."""
        while self._running:
            try:
                await self.clv_tracker.capture_pre_close_snapshots()
            except Exception as e:
                logger.error(f"CLV tracker error: {e}")
            await asyncio.sleep(60)

    async def _daily_reset_task(self):
        """Reset daily counters and send summary at UTC midnight."""
        while self._running:
            import datetime
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            tomorrow = utc_now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
            seconds_until_midnight = (tomorrow - utc_now).total_seconds()

            await asyncio.sleep(seconds_until_midnight)

            if not self._running:
                break

            # Send daily summary
            daily_pnl = await self.db.get_daily_pnl()
            trades = await self.db.get_trades()
            today_trades = [t for t in trades if t["timestamp"].startswith(utc_now.strftime("%Y-%m-%d"))]
            wins = sum(1 for t in today_trades if t.get("pnl", 0) > 0)
            losses = sum(1 for t in today_trades if t.get("pnl", 0) < 0)

            await self.notifier.notify_daily_summary(
                total_trades=len(today_trades),
                wins=wins, losses=losses,
                daily_pnl=daily_pnl,
                bankroll=self.order_manager.bankroll,
            )

            # Reset daily counters
            self.order_manager.reset_daily()
            logger.info(f"Daily reset complete. Yesterday PnL: ${daily_pnl:.2f}")

    async def _freshness_sample_loop(self):
        """Capture one Odds API freshness sample every 5 minutes."""
        import json as _json
        import os
        from scripts.measure_odds_freshness import sample_once
        out_path = os.path.join(os.path.dirname(__file__), "data", "odds_freshness_log.jsonl")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        tick = 0
        while self._running:
            tick += 1
            try:
                api_key = self.config.ODDS_API_KEY
                if api_key:
                    rows = await sample_once(api_key, tick=tick)
                    with open(out_path, "a") as f:
                        for r in rows:
                            f.write(_json.dumps(r) + "\n")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"freshness sample failed: {e}")
            # Cooperative sleep — break into 5s chunks so shutdown is responsive
            for _ in range(60):
                if not self._running:
                    return
                await asyncio.sleep(5)

    async def shutdown(self):
        logger.info("Shutting down...")
        self._running = False
        open_orders = await self.db.get_open_orders()
        for order in open_orders:
            await self.order_manager.cancel_order(order["order_id"])
            logger.info(f"Cancelled order: {order['order_id']}")
        daily_pnl = await self.db.get_daily_pnl()
        logger.info(f"Final bankroll: ${self.order_manager.bankroll:.2f} | Daily PnL: ${daily_pnl:.2f}")
        await self.notifier.notify_bot_status("Stopped", self.order_manager.bankroll)
        logger.info("Shutdown complete")
        # Gracefully stop dashboard server, then cancel remaining tasks
        if hasattr(self, "_dashboard_server"):
            self._dashboard_server.should_exit = True
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()


if __name__ == "__main__":
    bot = Bot()
    asyncio.run(bot.start())
