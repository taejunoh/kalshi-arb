import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        # Polymarket API
        self.POLY_API_KEY = os.getenv("POLY_API_KEY", "")
        self.POLY_API_SECRET = os.getenv("POLY_API_SECRET", "")
        self.POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")
        self.POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")

        # Trading mode
        self.TRADING_MODE = os.getenv("TRADING_MODE", "paper")
        self.INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))

        # Strategy parameters
        self.SUM_ARB_MIN_GAP = float(os.getenv("SUM_ARB_MIN_GAP", "0.03"))
        self.MAKER_TARGET_SUM = float(os.getenv("MAKER_TARGET_SUM", "0.96"))
        self.KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
        self.MAX_SINGLE_TRADE_PCT = float(os.getenv("MAX_SINGLE_TRADE_PCT", "0.10"))
        self.MAX_DIRECTIONAL_LOSS = float(os.getenv("MAX_DIRECTIONAL_LOSS", "50"))
        self.MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "200"))
        self.MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5"))

        # Monitoring
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        # Binance WebSocket URLs
        self.BINANCE_WS_URLS = {
            "BTC": "wss://stream.binance.com:9443/ws/btcusdt@aggTrade",
            "ETH": "wss://stream.binance.com:9443/ws/ethusdt@aggTrade",
            "SOL": "wss://stream.binance.com:9443/ws/solusdt@aggTrade",
        }

        # Polymarket
        self.CLOB_ENDPOINT = "https://clob.polymarket.com"
        self.POLYGON_CHAIN_ID = 137

    @property
    def is_paper_mode(self) -> bool:
        return self.TRADING_MODE == "paper"

    def estimate_taker_fee(self, price: float) -> float:
        proximity_to_50 = 1 - abs(price - 0.5) * 2
        return 0.0315 * max(proximity_to_50, 0)
