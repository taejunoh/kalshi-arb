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
        # Live-strategy daily loss cap — independent from paper MAX_DAILY_LOSS.
        # Set to same default so both modes have identical limits by default.
        self.MAX_DAILY_LOSS_LIVE = float(os.getenv("MAX_DAILY_LOSS_LIVE", "200"))
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

        # Ladder consistency arb (A1) — 4 sub-strategy shared knobs.
        # Per-sub-strategy override pattern: each sub-strategy class can
        # override class-level constants instead of env vars.
        self.LADDER_MIN_ARB_EDGE_USDC = float(os.getenv("LADDER_MIN_ARB_EDGE_USDC", "0.02"))
        self.LADDER_MIN_FADE_INVERSION = float(os.getenv("LADDER_MIN_FADE_INVERSION", "0.05"))
        self.LADDER_MIN_DEPTH_USDC = float(os.getenv("LADDER_MIN_DEPTH_USDC", "30"))
        self.LADDER_MAX_POSITION_PER_GROUP = float(os.getenv("LADDER_MAX_POSITION_PER_GROUP", "50"))
        self.LADDER_STALE_ORDERBOOK_SEC = float(os.getenv("LADDER_STALE_ORDERBOOK_SEC", "10"))

        # Macro Event Trader (C1) — single sub-strategy macro_event.
        # Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
        self.MACRO_EVENT_MIN_DEVIATION = float(os.getenv("MACRO_EVENT_MIN_DEVIATION", "0.10"))
        self.MACRO_EVENT_MIN_DEPTH_USDC = float(os.getenv("MACRO_EVENT_MIN_DEPTH_USDC", "20"))
        self.MACRO_EVENT_BASE_SIZE_USDC = float(os.getenv("MACRO_EVENT_BASE_SIZE_USDC", "20"))
        self.MACRO_EVENT_MAX_POSITION_PER_TICKER = float(os.getenv("MACRO_EVENT_MAX_POSITION_PER_TICKER", "30"))
        self.MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.MACRO_EVENT_WINDOW_MAX_HOURS = float(os.getenv("MACRO_EVENT_WINDOW_MAX_HOURS", "336"))
        self.MACRO_EVENT_WINDOW_MIN_MINUTES = float(os.getenv("MACRO_EVENT_WINDOW_MIN_MINUTES", "30"))
        self.MACRO_CONSENSUS_STALE_HOURS = float(os.getenv("MACRO_CONSENSUS_STALE_HOURS", "24"))

        # Monitoring
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        # Binance WebSocket URLs (US fallback for regions where global is blocked)
        binance_base = os.getenv("BINANCE_WS_BASE", "wss://stream.binance.us:9443")
        self.BINANCE_WS_URLS = {
            "BTC": f"{binance_base}/ws/btcusdt@aggTrade",
            "ETH": f"{binance_base}/ws/ethusdt@aggTrade",
            "SOL": f"{binance_base}/ws/solusdt@aggTrade",
            "XRP": f"{binance_base}/ws/xrpusdt@aggTrade",
        }

        # Crypto Fair-Value Trader (B2) — single sub-strategy crypto_fair_value.
        # Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
        self.CRYPTO_FAIR_MIN_DEVIATION = float(os.getenv("CRYPTO_FAIR_MIN_DEVIATION", "0.10"))
        self.CRYPTO_FAIR_MIN_DEPTH_USDC = float(os.getenv("CRYPTO_FAIR_MIN_DEPTH_USDC", "30"))
        self.CRYPTO_FAIR_BASE_SIZE_USDC = float(os.getenv("CRYPTO_FAIR_BASE_SIZE_USDC", "20"))
        self.CRYPTO_FAIR_MAX_POSITION_PER_TICKER = float(os.getenv("CRYPTO_FAIR_MAX_POSITION_PER_TICKER", "30"))
        self.CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.CRYPTO_FAIR_RECENTLY_FIRED_SECS = float(os.getenv("CRYPTO_FAIR_RECENTLY_FIRED_SECS", "60"))
        self.CRYPTO_FAIR_ATM_WINDOW_N = int(os.getenv("CRYPTO_FAIR_ATM_WINDOW_N", "5"))
        self.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS = float(os.getenv("CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS", "60"))

        # Cross-Venue Polymarket Trader (B1) — single sub-strategy xvenue_polymarket.
        # Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
        self.XVENUE_MIN_DEVIATION = float(os.getenv("XVENUE_MIN_DEVIATION", "0.10"))
        self.XVENUE_MIN_DEPTH_USDC = float(os.getenv("XVENUE_MIN_DEPTH_USDC", "30"))
        self.XVENUE_BASE_SIZE_USDC = float(os.getenv("XVENUE_BASE_SIZE_USDC", "20"))
        self.XVENUE_MAX_POSITION_PER_TICKER = float(os.getenv("XVENUE_MAX_POSITION_PER_TICKER", "30"))
        self.XVENUE_MAX_TICKER_TOTAL_EXPOSURE = float(os.getenv("XVENUE_MAX_TICKER_TOTAL_EXPOSURE", "80"))
        self.XVENUE_QUOTE_STALE_MINS = float(os.getenv("XVENUE_QUOTE_STALE_MINS", "15"))
        self.XVENUE_MIN_POLYMARKET_VOLUME = float(os.getenv("XVENUE_MIN_POLYMARKET_VOLUME", "5000"))
        self.XVENUE_RECENTLY_FIRED_SECS = float(os.getenv("XVENUE_RECENTLY_FIRED_SECS", "300"))

        # Phase 1 promotions from crypto_observer (2026-05-14).
        # Both paper-only at launch — no LIVE_STRATEGIES env entry.
        # See [[crypto_observer_phase1_promotion_2026-05-14]].
        # ─ deribit_tail_fade_v1: observer cohort showed 26/26 = 100% WR
        #   across ya bands $0.03-$0.30. Fades tail (BUY NO).
        self.DERIBIT_TAIL_FADE_SIZE_USDC = float(os.getenv("DERIBIT_TAIL_FADE_SIZE_USDC", "20"))
        # 2026-05-15: 0.35→0.30. Phase 0 ya 0.30-0.40 cohort 77.8% WR
        # (n=18), too close to ~71% break-even at this price level.
        self.DERIBIT_TAIL_FADE_MAX_YA = float(os.getenv("DERIBIT_TAIL_FADE_MAX_YA", "0.30"))
        self.DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S = float(os.getenv("DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S", "15"))
        # ─ bid_imbalance_v1: original direction (BUY pred side at ask).
        #   5/14 promotion used n=26 cohort @ 69% WR — fluke. Full Phase 0
        #   cohort 2026-05-15 (n=266) showed 36% WR overall; paper trader
        #   hit -37.6% ROI on n=83. MIN_PRICE keeps only the rich-end band
        #   (ya≥0.50, where original wins ~80% WR); ya<0.60 zone handled
        #   by `bid_imbalance_fade_v1`.
        self.BID_IMBALANCE_SIZE_USDC = float(os.getenv("BID_IMBALANCE_SIZE_USDC", "20"))
        self.BID_IMBALANCE_SIGNAL_FRESHNESS_S = float(os.getenv("BID_IMBALANCE_SIGNAL_FRESHNESS_S", "15"))
        self.BID_IMBALANCE_MIN_PRICE = float(os.getenv("BID_IMBALANCE_MIN_PRICE", "0.50"))
        # ─ bid_imbalance_fade_v1 (2026-05-15): flipped sibling for ya<0.60.
        #   Phase 0 cohort flips at 83% WR in this zone (n=141).
        self.BID_IMBALANCE_FADE_SIZE_USDC = float(os.getenv("BID_IMBALANCE_FADE_SIZE_USDC", "20"))
        self.BID_IMBALANCE_FADE_SIGNAL_FRESHNESS_S = float(os.getenv("BID_IMBALANCE_FADE_SIGNAL_FRESHNESS_S", "15"))

        # Flipped-prediction Phase 1 variants (2026-05-14).
        # Phase 0 cohort showed original observers' WR ≪ 50% — the inverse
        # is strongly +EV. New observers `open_no_touch` / `atm_hold` emit
        # flipped predictions; these traders execute them as paper trades.
        # ─ open_no_touch_v1: 80% WR / +30% ROI on n=325 cohort.
        #   BUY NO at no_ask on each fresh signal.
        self.OPEN_NO_TOUCH_SIZE_USDC = float(os.getenv("OPEN_NO_TOUCH_SIZE_USDC", "20"))
        self.OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S = float(os.getenv("OPEN_NO_TOUCH_SIGNAL_FRESHNESS_S", "15"))
        # MIN_YA filter: tail buckets (ya < 0.20) have no_ask ≥ 0.80 → max
        # profit < $5/$20 stake, eaten by fee. Cohort 587-signal breakdown
        # showed EV concentrated in ya ≥ 0.20 (band 04+05 = +40%/+182% ROI).
        # Set MIN_YA=0.20 so trader fires only on ATM-region buckets where
        # the payoff multiplier overcomes fee asymmetry.
        self.OPEN_NO_TOUCH_MIN_YA = float(os.getenv("OPEN_NO_TOUCH_MIN_YA", "0.20"))
        # Per-event cap: ATM±1% in KXBTC ($100 grid) yields 10-16 bucket
        # signals per event open. Without a cap, a single event's outcome
        # determines N trades' fate — same close = same direction = correlated
        # samples. Cap at 3 trades/event so 50 trades = ~17 independent
        # events instead of ~5. Counts reset on bot restart (in-memory).
        self.OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT = int(os.getenv("OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT", "3"))
        # ─ atm_hold_v1: ATM bucket holds (resolves YES) within 5min of close.
        #   BUY YES at ya on each fresh signal. Cohort tiny (n=10 pre-fix);
        #   relies on post-MAX-fix observer keeping ya in [0.40, 0.65].
        self.ATM_HOLD_SIZE_USDC = float(os.getenv("ATM_HOLD_SIZE_USDC", "20"))
        self.ATM_HOLD_SIGNAL_FRESHNESS_S = float(os.getenv("ATM_HOLD_SIGNAL_FRESHNESS_S", "15"))

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
