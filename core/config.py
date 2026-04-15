import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        # Kalshi API
        self.KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
        self.KALSHI_RSA_PRIVATE_KEY_PATH = os.getenv("KALSHI_RSA_PRIVATE_KEY_PATH", "")
        self.KALSHI_API_BASE_URL = os.getenv("KALSHI_API_BASE_URL", "https://api.elections.kalshi.com")
        self.KALSHI_FEE_PER_CONTRACT = float(os.getenv("KALSHI_FEE_PER_CONTRACT", "0.07"))

        # Trading mode
        self.TRADING_MODE = os.getenv("TRADING_MODE", "paper")
        self.INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))
        # Per-strategy live override (comma-separated strategy names that trade live)
        self.LIVE_STRATEGIES = set(
            s.strip() for s in os.getenv("LIVE_STRATEGIES", "").split(",") if s.strip()
        )

        # Strategy parameters (unchanged)
        self.SUM_ARB_MIN_GAP = float(os.getenv("SUM_ARB_MIN_GAP", "0.03"))
        self.MAKER_TARGET_SUM = float(os.getenv("MAKER_TARGET_SUM", "0.96"))
        self.KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
        self.MAX_SINGLE_TRADE_PCT = float(os.getenv("MAX_SINGLE_TRADE_PCT", "0.10"))
        self.MAX_DIRECTIONAL_LOSS = float(os.getenv("MAX_DIRECTIONAL_LOSS", "50"))
        self.MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "200"))
        self.MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5"))

        # Market Maker strategy
        self.MM_ENABLED = os.getenv("MM_ENABLED", "false").lower() == "true"
        self.MM_MIN_VOLUME = int(os.getenv("MM_MIN_VOLUME", "1000"))
        self.MM_SPREAD_CENTS = int(os.getenv("MM_SPREAD_CENTS", "4"))
        self.MM_REQUOTE_INTERVAL = int(os.getenv("MM_REQUOTE_INTERVAL", "30"))
        self.MM_MAX_POSITION = float(os.getenv("MM_MAX_POSITION", "200"))
        self.MM_MAX_MARKETS = int(os.getenv("MM_MAX_MARKETS", "3"))

        # Shutdown Fade strategy — DEPRECATED V1, see docs/future-work/shutdown-fade-redesign.md.
        # Flag retained for dashboard/.env 호환성. 값과 무관하게 전략 실행 안 됨 (main.py 에서 제거됨,
        # 인스턴스화 시도 시 RuntimeError). 재설계 후 새 클래스로 부활 예정.
        self.SHUTDOWN_FADE_ENABLED = os.getenv("SHUTDOWN_FADE_ENABLED", "false").lower() == "true"
        self.SHUTDOWN_FADE_THRESHOLD = float(os.getenv("SHUTDOWN_FADE_THRESHOLD", "0.25"))
        self.SHUTDOWN_FADE_SCALE_LEVELS = int(os.getenv("SHUTDOWN_FADE_SCALE_LEVELS", "3"))
        self.SHUTDOWN_FADE_MAX_POSITION = float(os.getenv("SHUTDOWN_FADE_MAX_POSITION", "500"))

        # Value Spread Scanner strategy (crypto-focused)
        self.SPREAD_SCANNER_ENABLED = os.getenv("SPREAD_SCANNER_ENABLED", "false").lower() == "true"
        self.SPREAD_SCANNER_MIN_WIDTH = float(os.getenv("SPREAD_SCANNER_MIN_WIDTH", "0.08"))
        self.SPREAD_SCANNER_MIN_VOLUME = int(os.getenv("SPREAD_SCANNER_MIN_VOLUME", "500"))
        self.SPREAD_SCANNER_INTERVAL = int(os.getenv("SPREAD_SCANNER_INTERVAL", "300"))
        self.SPREAD_SCANNER_MAX_HOURS = float(os.getenv("SPREAD_SCANNER_MAX_HOURS", "12"))
        self.SPREAD_SCANNER_MIN_EDGE = float(os.getenv("SPREAD_SCANNER_MIN_EDGE", "0.05"))

        # Sports Trader strategy
        self.SPORTS_ENABLED = os.getenv("SPORTS_ENABLED", "false").lower() == "true"
        self.SPORTS_SCAN_INTERVAL = int(os.getenv("SPORTS_SCAN_INTERVAL", "300"))
        self.ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
        self.ODDS_API_REGION = os.getenv("ODDS_API_REGION", "eu")

        # Economic Indicator Trader strategy
        self.ECON_ENABLED = os.getenv("ECON_ENABLED", "false").lower() == "true"
        self.ECON_SCAN_INTERVAL = int(os.getenv("ECON_SCAN_INTERVAL", "3600"))
        self.ECON_MIN_EDGE = float(os.getenv("ECON_MIN_EDGE", "0.10"))

        # Mentions Market Trader strategy
        self.MENTIONS_ENABLED = os.getenv("MENTIONS_ENABLED", "false").lower() == "true"
        self.MENTIONS_MIN_EDGE = float(os.getenv("MENTIONS_MIN_EDGE", "0.15"))
        self.MENTIONS_FREQUENCY_API_URL = os.getenv("MENTIONS_FREQUENCY_API_URL", "")

        # Monitoring
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        # Binance WebSocket URLs (US fallback for regions where global is blocked)
        binance_base = os.getenv("BINANCE_WS_BASE", "wss://stream.binance.us:9443")
        self.BINANCE_WS_URLS = {
            "BTC": f"{binance_base}/ws/btcusdt@aggTrade",
            "ETH": f"{binance_base}/ws/ethusdt@aggTrade",
            "SOL": f"{binance_base}/ws/solusdt@aggTrade",
        }

    @property
    def is_paper_mode(self) -> bool:
        return self.TRADING_MODE == "paper"

    def estimate_fee(self, num_contracts: int, price_cents: int) -> float:
        """Kalshi fee: flat rate per contract, capped so fee never exceeds potential profit."""
        raw_fee = self.KALSHI_FEE_PER_CONTRACT * num_contracts
        max_fee_yes = (price_cents / 100) * num_contracts
        max_fee_no = ((100 - price_cents) / 100) * num_contracts
        return min(raw_fee, max_fee_yes, max_fee_no)

    def estimate_fee_per_contract(self, price_cents: int) -> float:
        """Fee for a single contract — used by strategies to evaluate signals before sizing."""
        return min(self.KALSHI_FEE_PER_CONTRACT, price_cents / 100, (100 - price_cents) / 100)
