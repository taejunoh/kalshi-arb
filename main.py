import asyncio
import logging
import signal
import sys

from core.config import Config
from core.database import Database
from core.price_feed import PriceFeed
from core.polymarket_client import PolymarketClient
from core.market_scanner import MarketScanner
from core.order_manager import OrderManager
from strategies.sum_arb import SumArbStrategy
from monitoring.logger import setup_logger, TradeLogger

logger = logging.getLogger("bot")


class Bot:
    def __init__(self):
        self.config = Config()
        self.db = Database()
        self.price_feed = PriceFeed()
        self.polymarket_client = PolymarketClient(self.config)
        self.scanner = MarketScanner(self.polymarket_client)
        self.order_manager = OrderManager(self.config, self.polymarket_client, self.db)
        self.sum_arb = SumArbStrategy(
            self.config, self.order_manager, self.scanner, self.polymarket_client
        )
        self.trade_logger = TradeLogger()
        self._running = True

    async def start(self):
        setup_logger("bot")
        await self.db.initialize()

        mode = "PAPER" if self.config.is_paper_mode else "LIVE"
        logger.info(f"Bot starting in {mode} mode | Bankroll: ${self.order_manager.bankroll:.2f}")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        tasks = [
            asyncio.create_task(self._price_feed_task()),
            asyncio.create_task(self.scanner.poll_loop()),
            asyncio.create_task(self._strategy_loop()),
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

    async def _strategy_loop(self):
        while self._running:
            try:
                await self.sum_arb.scan_and_execute()
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")
            await asyncio.sleep(0.2)

    async def shutdown(self):
        logger.info("Shutting down...")
        self._running = False
        open_orders = await self.db.get_open_orders()
        for order in open_orders:
            await self.order_manager.cancel_order(order["order_id"])
            logger.info(f"Cancelled order: {order['order_id']}")
        daily_pnl = await self.db.get_daily_pnl()
        logger.info(f"Final bankroll: ${self.order_manager.bankroll:.2f} | Daily PnL: ${daily_pnl:.2f}")
        logger.info("Shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    bot = Bot()
    asyncio.run(bot.start())
