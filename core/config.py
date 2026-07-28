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
        # ④ Real-fee GATE mode (default 'off'). Strategy +EV gates can use the real
        # Kalshi fee 0.07*p*(1-p) (via gate_fee_pct) instead of the inflated flat-$0.07,
        # opening marginal +EV trades the old gate rejected.
        #   'off'   — legacy flat-fee gate everywhere (zero behaviour change).
        #   'paper' — real-fee gate for PAPER strategies only; LIVE_STRATEGIES stay on
        #             the legacy gate (no real-money change). The paper-first step:
        #             collect forward data on the looser gate before any live flip.
        #   'all'   — real-fee gate everywhere (paper + live).
        # Back-compat: the old boolean REAL_FEE_GATE=true maps to mode 'all'.
        _legacy_gate_on = os.getenv("REAL_FEE_GATE", "").lower() == "true"
        self.REAL_FEE_GATE_MODE = os.getenv(
            "REAL_FEE_GATE_MODE", "all" if _legacy_gate_on else "off").lower()
        # Back-compat attribute: gate_fee_pct() applies the real fee when this is True
        # (mode 'all'). The 'paper' split is resolved per-strategy in _gate_fee_pct().
        self.REAL_FEE_GATE = self.REAL_FEE_GATE_MODE == "all"

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
        # ladder_fed only: how many of the nearest still-open FOMC meetings it
        # may trade, and how much open capital it may hold across them. Both
        # added 2026-07-28 after it booked 63 entries over 2.5 months and
        # settled ZERO — it had been entering meetings out to 28JAN (18 months),
        # so no leg could return a result while $750 stayed locked.
        self.LADDER_FED_HORIZON_MEETINGS = int(os.getenv("LADDER_FED_HORIZON_MEETINGS", "2"))
        self.LADDER_FED_MAX_OPEN_EXPOSURE_USDC = float(
            os.getenv("LADDER_FED_MAX_OPEN_EXPOSURE_USDC", "100")
        )

        # Ladder Phase 0 resurrection (2026-05-19). Paper-only walked-edge
        # logging; phantom-quote defense via orderbook walk. Spec:
        # docs/superpowers/specs/2026-05-19-ladder-crypto-resurrection-design.md
        self.LADDER_PHASE0_MIN_WALKED_EDGE_USDC = float(
            os.getenv("LADDER_PHASE0_MIN_WALKED_EDGE_USDC", "0.15")
        )
        self.LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS = float(
            os.getenv("LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS", "2.0")
        )
        self.LADDER_PHASE0_PROBE_NOTIONAL_USDC = float(
            os.getenv("LADDER_PHASE0_PROBE_NOTIONAL_USDC", "25.0")
        )
        self.LADDER_PHASE0_ENABLED = os.getenv("LADDER_PHASE0_ENABLED", "true").lower() == "true"

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

        # Binance WebSocket URLs (US fallback for regions where global is blocked).
        # 2026-05-19: binance.us silently stopped emitting @trade / @aggTrade /
        # @miniTicker (no error, just zero messages). @kline_1m still works and
        # k.c (close) tracks last trade at ~1Hz, which is sufficient for spot.
        binance_base = os.getenv("BINANCE_WS_BASE", "wss://stream.binance.us:9443")
        self.BINANCE_WS_URLS = {
            "BTC": f"{binance_base}/ws/btcusdt@kline_1m",
            "ETH": f"{binance_base}/ws/ethusdt@kline_1m",
            "SOL": f"{binance_base}/ws/solusdt@kline_1m",
            "XRP": f"{binance_base}/ws/xrpusdt@kline_1m",
        }
        # bookTicker mid feed — consumed by crypto_observer's get_fresh_spot
        # fallback (sub-second mid > 60s kline). SOL omitted — no live market
        # universe for SOL strike grids.
        self.BINANCE_BOOK_TICKER_URLS = {
            "BTC": f"{binance_base}/ws/btcusdt@bookTicker",
            "ETH": f"{binance_base}/ws/ethusdt@bookTicker",
            "XRP": f"{binance_base}/ws/xrpusdt@bookTicker",
        }

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
        # 2026-05-18 band re-analysis (paper n=483 post-tune):
        #   no_ask 0.80-0.85 (ya 0.15-0.20)  n=125  WR 99%  ROI +14.2%
        #   no_ask 0.85-0.90 (ya 0.10-0.15)  n=189  WR 100% ROI  +8.8%
        #   no_ask 0.90-0.93 (ya 0.07-0.10)  n= 39  WR 100% ROI  +3.3%
        #   no_ask 0.93-0.99 (ya 0.01-0.07)  n=109  WR 95%  ROI  ~0%   ← scratch
        #   no_ask 0.99+     (ya <0.01)      n= 17  WR  0%  ROI -5.9%  ← loss
        #   no_ask <0.80     (ya >0.20)      n=  4  WR 50%  ROI -54%   ← mis-cal
        # MAX_YA 0.30→0.20 cuts the sub-0.80 mis-calibration zone (P1).
        # MIN_YA = 0.08 cuts the >=0.93 degenerate zone where win payoff
        # (~$0.02-0.07) can't cover the rare full loss (P0). Projects
        # lifetime ROI +6.5% → ~+10%.
        self.DERIBIT_TAIL_FADE_MAX_YA = float(os.getenv("DERIBIT_TAIL_FADE_MAX_YA", "0.20"))
        self.DERIBIT_TAIL_FADE_MIN_YA = float(os.getenv("DERIBIT_TAIL_FADE_MIN_YA", "0.08"))
        self.DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S = float(os.getenv("DERIBIT_TAIL_FADE_SIGNAL_FRESHNESS_S", "15"))
        # 2026-05-18: depth-aware sizing replaces FOK market orders that
        # rejected on thin tail-strike books (first live fire 19:51 UTC,
        # KXETH-26MAY1816-B2100 needed 21 contracts, book had <21). Order
        # is clamped to top-of-book NO-ask depth; fires with effective
        # contract count below MIN_FIRE_CONTRACTS are skipped.
        # 2026-05-18 evening: lowered 3→1 to maximize fire-rate while
        # validating the live execution path. Once first live fill
        # confirms end-to-end correctness, raise back to 3 to avoid
        # dust trades polluting analytics.
        self.DERIBIT_TAIL_FADE_MIN_FIRE_CONTRACTS = float(os.getenv("DERIBIT_TAIL_FADE_MIN_FIRE_CONTRACTS", "1"))
        # 2026-05-20 MAX_NO_ASK = 0.95. Paper 14d bucket analysis (n=537):
        #   no_ask <0.85    n=130  +$2.04/fire  total +$266
        #   no_ask 0.85-0.90 n=191 +$1.51/fire  total +$289
        #   no_ask 0.90-0.93 n= 42 +$0.58/fire  total  +$24
        #   no_ask 0.93-0.95 n= 11 +$0.03/fire  total +$0.30  (BE)
        #   no_ask 0.95-0.97 n= 15 +$0.01/fire  total +$0.21  (BE, 47% WR)
        #   no_ask >=0.97   n=148 -$0.13/fire  total -$18.58  ← net loss
        # MIN_YA=0.08 was supposed to handle this, but ya gates on yes_ask
        # which can decouple from no_ask under wide crypto spreads (e.g.,
        # yes_ask=0.10 + no_ask=0.98 = 8c spread allowed). All 5 live FOK
        # rejects (5/19 22:54-22:58 KXETH-26MAY1919-B2090) were at 0.98 —
        # the same band that is net-EV-negative even in paper. Cap targets
        # both fill-rate and EV simultaneously.
        self.DERIBIT_TAIL_FADE_MAX_NO_ASK = float(os.getenv("DERIBIT_TAIL_FADE_MAX_NO_ASK", "0.95"))
        # 2026-05-21 asset exclude. Pre-cap (5/14-5/19) post-mega audit
        # (n=231 ex-KXBTC-26MAY1617): KXBTC −$74.44 / n=75 / ROI −4.96%
        # vs KXETH +$12.44 / n=158 / ROI +0.39%. All 5 tail wipes (−$20)
        # were KXBTC; 4/5 were at NO<0.95 so the cap above does not defend
        # against them. KXETH had 0 wipes in n=158. Comma-separated asset
        # prefixes (e.g. "KXBTC" or "KXBTC,KXXRP") matched against
        # market_id.split('-')[0]. Empty string = no exclusion.
        self.DERIBIT_TAIL_FADE_EXCLUDE_ASSETS = os.getenv("DERIBIT_TAIL_FADE_EXCLUDE_ASSETS", "KXBTC")
        # ─ deribit_tail_fade_high_v1 (2026-05-21): sister paper trader for the
        #   high-ya zone (0.65-0.95) that the parent gate ignores. Lifetime
        #   observer (n=1,880) shows two distinct signal classes:
        #     - low-ya 0.08-0.20 (current gate): long-dated tail (mts p50 ~22h),
        #       WR 99.7%, +$0.13/ct gross-fee — already covered.
        #     - high-ya 0.65-0.95 (new gate): near-expiry near-strike (mts p50
        #       ~1h), WR 86-94%, +$0.53-0.74/ct gross-fee — uncovered.
        #   Same observer (deribit_tail_skew), same predicted_outcome=no, but
        #   different feature shape: high-ya signals fire when spot is near or
        #   above strike and Deribit IV implies the move-back probability is
        #   under-discounted by Kalshi. See [[phase1-lifetime-audit-sweep-2026-05-21]].
        #
        #   PAPER-ONLY. Fill realism is the open question — at ya 0.88, NO=0.12
        #   means $20 stake = 167 contracts, and high-ya NO-side books are thin
        #   near expiry. Depth-aware sizing + MIN_FIRE_CONTRACTS will skip
        #   under-depth signals; LIVE promotion gated on paper fire-rate +
        #   actual fill realism over a 1-2 week window.
        # 2026-05-22 redesign: SIZE_CT replaces SIZE_USDC. ya 0.65-0.95
        # means no_ask 0.05-0.35, so $20 stake = 57-400 contracts —
        # structurally unfillable on tail books. LIVE 5/22 saw 22/22 FOK
        # rejects at 7 ct. Switching to fixed-contract sizing bounds the
        # FOK risk regardless of ya. See [[deribit_tail_fade_high_v1_paper_fictitious_2026-05-22]].
        self.DERIBIT_TAIL_FADE_HIGH_SIZE_CT = int(os.getenv("DERIBIT_TAIL_FADE_HIGH_SIZE_CT", "5"))
        # Legacy USDC field kept for migration / tests; SIZE_CT * price
        # takes precedence in the strategy.
        self.DERIBIT_TAIL_FADE_HIGH_SIZE_USDC = float(os.getenv("DERIBIT_TAIL_FADE_HIGH_SIZE_USDC", "20"))
        self.DERIBIT_TAIL_FADE_HIGH_MIN_YA = float(os.getenv("DERIBIT_TAIL_FADE_HIGH_MIN_YA", "0.65"))
        # 2026-05-23 redesign #2: MAX_YA back to 0.95. With LIMIT execution
        # (USE_LIMIT_ORDER=True on strategy), thin books at 0.85-0.95 are
        # exactly where the highest per-ct EV lives (observer ya 0.85-0.90
        # = +$0.80/ct gross, 0.90-0.95 = +$0.75/ct). LIMIT order rests on
        # the book and accepts partial fills, so thin-book FOK problem is
        # solved by execution change, not by cohort avoidance.
        self.DERIBIT_TAIL_FADE_HIGH_MAX_YA = float(os.getenv("DERIBIT_TAIL_FADE_HIGH_MAX_YA", "0.95"))
        self.DERIBIT_TAIL_FADE_HIGH_SIGNAL_FRESHNESS_S = float(os.getenv("DERIBIT_TAIL_FADE_HIGH_SIGNAL_FRESHNESS_S", "15"))
        # 2026-05-23 redesign #2: DEPTH_SAFETY_MULT 3.0 → 1.0. LIMIT order
        # rests on the book + partial fills, so the FOK depth-overstatement
        # buffer is no longer needed. Keep at 1× as sanity gate (depth >=
        # SIZE_CT) — combined with MIN_FIRE_CONTRACTS the effective floor
        # is max(SIZE_CT, MIN_FIRE_CONTRACTS).
        self.DERIBIT_TAIL_FADE_HIGH_DEPTH_SAFETY_MULT = float(
            os.getenv("DERIBIT_TAIL_FADE_HIGH_DEPTH_SAFETY_MULT", "1.0"))
        self.DERIBIT_TAIL_FADE_HIGH_MIN_FIRE_CONTRACTS = float(os.getenv("DERIBIT_TAIL_FADE_HIGH_MIN_FIRE_CONTRACTS", "5"))
        # 2026-05-22 redesign: per-event cap 2 → 1. Cascade winners were
        # the +EV illusion in paper (B77650 3× +$215 at 80-125 ct, all
        # fictitious). Capping at 1 fire per event reflects what's
        # realistically fillable in LIVE on tail books — compounding
        # bets on the same event is structurally unfillable here.
        self.DERIBIT_TAIL_FADE_HIGH_MAX_FIRES_PER_EVENT = int(
            os.getenv("DERIBIT_TAIL_FADE_HIGH_MAX_FIRES_PER_EVENT", "1"))
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
        # ya window [MIN_YA, MAX_YA) — band D from 2026-05-15 Phase 0 re-analysis
        # (n=928 settled, proper no_ask=1-yb + Kalshi fee). Original "+30% ROI"
        # promotion claim used a flawed symmetric payoff model; corrected
        # band ROI breakdown shows ya 0.30-0.45 is the only positive slice
        # (+10% n=38) — all other bands are -3% to -44%. ya here is derived
        # from no_ask (ya = 1 - no_ask), not from yes_ask_dollars (which
        # diverges on wide crypto spreads).
        self.OPEN_NO_TOUCH_MIN_YA = float(os.getenv("OPEN_NO_TOUCH_MIN_YA", "0.30"))
        self.OPEN_NO_TOUCH_MAX_YA = float(os.getenv("OPEN_NO_TOUCH_MAX_YA", "0.45"))
        # Per-event cap = 1: ATM±1% strikes all resolve on the same BTC/ETH
        # hourly close (~100 signals/event observed in Phase 0). Multiple
        # fires share one outcome → correlated samples that inflate n without
        # adding information. Cap=1 + center-of-window strike picker keeps
        # samples independent enough for promotion-gate statistics.
        self.OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT = int(os.getenv("OPEN_NO_TOUCH_MAX_FIRES_PER_EVENT", "1"))
        # KXETH / KXETHD × no_ask ∈ [0.40, 0.60) wipe-cohort exclusion
        # (2026-05-21). Phase 1 paper 14d (n=69, -$982, WR 7%, 8/8 days
        # negative) — fine-grain bins 0.40-0.60 all between -$140 and
        # -$342 net. Same band on KXBTC is +EV, so the filter is asset-
        # gated rather than a global MAX_YA shift. Excluding the cohort
        # turns trader from -$823/14d to +$158/14d (WR 68.6%, 6/8 days
        # positive). Bound is exclusive on top to match ya<MAX_YA shape.
        self.OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_LOW = float(
            os.getenv("OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_LOW", "0.40"))
        self.OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_HIGH = float(
            os.getenv("OPEN_NO_TOUCH_KXETH_EXCLUDE_NO_ASK_HIGH", "0.60"))
        # ─ Phase 1 per-ticker cooldown (2026-05-20). DB-persisted gate that
        #   blocks a fire on (strategy, ticker) if the previous fire was
        #   within COOLDOWN_S AND at a price within PRICE_DIFF dollars.
        #   Rationale (atm_hold_v1 5/20 incident): in-memory `_processed`
        #   dedup is wiped on bot restart. Multiple restarts in 4 minutes
        #   caused 3 fires on the same ticker at the same $0.49 price
        #   from 3 different PIDs, losing $60. Promo n=11 paper cohort
        #   showed legitimate multi-fire cascade always moved ya by ≥7c,
        #   so 3c price tolerance preserves edge while blocking restart
        #   pollution. See [[atm_hold_v1_promo_cohort_audit_2026-05-20]].
        self.PHASE1_TICKER_COOLDOWN_S = float(os.getenv("PHASE1_TICKER_COOLDOWN_S", "30"))
        self.PHASE1_TICKER_PRICE_DIFF = float(os.getenv("PHASE1_TICKER_PRICE_DIFF", "0.03"))
        # ─ atm_hold_v1: ATM bucket holds (resolves YES) within 5min of close.
        #   BUY YES at ya on each fresh signal. Cohort tiny (n=10 pre-fix);
        #   relies on post-MAX-fix observer keeping ya in [0.40, 0.65].
        self.ATM_HOLD_SIZE_USDC = float(os.getenv("ATM_HOLD_SIZE_USDC", "20"))
        self.ATM_HOLD_SIGNAL_FRESHNESS_S = float(os.getenv("ATM_HOLD_SIGNAL_FRESHNESS_S", "15"))

        # 2026-05-18 Phase 1 promotions: two new paper traders covering
        # gaps in the existing observer coverage.
        # ─ late_close_tail_v1: mid-OTM (ya 0.05-0.30) fader in the 10-min
        #   pre-close window. Broader than tail_decay (which caps at ya
        #   0.05) and orthogonal to deribit_tail_fade (which gates by IV
        #   ratio rather than absolute ya band). BUY NO at no_ask.
        self.LATE_CLOSE_TAIL_SIZE_USDC = float(os.getenv("LATE_CLOSE_TAIL_SIZE_USDC", "20"))
        self.LATE_CLOSE_TAIL_SIGNAL_FRESHNESS_S = float(os.getenv("LATE_CLOSE_TAIL_SIGNAL_FRESHNESS_S", "15"))
        self.LATE_CLOSE_TAIL_MIN_YA = float(os.getenv("LATE_CLOSE_TAIL_MIN_YA", "0.05"))
        self.LATE_CLOSE_TAIL_MAX_YA = float(os.getenv("LATE_CLOSE_TAIL_MAX_YA", "0.30"))
        # ─ cme_basis_fade_v1 (2026-05-20 promo, 2026-05-21 retune): paper-only.
        #   Original gate [0.17, 0.42] from n=292 sub-cohort was a sample-size
        #   artifact — lifetime observer n=5,581 shows real +EV is concentrated
        #   in ya 0.17-0.25 (n=626 / +$0.062/ct), and 0.25-0.42 is uniformly
        #   −EV after fees (most catastrophic at 0.40-0.42 = WR 25% / −$0.37/ct).
        #   24h-pre-5/21 + 5/21-only sub-windows both confirm the same shape.
        #   BUY NO at no_ask. See [[cme_basis_fade_v1_bleed_2026-05-21]].
        self.CME_BASIS_FADE_SIZE_USDC = float(os.getenv("CME_BASIS_FADE_SIZE_USDC", "20"))
        self.CME_BASIS_FADE_SIGNAL_FRESHNESS_S = float(os.getenv("CME_BASIS_FADE_SIGNAL_FRESHNESS_S", "15"))
        self.CME_BASIS_FADE_MIN_YA = float(os.getenv("CME_BASIS_FADE_MIN_YA", "0.17"))
        self.CME_BASIS_FADE_MAX_YA = float(os.getenv("CME_BASIS_FADE_MAX_YA", "0.25"))
        self.CME_BASIS_FADE_MIN_FIRE_CONTRACTS = float(os.getenv("CME_BASIS_FADE_MIN_FIRE_CONTRACTS", "1"))
        # 2026-05-21 LIVE-hardening (Config J from DB-replay sim n=233 paper+LIVE):
        # MAX_NO_ASK 0.85 — entry above 0.85 needs WR ≥92% to break Kalshi
        # taker fee ($0.07/ct flat); paper WR 96% margin is ~4pp at 0.85,
        # near zero at 0.87. Above-band fires were 23/233 of sample but
        # only cut $28 of bleed (Fix 1 minor).
        # MAX_FIRES_PER_EVENT 8 — single-event cascade cap. 5/20 KXETH crash
        # cohort fired 15× on one strike all wiping ($300 single-event loss);
        # KXBTC trend cohort fired 77× across 5 strikes (-$312). per-event
        # cap matters more than per-ticker because adjacent strikes wipe
        # together on underlying drift. Sim n=233: cap-8 cut 5/20 -$609 →
        # -$119 (80% truncation) while only losing 14% of 5/21 calm alpha.
        # Combined J: total -$57 vs A_AS_IS -$537.
        self.CME_BASIS_FADE_MAX_NO_ASK = float(os.getenv("CME_BASIS_FADE_MAX_NO_ASK", "1.0"))
        self.CME_BASIS_FADE_MAX_FIRES_PER_EVENT = int(os.getenv("CME_BASIS_FADE_MAX_FIRES_PER_EVENT", "999"))
        # Same depth-aware-sizing floor as deribit_tail_fade_v1; skip fires
        # whose effective contract count would be below this threshold to
        # avoid dust trades.
        self.LATE_CLOSE_TAIL_MIN_FIRE_CONTRACTS = float(os.getenv("LATE_CLOSE_TAIL_MIN_FIRE_CONTRACTS", "1"))
        # 2026-05-21 LIVE-promo pre-coding (defaults = no-op; flip at 6/03 audit).
        # MAX_NO_ASK: paper bucket data (n=110 observer) shows ≥0.95 zone is
        # LIVE-net-zero — paper edge ($0.02-0.06/ct) is fully consumed by
        # integer-contract rounding + Kalshi fee. Default 1.0 keeps current
        # paper behavior; LIVE-promo target = 0.94.
        # EXCLUDE_NO_ASK band: 0.88-0.89 single-bucket cliff (n=5, WR 60%,
        # −$0.282/ct) observed 5/21 — un-defended at paper. Default disabled
        # (LOW >= HIGH); flip to (0.88, 0.89) if 6/03 audit confirms cliff
        # at n≥15 with WR<75%. See [[late_close_tail_v1_subcohort_audit_2026-05-20]].
        self.LATE_CLOSE_TAIL_MAX_NO_ASK = float(os.getenv("LATE_CLOSE_TAIL_MAX_NO_ASK", "1.0"))
        self.LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_LOW = float(os.getenv("LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_LOW", "0.0"))
        self.LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_HIGH = float(os.getenv("LATE_CLOSE_TAIL_EXCLUDE_NO_ASK_HIGH", "0.0"))
        # ─ deribit_atm_fade_v1: ATM-bucket parallel of deribit_tail_fade.
        #   New hypothesis — Kalshi ATM may be overpriced vs Deribit-IV
        #   lognormal (most existing alpha is in tails). BUY NO at no_ask
        #   when ya ≥ 1.5 × P_LN and ya ∈ [0.30, 0.70].
        self.DERIBIT_ATM_FADE_SIZE_USDC = float(os.getenv("DERIBIT_ATM_FADE_SIZE_USDC", "20"))
        self.DERIBIT_ATM_FADE_SIGNAL_FRESHNESS_S = float(os.getenv("DERIBIT_ATM_FADE_SIGNAL_FRESHNESS_S", "15"))
        self.DERIBIT_ATM_FADE_MIN_YA = float(os.getenv("DERIBIT_ATM_FADE_MIN_YA", "0.30"))
        self.DERIBIT_ATM_FADE_MAX_YA = float(os.getenv("DERIBIT_ATM_FADE_MAX_YA", "0.70"))
        self.DERIBIT_ATM_FADE_MIN_FIRE_CONTRACTS = float(os.getenv("DERIBIT_ATM_FADE_MIN_FIRE_CONTRACTS", "1"))
        # NOTE 2026-05-20: no MAX_NO_ASK cap. ATM band ya 0.30-0.70 rarely
        # crosses 0.95 by design; paper data is n=0 (strategy has not yet
        # fired). Add cap only if/when band data warrants it.

    @property
    def is_paper_mode(self) -> bool:
        return self.TRADING_MODE == "paper"

    def estimate_fee(self, num_contracts: int, price_cents: int) -> float:
        """Kalshi fee: flat rate per contract, capped so fee never exceeds potential profit.

        NOTE: This is the OLD flat-$0.07/ct model. It overstates the real Kalshi
        fee ~4-9x (see real_fee). Retained ONLY for the strategy signal GATE
        (estimate_fee_per_contract) until that gate is migrated separately; the
        trade-RECORDING paths now use real_fee() so stored fee/pnl/bankroll match
        Kalshi's actual ledger.
        """
        raw_fee = self.KALSHI_FEE_PER_CONTRACT * num_contracts
        max_fee_yes = (price_cents / 100) * num_contracts
        max_fee_no = ((100 - price_cents) / 100) * num_contracts
        return min(raw_fee, max_fee_yes, max_fee_no)

    def real_fee(self, num_contracts: float, price_cents: int, maker: bool = False) -> float:
        """Actual Kalshi trading fee = 0.07 * p * (1-p) per contract (taker);
        maker (resting limit order that gets hit) = 25% of that.

        CONFIRMED 2026-06-02 against the live /portfolio/fills `fee_cost` field:
        per-fill fee_cost == 0.07*p*(1-p)*count to 4 decimals; aggregate over
        2,400 fills actual $58 vs flat-$0.07 model $512 (8.8x). The bot trades
        marketable-limit/market (taker) but many limit-TTL orders rest and fill
        as maker — so real_fee(taker) is a conservative UPPER bound on the charge.
        """
        p = max(0.0, min(1.0, price_cents / 100.0))
        coeff = 0.0175 if maker else 0.07  # maker = 0.25 * 0.07
        fee = coeff * p * (1.0 - p) * num_contracts
        # never exceeds the contract value (parity with estimate_fee cap)
        return min(fee, p * num_contracts, (1.0 - p) * num_contracts)

    def estimate_fee_per_contract(self, price_cents: int) -> float:
        """Fee for a single contract — used by strategies to evaluate signals before sizing.

        STILL the flat-$0.07 model (the trade GATE). Migrating this to real_fee
        changes which trades fire (opens currently-rejected +EV trades) and is
        done as a SEPARATE, paper-validated step — not here.
        """
        return min(self.KALSHI_FEE_PER_CONTRACT, price_cents / 100, (100 - price_cents) / 100)

    def gate_fee_pct(self, price: float) -> float:
        """Fee as a fraction of the entry price, for strategy +EV gates (④).

        Resolves the 'off' vs 'all' modes (the per-strategy 'paper' split is applied
        upstream in Strategy._gate_fee_pct, which only delegates here for off/all):
          mode 'all' (REAL_FEE_GATE True) -> 0.07*(1-price)  [= (0.07*p*(1-p))/p, the
            real Kalshi taker fee as a fraction of price] — ~4-9x lower than flat-$0.07.
          mode 'off' -> min(0.07, price, 1-price)/price — IDENTICAL to the legacy
            inline `min(fee, price, 1-price)/price` the strategies used.
        `price` in DOLLARS (0-1); returns 0 for degenerate prices.
        """
        if price <= 0 or price >= 1:
            return 0.0
        if self.REAL_FEE_GATE:
            return 0.07 * (1.0 - price)
        return min(self.KALSHI_FEE_PER_CONTRACT, price, 1.0 - price) / price

    def is_live_strategy(self, name: str) -> bool:
        """True if a strategy named `name` trades real money under the current mode.

        Mirrors OrderManager._is_live: global live mode => always True; otherwise
        live only if `name` is in LIVE_STRATEGIES. Used by the ④ 'paper' gate split.
        """
        if not self.is_paper_mode:
            return True
        return name in self.LIVE_STRATEGIES
