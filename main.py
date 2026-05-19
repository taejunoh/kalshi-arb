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
from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy
from strategies.sports_longshot_v34 import SportsLongshotV34Strategy
from strategies.sports_midshot_v34 import SportsMidshotV34Strategy
from strategies.sports_underdog_v34 import SportsUnderdogV34Strategy
from strategies.sports_longshot_v35 import SportsLongshotV35Strategy
from strategies.sports_midshot_v35 import SportsMidshotV35Strategy
from strategies.sports_underdog_v35 import SportsUnderdogV35Strategy
from strategies.sports_playoff_underdog_v1 import SportsPlayoffUnderdogV1Strategy
from strategies.sports_playoff_underdog_v1_us import SportsPlayoffUnderdogV1USStrategy
from strategies.sports_soccer_underdog_v1 import SportsSoccerUnderdogV1Strategy
from strategies.sports_soccer_draw_v1 import SportsSoccerDrawV1Strategy
from strategies.sports_mls_fade_favorite_v1 import SportsMLSFadeFavoriteV1Strategy
from strategies.sports_underdog_v36 import SportsUnderdogV36Strategy
from strategies.sports_underdog_v37 import SportsUnderdogV37Strategy
from strategies.sports_favorite_v40 import SportsFavoriteV40Strategy
from strategies.sports_tennis_pinnacle_arb_v1 import SportsTennisPinnacleArbV1Strategy
from strategies.sports_atp_gs_bo5_chalk_v1 import SportsAtpGsBo5ChalkV1Strategy
from strategies.sports_tennis_bid_imbalance_v1 import SportsTennisBidImbalanceV1Strategy
from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy
from strategies.sports_tennis_surface_skew_v1 import SportsTennisSurfaceSkewV1Strategy
from strategies.sports_tennis_underdog_v1 import SportsTennisUnderdogV1Strategy
from strategies.sports_pinnacle_edge import SportsPinnacleEdgeStrategy
from strategies.sports_v4_edge import SportsV4EdgeStrategy
from strategies.sports_chalk_shadow import SportsChalkShadowStrategy
from strategies.sports_scan_observer import SportsScanObserver
from strategies.econ_trader import EconTraderStrategy
from strategies.crypto_observer import CryptoObserverStrategy
from strategies.crypto_observer_phase1 import (
    DeribitTailFadeV1Strategy, BidImbalanceV1Strategy,
    OpenNoTouchV1Strategy, AtmHoldV1Strategy,
    BidImbalanceFadeV1Strategy,
    LateCloseTailV1Strategy, DeribitAtmFadeV1Strategy,
)
from strategies.ladder_fed import LadderFedStrategy
from strategies.ladder_approval import LadderApprovalStrategy
from strategies.ladder_shutdown import LadderShutdownStrategy
from strategies.macro_event import MacroEventStrategy
from strategies.crypto_fair_value import CryptoFairValueStrategy
from strategies.crypto_fair_value_mid_v1 import CryptoFairValueMidV1Strategy
from strategies.xvenue_polymarket import XVenuePolymarketStrategy
from core.polymarket_mapping import PolymarketKalshiMapping
from core.kalshi_orderbook_cache import OrderbookCache
from core.settlement import SettlementChecker
from core.clv_tracker import CLVTracker
from monitoring.logger import setup_logger, TradeLogger
from monitoring.notifier import TelegramNotifier
from monitoring.telegram_error_handler import TelegramErrorHandler
from monitoring.dashboard.api import create_app
from monitoring.balance_schedule import (
    ET as BALANCE_CHECK_TZ,
    format_check_message as format_balance_check_message,
    next_check_time as next_balance_check_time,
)

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
        # V3.2 variants — V3.1 + edge-scaled sizing (paper-only A/B).
        self.sports_longshot_v32 = SportsLongshotV32Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot_v32 = SportsMidshotV32Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog_v32 = SportsUnderdogV32Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.3 variants — V3.1 + per-game stake cap (paper-only A/B).
        self.sports_longshot_v33 = SportsLongshotV33Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot_v33 = SportsMidshotV33Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog_v33 = SportsUnderdogV33Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.4 variants — V3.1 logic with region=us (paper-only). 2026-04-21
        # sport×region matrix showed us outperforming eu on 8/9 splits.
        self.sports_longshot_v34 = SportsLongshotV34Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot_v34 = SportsMidshotV34Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog_v34 = SportsUnderdogV34Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.5 — V3.4's (NBA, pre) us cell promoted to live trading 2026-04-23.
        # Coexists with V3.4 paper on same cell; V3.5 fires live + paper-twin.
        self.sports_longshot_v35 = SportsLongshotV35Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_midshot_v35 = SportsMidshotV35Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_underdog_v35 = SportsUnderdogV35Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.6 Playoff — paper-only. NBA-playoff-only underdog with 35¢ floor.
        # Built 2026-04-30 from sub-bucket analysis showing 35-45¢ band has
        # +135% observer ROI while 20-30¢ band loses -178%/-102% in NBA
        # playoffs. See strategy file docstring for the band-by-band data.
        self.sports_playoff_underdog_v1 = SportsPlayoffUnderdogV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.6 Playoff US — companion to v36_playoff (eu) covering us region.
        # Same 35¢+ floor + PLAYOFF_ONLY logic. Evidence: trade-level us 35¢+
        # n=12 +88-189% across underdog_v34 paper + v35 LIVE+twin.
        self.sports_playoff_underdog_v1_us = SportsPlayoffUnderdogV1USStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.7 Soccer — soccer-only deep-underdog 20-29c TEAM-only.
        # Built 2026-05-06 from observer analysis of MLS/EPL/LAL/BUN/UCL.
        # Paper-twin only at launch. n=13 games / +120% ROI / drop-top-3 +55%.
        # Promotion gate: n>=25 + ROI>=+30% + drop-3 > 0.
        self.sports_soccer_underdog_v1 = SportsSoccerUnderdogV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.8 Soccer Draw — DRAW side of soccer 3-way markets.
        # Built 2026-05-11 from observer of 71 settled TIE tickers since 5/2.
        # EPL+LAL only, 0.25-0.30 YES band. n=13 baseline ROI ~+60%/drop-1
        # ~+25%. Non-BUN 0.20-0.30 sample n=47, ROI +27%, drop-3 +9%.
        # LIVE at launch (size 12). Eval gate: n>=25, ROI>=+30%, drop-3>0 by ~6/22.
        self.sports_soccer_draw_v1 = SportsSoccerDrawV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # MLS Fade-Favorite V1 — MLS reg-season TEAM no_ask 0.30-0.45 BUY NO.
        # Built 2026-05-15 from cross-sport observer scan (n=1214 settled).
        # MLS observer n=14, WR 57.1%, ROI +18.57% at fade-fav band.
        # Paper-twin only at launch. Promotion gate: n>=25 + ROI>=+10% +
        # drop-top-3 > 0 by ~6/22.
        self.sports_mls_fade_favorite_v1 = SportsMLSFadeFavoriteV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.6 WNBA — chalk-favorite YES/NO at 35-55c with fair_prob > 0.55.
        # Built 2026-05-13 from WNBA observer replay (n=3, +73% ROI). Sibling
        # to v36_playoff/v36_us; fresh evaluate() (chalk-favorite, not v36
        # underdog floor35). Live launch size 8. First eval 5/23 at n>=25.
        self.sports_underdog_v36 = SportsUnderdogV36Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V3.7 WNBA — TEAM 20-29c band, payoff>=2x. Thin sibling of v37 soccer
        # (sports_soccer_underdog_v1) with SPORT_BLOCKLIST allowing WNBA +
        # blocking soccer/other sports. Replay n=2, +118% ROI. Live launch
        # size 8. First eval 5/23 at n>=25.
        self.sports_underdog_v37 = SportsUnderdogV37Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # V4.0 Favorite — slight-favorite YES/NO bets on MLB 0.50-0.55 +
        # NPB 0.55-0.65. Built 2026-05-12 from observer scan. MLB n=65 +8%,
        # NPB n=28 +13%. First non-underdog/non-longshot/non-midshot family.
        # PAPER-ONLY size 4 at launch. Drop-top-5 break-even — needs n=120+ for
        # robust read. Promotion gate ROI>=+5% + drop-5>0 by ~6/9.
        self.sports_favorite_v40 = SportsFavoriteV40Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # 2026-05-15 tennis/MMA portfolio — 5 paper-twin strategies built
        # from KXATPMATCH/KXWTAMATCH/KXUFCFIGHT observer + bucket-replay
        # analysis. Promotion gate: n>=25 per strategy + ROI per spec.
        # See docs/superpowers/plans/2026-05-15-tennis-mma-strategy-portfolio-plan.md
        self.sports_tennis_pinnacle_arb_v1 = SportsTennisPinnacleArbV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_atp_gs_bo5_chalk_v1 = SportsAtpGsBo5ChalkV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_tennis_bid_imbalance_v1 = SportsTennisBidImbalanceV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_ufc_chalk_fade_v1 = SportsUfcChalkFadeV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_tennis_surface_skew_v1 = SportsTennisSurfaceSkewV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # Tennis underdog band 0.25-0.35 BUY YES — paper-only. Built from
        # 2026-05-19 observer backtest (n=55 settled, ya[0.25,0.35] WR 37.5%
        # vs implied ~30%). Pattern mirrors team-sport v31 underdog edge.
        self.sports_tennis_underdog_v1 = SportsTennisUnderdogV1Strategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_pinnacle_edge = SportsPinnacleEdgeStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        self.sports_v4_edge = SportsV4EdgeStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # Ladder Consistency Arb (A1) — paper-only at launch.
        # 4 sub-strategies share core/ladder_framework.py. See spec:
        # docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md
        self.ladder_shutdown = LadderShutdownStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.ladder_approval = LadderApprovalStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        self.ladder_fed = LadderFedStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        # ladder_crypto retired 2026-05-13: n=54 paper / -77.5% ROI. Root
        # cause was B-bucketed vs T-cumulative mismatch (commit d30efb5);
        # post-fix the strategy was structurally silent (phantom-quote
        # pollution made sum_mid gate impossible). Replaced by
        # crypto_observer (8-hypothesis Phase 0 prevalence scanner).
        self.crypto_observer = CryptoObserverStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            price_feed=self.price_feed,
        )
        # Phase 1 paper traders promoted from crypto_observer (2026-05-14).
        # 24h cohort showed deribit_tail_skew 26/26 = 100% WR and
        # bid_imbalance 18/26 = 69.2% WR — both above break-even at the
        # observed price bands. Both paper-only at launch — no
        # LIVE_STRATEGIES env entry. See [[crypto_observer_phase1_promotion_2026-05-14]].
        self.deribit_tail_fade_v1 = DeribitTailFadeV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        self.bid_imbalance_v1 = BidImbalanceV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        # Flipped-prediction Phase 1 variants (2026-05-14). Phase 0 cohort
        # showed original observers' WR ≪ 50% — flipping yields +30% / break-even
        # ROI on n=325 / 10. New observers `open_no_touch` / `atm_hold` emit
        # flipped predictions; these traders execute them as paper trades.
        self.open_no_touch_v1 = OpenNoTouchV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        self.atm_hold_v1 = AtmHoldV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        # bid_imbalance_fade_v1 (2026-05-15): conditional-flip sibling for
        # ya<0.60. Full Phase 0 cohort (n=266) showed original 36% WR but
        # flipped 64% WR; ya<0.60 sub-cohort flips at 83% WR. Paper-only.
        self.bid_imbalance_fade_v1 = BidImbalanceFadeV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        # 2026-05-18 additions: gap-fill (late_close_tail) + new-hypothesis
        # (deribit_atm_fade). Both paper-only at launch; Phase 2 promotion
        # gate is n ≥ 100 settled + ROI ≥ +5%.
        self.late_close_tail_v1 = LateCloseTailV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        self.deribit_atm_fade_v1 = DeribitAtmFadeV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
        )
        # Macro Event Trader (C1) — paper-only at launch.
        # Requires scripts/macro_consensus_cron.py to populate consensus
        # cache. See spec:
        # docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
        self.macro_event = MacroEventStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client
        )
        # Crypto Fair-Value Trader (B2) — paper-only at launch.
        # Uses existing PriceFeed (Binance WS) + Deribit IV cron.
        # Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
        self.crypto_fair_value_orderbook_cache = OrderbookCache(
            client=self.kalshi_client,
            refresh_interval_secs=5.0,
            stale_threshold_secs=10.0,
        )
        self.crypto_fair_value = CryptoFairValueStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            price_feed=self.price_feed,
        )
        # Mid-price variant (paper-only A/B): uses bookTicker mid as spot
        # instead of kline-derived last-trade. Separate orderbook cache so
        # ATM windowing per variant stays independent.
        self.crypto_fair_value_mid_orderbook_cache = OrderbookCache(
            client=self.kalshi_client,
            refresh_interval_secs=5.0,
            stale_threshold_secs=10.0,
        )
        self.crypto_fair_value_mid_v1 = CryptoFairValueMidV1Strategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            price_feed=self.price_feed,
        )
        # Cross-Venue Polymarket Trader (B1) — paper-only at launch.
        # Reads gamma-api via 1-min cron. Spec:
        # docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
        self.xvenue_mapping = PolymarketKalshiMapping(
            "data/polymarket_kalshi_mapping.json"
        )
        self.xvenue_polymarket = XVenuePolymarketStrategy(
            self.config, self.order_manager, self.scanner, self.kalshi_client,
            mapping=self.xvenue_mapping,
        )
        # Chalk shadow — paper-only experimental strategy testing the
        # 2026-04-24 observer hypothesis that live-game 60-95¢ chalk YES
        # underprices residual uncertainty. ALLOWED_CELLS restricts to live
        # phase. Paper for 2-4 weeks before any live-promotion call.
        self.sports_chalk_shadow = SportsChalkShadowStrategy(
            self.config, self.order_manager, self.kalshi_client
        )
        # Read-only consensus snapshotter — no trades, no gates. Runs as a
        # dedicated 60s-cadence task (aligned with us-region cache TTL), not
        # in the main strategy loop. Feeds the odds_observations table for
        # 4D (region × sport × phase × ticker-price) analyses.
        self.sports_scan_observer = SportsScanObserver(
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
            asyncio.create_task(self._scan_observer_task()),
            asyncio.create_task(self._balance_schedule_task()),
            # B2 Crypto Fair-Value — its own asyncio task pair (1s scan +
            # 5s orderbook cache refresh). Separate from _strategy_loop
            # because of higher cadence.
            asyncio.create_task(
                self.crypto_fair_value_orderbook_cache.refresh_loop()
            ),
            asyncio.create_task(
                self.crypto_fair_value.scan_loop(
                    cache=self.crypto_fair_value_orderbook_cache,
                    scan_interval_secs=1.0,
                )
            ),
            # Mid-price variant: own cache refresh + scan loop. Paper-only.
            asyncio.create_task(
                self.crypto_fair_value_mid_orderbook_cache.refresh_loop()
            ),
            asyncio.create_task(
                self.crypto_fair_value_mid_v1.scan_loop(
                    cache=self.crypto_fair_value_mid_orderbook_cache,
                    scan_interval_secs=1.0,
                )
            ),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")

    async def _price_feed_task(self):
        tasks = [asyncio.create_task(self.price_feed.watchdog())]
        for symbol, url in self.config.BINANCE_WS_URLS.items():
            tasks.append(asyncio.create_task(self.price_feed.subscribe(symbol, url)))
        for symbol, url in self.config.BINANCE_BOOK_TICKER_URLS.items():
            tasks.append(
                asyncio.create_task(self.price_feed.subscribe_book_ticker(symbol, url))
            )
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
            # mentions_trader disabled 2026-04-17 — sports v31 trio is now the
            # main live strategy. Mentions accumulated 110 Kalshi positions
            # (~$200 tied capital) in KXFEDMENTION-* markets settling end of
            # April; those will self-resolve at the FOMC event. Also has a
            # known silent-drift bug (live limit fills don't record_trade, so
            # DB shows 0 mentions_trader rows despite real Kalshi positions).
            # Disabling prevents further capital commitment; revisit post-
            # settlement if sports hits its own cap.
            # ("mentions_trader", self.mentions_trader),
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
            # V3.2 + V3.3 paper variants — pre-game-only re-analysis 2026-04-24
            # (filtering out pre-2026-04-21 live entries that PRE_GAME_ONLY now
            # blocks anyway). 5 of 6 underperform v31 baseline:
            #   v32 longshot pre-only: -13.9% (n=24)  → retire
            #   v32 midshot  pre-only:  +2.1% (n=56)  → retire (v31 +18.9%)
            #   v33 longshot pre-only: -23.8% (n=23)  → retire (cap cuts winners)
            #   v33 midshot  pre-only:  -2.8% (n=55)  → retire
            #   v33 underdog pre-only: +21.5% (n=91)  → retire (v31 +19.7% — no edge over baseline)
            # Only winner: v32 underdog +27.1% (n=91) vs v31 underdog +19.7%.
            # Edge-scaled sizing genuinely helps for underdog (high-payoff
            # underdog signals = real high-EV). Keep as live-promotion candidate.
            # ("sports_longshot_v32", self.sports_longshot_v32),
            # ("sports_midshot_v32", self.sports_midshot_v32),
            ("sports_underdog_v32", self.sports_underdog_v32),
            # ("sports_longshot_v33", self.sports_longshot_v33),
            # ("sports_midshot_v33", self.sports_midshot_v33),
            # ("sports_underdog_v33", self.sports_underdog_v33),
            # V3.4 — V3.1 logic + region=us (paper-only). 2026-04-21 matrix
            # found us outperforming eu on 8 of 9 sport × base splits. Runs
            # in parallel with V3.1 (eu) to confirm the region finding on a
            # fresh sample post dead-zone + pre-game-only gate rollout.
            # sports_longshot_v34 retired 2026-04-26 — after removing the
            # structurally-dry (MLB,pre) cell, v34 longshot was identical to
            # v35 longshot's paper twin (both NBA pre us). v35's PAPER_TWIN
            # already covers paper observation; v34 was duplicate work.
            # ("sports_longshot_v34", self.sports_longshot_v34),
            ("sports_midshot_v34", self.sports_midshot_v34),
            ("sports_underdog_v34", self.sports_underdog_v34),
            # V3.5 — V3.4's (NBA, pre) us cell promoted to live trading
            # 2026-04-23. Coexists with V3.4 paper on same cell (V3.4 keeps
            # recording paper for matrix continuity; V3.5 fires live + paper
            # twin). Add v35 names to LIVE_STRATEGIES env on EC2 to activate.
            ("sports_longshot_v35", self.sports_longshot_v35),
            ("sports_midshot_v35", self.sports_midshot_v35),
            ("sports_underdog_v35", self.sports_underdog_v35),
            # V3.6 playoff — paper-only NBA-playoff underdog with 35¢ floor.
            # Targets the 35-45¢ band where observer + trades both showed
            # +135% / +153% ROI while 20-30¢ loses catastrophically. Promotion
            # gate: n_games ≥ 25 with ROI ≥ +30%.
            ("sports_playoff_underdog_v1", self.sports_playoff_underdog_v1),
            ("sports_playoff_underdog_v1_us", self.sports_playoff_underdog_v1_us),
            # V3.7 Soccer — paper-twin only at launch. Soccer 20-29c TEAM.
            ("sports_soccer_underdog_v1", self.sports_soccer_underdog_v1),
            # V3.8 Soccer Draw — DRAW side, EPL+LAL 0.25-0.30 YES only.
            ("sports_soccer_draw_v1", self.sports_soccer_draw_v1),
            # MLS Fade-Favorite V1 — paper-twin only. MLS TEAM no_ask 30-45c
            # BUY NO. Built 2026-05-15 from observer scan n=14 +18.6% ROI.
            ("sports_mls_fade_favorite_v1", self.sports_mls_fade_favorite_v1),
            # V3.6 WNBA — chalk-favorite 35-55c, WNBA-only.
            ("sports_underdog_v36", self.sports_underdog_v36),
            # V3.7 WNBA — TEAM 20-29c band, WNBA-only.
            ("sports_underdog_v37", self.sports_underdog_v37),
            # V4.0 Favorite — MLB 0.50-0.55 + NPB 0.55-0.65 paper-only.
            ("sports_favorite_v40", self.sports_favorite_v40),
            # 2026-05-15 tennis/MMA portfolio — paper-twin only at launch.
            ("sports_tennis_pinnacle_arb_v1", self.sports_tennis_pinnacle_arb_v1),
            ("sports_atp_gs_bo5_chalk_v1",    self.sports_atp_gs_bo5_chalk_v1),
            # A3 (sports_tennis_bid_imbalance_v1) held back 2026-05-15 per Phase 0
            # replay selection-bias guard — top cohort WR 57% (n=7) ≤ baseline
            # YES-band 58.8% / NO-band 60%, fails +6pp threshold. Re-evaluate when
            # observer data reaches n≥30 on imbalance band (~2-3 weeks).
            # ("sports_tennis_bid_imbalance_v1", self.sports_tennis_bid_imbalance_v1),
            ("sports_ufc_chalk_fade_v1",      self.sports_ufc_chalk_fade_v1),
            ("sports_tennis_surface_skew_v1", self.sports_tennis_surface_skew_v1),
            # Tennis underdog band — paper-only. Built 2026-05-19 from observer
            # backtest. See sports_tennis_underdog_v1 module docstring.
            ("sports_tennis_underdog_v1",     self.sports_tennis_underdog_v1),
            # sports_pinnacle_edge disabled 2026-04-17 — 37 settled trades at
            # -37.5% ROI. Freshness gate was added 2026-04-16 (80ad42e) but
            # only a handful of post-gate entries accumulated before shutdown;
            # re-enable only after a fresh evaluation window if signal quality
            # materially changes.
            # ("sports_pinnacle_edge", self.sports_pinnacle_edge),
            ("sports_v4_edge", self.sports_v4_edge),
            # sports_chalk_shadow retired 2026-04-26 — paper experiment
            # (launched 2026-04-24) closed at n=45 settled / -18.5% ROI.
            # Both price buckets lost: 60-75¢ at 50% win (need 65%), 75¢+ at
            # 80% win (need 84%). Observer-derived 100%-win signal was
            # selection-biased; Kalshi prices live chalk correctly.
            # ("sports_chalk_shadow", self.sports_chalk_shadow),
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
            # Ladder Consistency Arb (A1) — paper-only at launch (no entry in
            # LIVE_STRATEGIES env). Phase 0 prevalence research must validate
            # per-sub-strategy fire rate before live promotion.
            ("ladder_shutdown", self.ladder_shutdown),
            ("ladder_approval", self.ladder_approval),
            ("ladder_fed", self.ladder_fed),
            # ladder_crypto retired 2026-05-13 — see CryptoObserverStrategy
            # init for full postmortem. Replacement runs 8 hypothesis-based
            # observers in parallel; first promotion eval in ~1 week.
            ("crypto_observer", self.crypto_observer),
            # Phase 1 paper traders promoted from crypto_observer 2026-05-14.
            # Paper-only until n ≥ 100 settled + ROI ≥ +5% for live promotion.
            ("deribit_tail_fade_v1", self.deribit_tail_fade_v1),
            ("bid_imbalance_v1", self.bid_imbalance_v1),
            ("open_no_touch_v1", self.open_no_touch_v1),
            ("atm_hold_v1", self.atm_hold_v1),
            ("bid_imbalance_fade_v1", self.bid_imbalance_fade_v1),
            ("late_close_tail_v1", self.late_close_tail_v1),
            ("deribit_atm_fade_v1", self.deribit_atm_fade_v1),
            # Macro Event Trader (C1) — paper-only. Requires consensus cron.
            ("macro_event", self.macro_event),
            # Cross-Venue Polymarket Trader (B1) — paper-only. Requires
            # scripts/polymarket_quote_cron.py running separately.
            ("xvenue_polymarket", self.xvenue_polymarket),
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
        """Reset circuit-breaker daily-loss counter at UTC midnight. The
        notification arm of this task was removed 2026-04-27 — paper-bankroll
        + entry-timestamp counts produced an effectively useless message
        (W/L typically 0/0 because most same-day entries have pnl=0). The
        09:00 + 22:00 UTC scripts/daily_report.py runs and the 14:00 +
        22:00 ET balance_schedule alerts cover the same ground better."""
        while self._running:
            import datetime
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            tomorrow = utc_now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
            seconds_until_midnight = (tomorrow - utc_now).total_seconds()

            await asyncio.sleep(seconds_until_midnight)

            if not self._running:
                break

            self.order_manager.reset_daily()
            logger.info("Daily reset complete (circuit-breaker counters cleared)")

    async def _balance_schedule_task(self):
        """Push a Telegram balance summary at 14:00 + 22:00 ET. Bracket the
        primary trading peak: 14:00 lets the user top up before the 16:00 ET
        cluster window; 22:00 lets the user verify the post-peak cushion
        before the 02:00-08:00 ET residual-signal tail (the time-of-day
        when the 2026-04-27 missed-entries incident happened)."""
        import datetime
        while self._running:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            target_utc = next_balance_check_time(now_utc)
            wait_s = max(1.0, (target_utc - now_utc).total_seconds())
            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            try:
                positions = self.kalshi_client.get_positions() or []
                positions = [
                    p for p in positions
                    if float(p.get("position_fp", 0)) != 0
                ]
                exposure = sum(
                    float(p.get("market_exposure_dollars", 0)) for p in positions
                )
                balance = self.kalshi_client.get_balance(max_age_s=60) or 0.0
                hour_et = target_utc.astimezone(BALANCE_CHECK_TZ).hour
                msg = format_balance_check_message(
                    hour_et=hour_et,
                    balance=balance,
                    total_kalshi=balance + exposure,
                    open_positions=len(positions),
                )
                await self.notifier.send(msg, msg_type="balance_check")
                logger.info(
                    f"[BALANCE] {hour_et}:00 ET check sent: "
                    f"free=${balance:.2f} pos={len(positions)} "
                    f"total=${balance + exposure:.2f}"
                )
            except Exception as e:
                logger.exception(f"[BALANCE] scheduled check failed: {e}")

    async def _scan_observer_task(self):
        """Snapshot consensus + Kalshi prices for every open sports market
        every 60s. Reuses the shared (region, sport) odds cache so this
        adds zero Odds API calls beyond what strategies already trigger."""
        while self._running:
            try:
                await self.sports_scan_observer.scan_and_execute()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"scan observer error: {e}")
            for _ in range(12):
                if not self._running:
                    return
                await asyncio.sleep(5)

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
