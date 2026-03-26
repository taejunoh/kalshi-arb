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
from strategies.sum_arb import SumArbStrategy
from strategies.maker_preorder import MakerPreorderStrategy
from strategies.shutdown_fade import ShutdownFadeStrategy
from strategies.spread_scanner import SpreadScannerStrategy
from strategies.market_maker import MarketMakerStrategy
from strategies.mentions_trader import MentionsTraderStrategy
from monitoring.logger import setup_logger, TradeLogger
from monitoring.notifier import TelegramNotifier
from monitoring.dashboard.api import create_app

logger = logging.getLogger("bot")


class Bot:
    def __init__(self):
        self.config = Config()
        self.db = Database()
        self.price_feed = PriceFeed()
        self.kalshi_client = KalshiClient(self.config)
        self.scanner = MarketScanner(self.kalshi_client)
        self.order_manager = OrderManager(self.config, self.kalshi_client, self.db)
        self.sum_arb = SumArbStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.maker_preorder = MakerPreorderStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.shutdown_fade = ShutdownFadeStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.spread_scanner = SpreadScannerStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.market_maker = MarketMakerStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.mentions_trader = MentionsTraderStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.trade_logger = TradeLogger()
        self.notifier = TelegramNotifier(
            self.config.TELEGRAM_BOT_TOKEN, self.config.TELEGRAM_CHAT_ID
        )
        self._running = True

    async def start(self):
        setup_logger("bot")
        await self.db.initialize()

        mode = "PAPER" if self.config.is_paper_mode else "LIVE"
        logger.info(f"Bot starting in {mode} mode | Bankroll: ${self.order_manager.bankroll:.2f}")
        await self.notifier.notify_bot_status("Started", self.order_manager.bankroll)

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
            asyncio.create_task(self._daily_reset_task()),
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
        """Run the FastAPI dashboard server in a separate thread."""
        import uvicorn
        import threading
        for name in ("uvicorn.error", "uvicorn", "uvicorn.access"):
            logging.getLogger(name).setLevel(logging.WARNING)
        app = create_app(self.db, bankroll=self.order_manager.bankroll)
        for port in (8080, 8081, 8082):
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
            self._dashboard_server = uvicorn.Server(config)
            try:
                thread = threading.Thread(target=self._dashboard_server.run, daemon=True)
                thread.start()
                # Wait briefly for the server to bind
                await asyncio.sleep(2)
                if self._dashboard_server.started:
                    logger.info(f"Dashboard running at http://localhost:{port}")
                    # Keep this task alive so it doesn't get collected
                    while self._running:
                        await asyncio.sleep(5)
                    return
                else:
                    logger.warning(f"Port {port} failed to bind, trying next...")
            except Exception as e:
                logger.error(f"Dashboard error on port {port}: {e}")
        logger.error("Dashboard could not bind to any port, continuing without dashboard")

    async def _strategy_loop(self):
        strategies = [
            ("sum_arb", self.sum_arb),
            ("maker_preorder", self.maker_preorder),
            ("shutdown_fade", self.shutdown_fade),
            ("spread_scanner", self.spread_scanner),
            ("market_maker", self.market_maker),
            ("mentions_trader", self.mentions_trader),
        ]
        while self._running:
            for name, strategy in strategies:
                try:
                    await strategy.scan_and_execute()
                except Exception as e:
                    logger.error(f"{name} loop error: {e}")
                    await self.notifier.notify_error(f"{name}: {e}")
            await asyncio.sleep(0.2)

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
