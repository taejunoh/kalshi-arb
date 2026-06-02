import asyncio
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger("order_manager")

# Strategies that should use limit+TTL instead of market orders for live fills.
# Rationale: V31 entries are small ($3), low-priced (often <30¢), which produces
# high contract counts that exceed the top-of-book depth → Kalshi's market order
# is fill-or-kill and rejects with 409. Limit at signal price mirrors paper
# semantics (same price, same size eventually) and sheds the FOK risk. Unfilled
# limits cancel after LIMIT_TTL_SECONDS so the position doesn't drift off-signal.
LIMIT_TTL_STRATEGIES = {
    "sports_longshot_v31",
    "sports_midshot_v31",
    "sports_underdog_v31",
    "sports_underdog_v32",  # 2026-04-25 live-promoted; paper +168% ROI (n=67 pre) vs v31 +88%
    "sports_v4_edge",  # 2026-04-20 live-promoted; same $3 / 5-35¢ profile as v31
    "sports_longshot_v35",  # 2026-04-23 live-promoted (NBA, pre) us cell from v34
    "sports_midshot_v35",
    "sports_underdog_v35",
    "sports_playoff_underdog_v1",  # 2026-04-30 live-promoted on observer +135% n=10 + trades +153% n=9
    "sports_playoff_underdog_v1_us",  # 2026-04-30 us companion to v36_playoff
    "sports_soccer_underdog_v1",  # 2026-05-06 paper-twin launch — soccer 20-29c TEAM
    "sports_soccer_draw_v1",  # 2026-05-11 live launch — soccer DRAW EPL+LAL 0.25-0.30
    # 2026-05-13 WNBA expansion. Both fired correctly post-launch but live
    # MARKET orders 409'd on Kalshi (`fill_or_kill_insufficient_resting_volume`)
    # because WNBA early-pre-game books are thin (LV-CONN-CONN 23:51 UTC had
    # <50 contracts at the YES 26c ask). Paper twins filled both; live didn't.
    # Same failure mode that retired v36_nhl_playoff (5/3) — same fix.
    "sports_underdog_v36",
    "sports_underdog_v37",
    # 2026-05-19 crypto Phase 1 LIVE traders. deribit_tail_fade_v1 hit
    # `fill_or_kill_insufficient_resting_volume` 5/19 22:54-22:58 on
    # KXETH-26MAY1919-B2090 NO @ 0.98 — 6 cancelled live attempts since
    # 5/18 promo, 0 real fills. Same pattern as the WNBA / NHL playoff
    # FOK rejects above. atm_hold_v1 added defensively — ATM-bucket
    # books are thicker than tails but signal cohort overlaps the same
    # 5-min-to-close window so prevention is cheap.
    "deribit_tail_fade_v1",
    "atm_hold_v1",
    # 2026-05-21 14:33 UTC promotion. Narrow-gate ya [0.17, 0.25] (dffc257)
    # paper 5h: 53 settled / WR 96% / +$91.26 / avg +$1.72. NO entry band
    # 0.74-0.87 has $0.13-0.26/ct gross margin — survives +3¢ slip + Kalshi
    # taker fee ($0.07/ct flat) unlike deribit_tail_fade's 0.93-0.97 band
    # where margin = fee (22/36 LIVE wipe-to-zero pattern). LIMIT_TTL
    # routing same as sister tail-NO strategies.
    "cme_basis_fade_v1",
    # 2026-05-21 LIVE promotion. Mid-OTM (ya 0.05-0.30) NO fader in 10-min
    # pre-close window. DB-replay sim (n=133 settled, 5/19-5/21 62h window)
    # showed Config C (MAX_NO_ASK 0.94 + EXCLUDE 0.88-0.89) yields ~$23/day
    # with worst-trade $0 — all 32 settled fires in the gated cohort won.
    # Thin-tail FOK same exposure as deribit_tail_fade → LIMIT_TTL required.
    "late_close_tail_v1",
}
LIMIT_TTL_SECONDS = 60
LIMIT_TTL_POLL_INTERVAL = 5

# Cents of aggression over signal price for v31 marketable limits.
# Why: a limit posted AT signal price only fills when the market drifts
# against our signal first (classic adverse selection). Observed
# 2026-04-16: paper twins of live-filled signals won 17-38%, while
# paper twins of the unfilled signals won 33-55% — a ~17pp gap across
# all 3 v31 strategies. Crossing the spread by 2¢ turns the order into
# a taker that hits any resting ask at-or-below (signal + 2¢), capturing
# the "market was at or better than signal" pool that adverse selection
# was filtering out. Kalshi limits support partial fills so the FOK
# issue that market orders had at thin strikes doesn't reappear.
#
# Raised 2→15 on 2026-04-25. Diagnostic showed adverse-selection gap had
# grown to 124-1931pp (LIVE/TWIN ROI vs SOLO_PAPER post-promo): live-filled
# signals lose −3% to −276%, while signals where the live limit didn't
# fill within TTL settled at +71% to +1665%. The 2¢ cross was no longer
# enough to capture the alpha that escaped before fill. 15¢ acts as a
# safety-capped market order — fill price is still best ask at execution
# time, but the ceiling lets us take through 15 ticks of book if the
# market has moved during signal generation latency. Above 15 the marginal
# benefit plateaus (60s price moves rarely exceed 15¢ on Kalshi sports).
LIMIT_AGGRESSION_CENTS = 15

# Per-strategy overrides for unfilled-alpha capture (2026-05-11). Paired
# paper-vs-live analysis over 4/26-5/11 showed three strategies whose
# unfilled signals were 100% paper-wins (mechanism: book agreed with our
# signal and moved the ask past our 15¢ cap before we could cross). The
# other v3X strategies had unfilled cohorts dominated by losers (TTL acted
# as an adverse-selection shield) — those keep the 15¢ default.
#   sports_midshot_v31:         2/2 wins, paper +$103
#   sports_playoff_underdog_v1: 4/4 wins, paper +$22
#   sports_playoff_underdog_v1_us:     1/1 win,  paper +$12
# Bump from 15→25 captures asks in the +15-25¢ band without affecting the
# full-fill cohort (avg realized slip 0.98¢ — depth is in book front, the
# wider cap only matters when book has moved past +15¢).
# Re-eval ~2026-05-25 (1 month at current ~1 unfilled / 2 days pace).
LIMIT_AGGRESSION_CENTS_BY_STRATEGY = {
    "sports_midshot_v31": 25,
    "sports_playoff_underdog_v1": 25,
    "sports_playoff_underdog_v1_us": 25,
    # 2026-05-11 v37 unfilled analysis: 2/17 paper trades did not fill live.
    # Paper-only cohort: 1W (+$18.61 ATMRCC) / 1L (-$8.00 SEVESP) = +$10.61
    # net, explains 87% of live-vs-paper $10.38 gap. Not the unanimous
    # 100% paper-win signal that other 25c overrides had — applied based on
    # user judgment that the structural pattern (60s TTL leaving alpha) likely
    # repeats. Re-eval at ~2026-05-25 with more data.
    "sports_soccer_underdog_v1": 25,
    # 2026-05-11 launch-time defensive override. v38 inherits v37's
    # execution mechanics (same MAX_MTS_AT_ENTRY, same depth gate, same
    # 0.25-0.30 band). No v38 fills yet to measure unfilled-cohort
    # directly, but if v37 leaks alpha through 15c ceiling at this band,
    # v38 likely does too. Re-eval at ~2026-05-25 alongside v37.
    "sports_soccer_draw_v1": 25,
    # 2026-05-13 WNBA launch-time defensive override. Same rationale as
    # soccer_draw_v1 above: thin-book sport (WNBA depth p50 ~$567), no
    # filled-cohort data yet to measure unfilled alpha. v37's 0.20-0.29
    # band matches soccer_underdog_v1 exactly; v36's 0.35-0.55 chalk band
    # similar thin-book regime. Re-eval at ~2026-05-25 with launch data.
    "sports_underdog_v36": 25,
    "sports_underdog_v37": 25,
    "sports_tennis_pinnacle_arb_v1": 25,    # 2026-05-15 thin-book tennis defensive
    "sports_atp_gs_bo5_chalk_v1": 25,       # 2026-05-15 GS BO5 chalk paper-twin
    "sports_tennis_bid_imbalance_v1": 25,
    "sports_ufc_chalk_fade_v1": 25,         # 2026-05-15 UFC slow-book defensive
    "sports_tennis_surface_skew_v1": 25,
    # 2026-05-21 deribit_tail_fade_high_v1 LIVE soft-launch: aggression=0
    # to match the Phase 0 14d sim (no-slip assumption) that drove the GO
    # decision. High-ya near-expiry strikes (NO ask 0.10-0.35) have win
    # net 4-7x the loss net, so even 5c of slip materially erodes EV
    # (28% reduction at the band edges). Accept higher FOK reject rate
    # in exchange for paper-parity fills. See
    # [[deribit_tail_fade_high_v1_live_promotion_2026-05-21]].
    "deribit_tail_fade_high_v1": 0,
}


def _aggression_cents_for(strategy: str) -> int:
    return LIMIT_AGGRESSION_CENTS_BY_STRATEGY.get(strategy, LIMIT_AGGRESSION_CENTS)


# Per-strategy TTL overrides (2026-05-19). Default 60s targets sports
# thin-book markets where new makers arrive every 10-20s and 1-min spot
# drift is small relative to per-trade alpha. Tuned per-strategy by ROI
# (not fill rate alone — long TTL costs alpha on stale-sensitive signals):
#
#   atm_hold_v1 = 20s. ATM bucket-flip threshold ~0.08% spot drift (BTC,
#   grid ~$250 / 0.33%) / ~0.12% (ETH, grid ~$10 / 0.47%). Empirical 60s
#   |Δ%| p75 on last 7d: BTC 0.084%, ETH 0.110% — both straddle the flip
#   line, so ~20-25% of 60s windows invalidate the signal post-placement.
#   Per-trade EV at 60s = $0.21; at 20s = $0.24 (-14% adverse-selection
#   saved). 20s catches ~82% fill rate (vs 92% at 60s) with ~8% stale rate
#   (vs 25%); the smaller fill is more than offset by cleaner alpha.
#
#   deribit_tail_fade_v1 = 60s (default). Deep tail (≥1% from spot) is
#   stale-immune — 1% move in 60s is BTC 0/1947 days, ETH 3/1925 = 0.16%.
#   Fill rate dominates per-trade alpha: 60s catches ~17 contracts
#   ($0.17 alpha) vs 15s ~12 contracts ($0.12 alpha). No override =
#   inherit default 60s.
#
#   deribit_tail_fade_high_v1 = 180s. LIVE soft-launch 5/21 23:01 UTC →
#   5/22 02:02 UTC posted 23 LIMIT orders, 0 filled (100% TTL-cancel).
#   aggression=0 (paper-parity, see LIMIT_AGGRESSION_CENTS_BY_STRATEGY)
#   posts passively at signal price; high-ya cohort (NO 0.20-0.35 and
#   deep-NO 0.85-0.94) maker flow at signal exact price is sparse, so
#   60s TTL almost never gets hit. Same stale-immunity as the parent
#   trader (1% move in 180s still ~0/day BTC, ~0.16/day ETH), so we
#   spend more time on the bid without bleeding alpha to drift. Partial
#   fills are captured (place_limit_order polls fill_count_fp every
#   interval). Target: lift fill rate from 0% to 20-40% over next 24h
#   while holding the Phase 0 sim's no-slip parity. Re-eval 5/23.
LIMIT_TTL_SECONDS_BY_STRATEGY = {
    "atm_hold_v1": 20,
    "deribit_tail_fade_high_v1": 180,
}


def _ttl_seconds_for(strategy: str) -> int:
    return LIMIT_TTL_SECONDS_BY_STRATEGY.get(strategy, LIMIT_TTL_SECONDS)

# Strategies that should record a paper twin alongside every live entry.
# Rationale: keep generating paper_filled rows under the same strategy name
# so paper and live stats accumulate in parallel from the same signal. The
# twin runs idealized (instant fill at signal price) and diverges from live
# only where Kalshi execution forces it (TTL expiry, partial fill). Dashboard
# already splits paper/live by status for each strategy card.
PAPER_TWIN_STRATEGIES = {
    "sports_longshot_v31",
    "sports_midshot_v31",
    "sports_underdog_v31",
    "sports_underdog_v32",  # 2026-04-25 live-promoted — keep paper twin for sizing A/B
    "sports_v4_edge",  # 2026-04-20 live-promoted — keep paper stream for live/paper A/B
    "sports_longshot_v35",  # 2026-04-23 live-promoted — keep paper twin alongside live
    "sports_midshot_v35",
    "sports_underdog_v35",
    "sports_playoff_underdog_v1",  # 2026-04-30 live-promoted — keep paper twin for slippage A/B
    "sports_playoff_underdog_v1_us",  # 2026-04-30 us companion
    "sports_soccer_underdog_v1",  # 2026-05-06 paper-twin launch — soccer 20-29c TEAM
    "sports_soccer_draw_v1",  # 2026-05-11 live launch — soccer DRAW EPL+LAL 0.25-0.30
    "sports_underdog_v36",   # 2026-05-13 WNBA expansion — chalk-favorite
    "sports_underdog_v37",   # 2026-05-13 WNBA expansion — 20-29c TEAM band
    "sports_tennis_pinnacle_arb_v1",   # 2026-05-15 A1 paper-twin launch
    "sports_atp_gs_bo5_chalk_v1",      # 2026-05-15 A2 paper-twin launch
    "sports_tennis_bid_imbalance_v1",  # 2026-05-15 A3 paper-twin launch
    "sports_ufc_chalk_fade_v1",        # 2026-05-15 B1 paper-twin launch
    "sports_tennis_surface_skew_v1",   # 2026-05-15 C paper-twin launch
    "deribit_tail_fade_v1",            # 2026-05-18 crypto Phase 1 live promotion
    "atm_hold_v1",                     # 2026-05-18 crypto Phase 1 live promotion
    "cme_basis_fade_v1",               # 2026-05-21 14:33 UTC crypto Phase 1 live promotion
    "late_close_tail_v1",              # 2026-05-21 PM crypto Phase 1 live promotion (Config C)
}


class OrderManager:
    def __init__(self, config, kalshi_client, database, notifier=None):
        self.config = config
        self._client = kalshi_client
        self._db = database
        self._notifier = notifier
        self.bankroll = config.INITIAL_BANKROLL
        self._daily_loss_paper = 0.0
        self._daily_loss_live = 0.0
        self._consecutive_losses = 0
        self._pause_until = 0.0
        # 2026-05-22: one-shot Telegram alert per UTC day per bucket when the
        # daily-loss cap first trips. Without this, a cap trip silently blocks
        # all entries until 00:00 UTC reset — bot can sit quiet for 15+ hours
        # before the next-morning daily_live_audit surfaces it. Flags reset in
        # reset_daily().
        self._cap_alert_paper_fired = False
        self._cap_alert_live_fired = False
        self._positions: dict[tuple[str, str], dict] = {}  # (strategy, ticker) -> position
        self._quota_exhausted = False  # Set when Odds API remaining < QUOTA_HALT_THRESHOLD (5%)
        # None until first fetch seeds it to the current bucket (avoids firing a
        # stale-bucket alert on every restart); thereafter set to last-alerted bucket.
        self._quota_last_alert_bucket: int | None = None
        # Shared per-game stake accounting for v33 variants. v33 caps aggregate
        # exposure across longshot/midshot/underdog on the same game (paper
        # analysis 2026-04-17 showed 75% intra-game outcome correlation). Key
        # is the market_id prefix without -TEAM suffix. Grows monotonically
        # since game_ids are date-scoped and we simply never rewind past entries.
        self._v33_game_spend: dict[str, float] = {}
        # Low-balance proactive alert state. Fires once when free cash crosses
        # below LOW_BALANCE_THRESHOLD; re-arms once balance recovers above
        # LOW_BALANCE_RECOVERY (hysteresis prevents oscillation spam). Pairs
        # with the post-reject alerts and 14:00/22:00 ET scheduled checks to
        # close the gap when an evening cluster drains cash between checks.
        self._low_balance_armed: bool = True

    LOW_BALANCE_THRESHOLD: float = 50.0
    LOW_BALANCE_RECOVERY: float = 100.0

    def check_low_balance(self, balance: Optional[float]) -> None:
        """Hysteresis: alert when balance first dips below threshold; re-arm
        only after it recovers above LOW_BALANCE_RECOVERY. Called once per
        scan cycle from the strategy loop after the per-cycle balance fetch."""
        if balance is None:
            return
        if self._low_balance_armed and balance < self.LOW_BALANCE_THRESHOLD:
            self._low_balance_armed = False
            if self._notifier:
                text = (
                    f"⚠️ <b>Free cash below ${self.LOW_BALANCE_THRESHOLD:.0f}</b>\n"
                    f"Balance: ${balance:.2f}\n"
                    f"Next signal cluster will likely reject — top up cash now "
                    f"to keep entries flowing.\n"
                    f"Alert re-arms once balance recovers above "
                    f"${self.LOW_BALANCE_RECOVERY:.0f}."
                )
                try:
                    asyncio.create_task(
                        self._notifier.send(text, msg_type="balance_low")
                    )
                except RuntimeError:
                    pass
            logger.warning(
                f"[BALANCE] Low-balance alert fired at ${balance:.2f} "
                f"(threshold ${self.LOW_BALANCE_THRESHOLD:.0f})"
            )
        elif not self._low_balance_armed and balance >= self.LOW_BALANCE_RECOVERY:
            self._low_balance_armed = True
            logger.info(
                f"[BALANCE] Low-balance alert re-armed at ${balance:.2f} "
                f"(recovery ${self.LOW_BALANCE_RECOVERY:.0f})"
            )

    def calculate_position_size(self, net_edge: float) -> float:
        kelly_size = self.bankroll * self.config.KELLY_FRACTION * net_edge
        max_size = self.bankroll * self.config.MAX_SINGLE_TRADE_PCT
        return min(kelly_size, max_size)

    def is_kill_switch_active(self) -> bool:
        """True if /tmp/kalshi_stop exists. Emergency halt for all new entries.
        Existing positions still settle. Override path via KILL_SWITCH_FILE env."""
        path = os.environ.get("KILL_SWITCH_FILE", "/tmp/kalshi_stop")
        return os.path.exists(path)

    def is_circuit_breaker_active(self, strategy: Optional[str] = None) -> bool:
        # Kill-switch file: instant halt for all new entries across strategies.
        # Existing open positions continue to settle via the settlement task.
        if self.is_kill_switch_active():
            return True
        # Quota halt — only blocks sports_* strategies (others don't use Odds API)
        if self._quota_exhausted and strategy and strategy.startswith("sports_"):
            return True
        # NOTE: consecutive-loss pause is intentionally GLOBAL across paper+live.
        # 5 consecutive losses anywhere pauses ALL entries for 15min. Rationale:
        # a losing streak is likely due to a systemic signal issue (bad data,
        # API lag) that affects both modes equally. Daily-loss caps ARE split.
        if self._consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
            if time.time() < self._pause_until:
                return True
            self._consecutive_losses = 0
        # Daily loss caps: either strategy-specific or either-breached
        if strategy is not None:
            if self._is_live(strategy):
                return self._daily_loss_live >= self.config.MAX_DAILY_LOSS_LIVE
            return self._daily_loss_paper >= self.config.MAX_DAILY_LOSS
        # No strategy given: conservative — True if EITHER cap breached
        if self._daily_loss_paper >= self.config.MAX_DAILY_LOSS:
            return True
        if self._daily_loss_live >= self.config.MAX_DAILY_LOSS_LIVE:
            return True
        return False

    def record_loss(self, amount: float = 0.0, strategy: Optional[str] = None,
                    entry_today: bool = True):
        # 2026-05-22: Daily-loss cap counts TODAY's entries only. Settlement of
        # an OLD entry (filled yesterday or earlier) still ticks the
        # consecutive-loss counter (cheap 15-min pause if a cluster lands) but
        # does NOT charge today's $200 daily budget — those are sunk decisions
        # from a prior day, not today's risk-taking. Without this split, a
        # routine WNBA/MLB settle cluster could trip the cap by 02:30 UTC and
        # silence the bot for the rest of the day even on a net-positive day.
        self._consecutive_losses += 1
        if entry_today:
            if strategy is not None and self._is_live(strategy):
                before = self._daily_loss_live
                self._daily_loss_live += amount
                self._maybe_alert_cap_trip(
                    "LIVE", before, self._daily_loss_live,
                    self.config.MAX_DAILY_LOSS_LIVE,
                )
            else:
                # Legacy callers (no strategy) land here with paper bucket
                before = self._daily_loss_paper
                self._daily_loss_paper += amount
                self._maybe_alert_cap_trip(
                    "PAPER", before, self._daily_loss_paper,
                    self.config.MAX_DAILY_LOSS,
                )
        if self._consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES:
            self._pause_until = time.time() + 900
            logger.warning(f"Circuit breaker: {self._consecutive_losses} consecutive losses. Pausing.")

    def _maybe_alert_cap_trip(self, bucket: str, before: float, after: float, cap: float):
        # One-shot Telegram alert the instant a bucket's _daily_loss_* crosses
        # the cap. Critical because the cap blocks ALL entries in that bucket
        # until the next 00:00 UTC reset_daily() — without an immediate signal
        # the bot can sit silent for hours, only surfacing in the next-morning
        # daily_live_audit. Sync method, schedules async send via the running
        # event loop; no-op if no notifier or no running loop (tests).
        fired_attr = "_cap_alert_live_fired" if bucket == "LIVE" else "_cap_alert_paper_fired"
        if getattr(self, fired_attr):
            return
        if not (before < cap <= after):
            return
        setattr(self, fired_attr, True)
        logger.warning(
            f"[CAP-TRIP] {bucket} daily loss ${after:.2f} ≥ cap ${cap:.2f} — "
            f"all {bucket} entries blocked until 00:00 UTC reset"
        )
        if self._notifier is None or not getattr(self._notifier, "is_enabled", False):
            return
        msg = (
            f"🚨 <b>Daily-loss cap tripped — {bucket}</b>\n"
            f"_daily_loss_{bucket.lower()} = ${after:.2f} ≥ cap ${cap:.2f}\n"
            f"All {bucket} entries blocked until 00:00 UTC reset.\n"
            f"If today's net is positive, consider <code>systemctl restart kalshi-bot</code>."
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No loop (unit tests etc.) — log-only is sufficient
        loop.create_task(self._notifier.send(msg, msg_type="cap_trip"))

    def record_win(self, amount: float = 0.0, strategy: Optional[str] = None):
        """Reset consecutive loss counter on a win.

        `strategy` is accepted for API symmetry with record_loss but unused —
        we don't track daily wins by bucket (only losses gate circuit breakers).
        Keep the parameter so settlement can call record_win/record_loss with
        the same signature without branching.
        """
        self._consecutive_losses = 0

    def reset_daily(self):
        self._daily_loss_paper = 0.0
        self._daily_loss_live = 0.0
        # Re-arm cap-trip alerts so tomorrow's trip (if any) sends Telegram again
        self._cap_alert_paper_fired = False
        self._cap_alert_live_fired = False

    async def hydrate_bankroll(self) -> None:
        """Restore bankroll from DB if present (survives restarts). No-op in live
        mode — main.py's real_balance sync overrides after this if applicable."""
        if self._db is None:
            return
        stored = await self._db.get_bot_state("bankroll")
        if stored is not None:
            self.bankroll = stored
            logger.info(f"Hydrated bankroll from DB: ${self.bankroll:.2f}")

    async def _persist_bankroll(self) -> None:
        """Write current bankroll to DB. Called after every mutation."""
        if self._db is None:
            return
        try:
            await self._db.set_bot_state("bankroll", self.bankroll)
        except Exception as e:
            logger.warning(f"Bankroll persist failed: {e}")

    # ── Position tracking ────────────────────────────────────────────

    def get_position(self, strategy: str, ticker: str) -> dict:
        return self._positions.get((strategy, ticker), {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0})

    def update_position(self, strategy: str, ticker: str, side: str, qty: int, price: float, size_usdc: float):
        key = (strategy, ticker)
        pos = self._positions.get(key, {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0})
        if pos["side"] == side or pos["qty"] == 0:
            total_cost = pos["avg_price"] * pos["qty"] + price * qty
            pos["qty"] += qty
            pos["avg_price"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0.0
            pos["side"] = side
            pos["size_usdc"] += size_usdc
        else:
            # Opposite side reduces position
            pos["qty"] -= qty
            pos["size_usdc"] -= size_usdc
            if pos["qty"] <= 0:
                pos = {"side": None, "qty": 0, "avg_price": 0.0, "size_usdc": 0.0}
        self._positions[key] = pos

    def get_strategy_exposure(self, strategy: str) -> float:
        return sum(p["size_usdc"] for (s, _), p in self._positions.items() if s == strategy)

    async def requote(self, old_order_id: str, ticker: str, side: str, new_price: float,
                      size_usdc: float, strategy: str):
        """Cancel existing order and place a new one. Returns new order result."""
        cancelled = await self.cancel_order(old_order_id)
        if not cancelled:
            logger.warning(f"Requote: failed to cancel {old_order_id}, skipping replacement")
            return None
        return await self.limit_order(ticker, side, new_price, size_usdc, strategy, ticker)

    # ── Order execution ────────────────────────────────────────────

    def _alert_dispatch_failure(
        self, *, strategy: str, ticker: str, side: str,
        order_id: str, filled_count: int, size_usdc: float, price: float,
        error: str,
    ) -> None:
        """Fire critical alert when record_trade fails after a Kalshi
        fill — orphan order risk. Built 2026-05-11 after 5/9 v37 incident
        where 2 trades (ATMRCC + SEVESP) filled on Kalshi but DB writes
        failed with 'database is locked', leaving no trade records. Without
        this alert, the issue wasn't discovered for 2+ days.
        """
        if not self._notifier:
            return
        text = (
            f"🚨 <b>ORPHAN ORDER RISK: record_trade failed after fill</b>\n"
            f"Strategy: {strategy}\n"
            f"Market: {ticker}\n"
            f"Side: {side} | Filled: {filled_count} contracts | "
            f"Signal: ${size_usdc:.2f} @ {price:.3f}\n"
            f"Kalshi order_id: {order_id}\n"
            f"Error: {error}\n"
            f"<b>Manual reconciliation needed</b>: check Kalshi fills "
            f"vs trades table for this ticker."
        )
        try:
            asyncio.create_task(
                self._notifier.send(text, msg_type="dispatch_failure")
            )
        except RuntimeError:
            pass

    def _alert_balance_reject(
        self, *, strategy: str, ticker: str, reason: str,
        balance: Optional[float] = None, required: Optional[float] = None,
    ) -> None:
        """Fire a Telegram alert when a live entry is rejected before reaching
        Kalshi's book (balance check). Unthrottled so cluster rejections each
        produce their own alert. The post-2026-04-27 dedup-rollback fix means
        the bot will retry on the next scan; this alert tells the user to top
        up cash so the retry succeeds at a price close to the original signal.
        """
        if not self._notifier:
            return
        if reason == "insufficient_balance" and balance is not None and required is not None:
            short = required - balance
            text = (
                f"⚠️ <b>Live entry blocked: insufficient balance</b>\n"
                f"Strategy: {strategy}\n"
                f"Market: {ticker}\n"
                f"Balance: ${balance:.2f} / Required: ${required:.2f} (short ${short:.2f})\n"
                f"Dedup auto-rollback — bot will retry once cash recovers."
            )
        else:
            text = (
                f"⚠️ <b>Live entry blocked: balance check failed</b>\n"
                f"Strategy: {strategy}\n"
                f"Market: {ticker}\n"
                f"Reason: {reason} (Kalshi API)\n"
                f"Dedup auto-rollback — bot will retry next scan."
            )
        try:
            asyncio.create_task(self._notifier.send(text, msg_type="balance_reject"))
        except RuntimeError:
            # No running event loop (sync test contexts) — skip silently.
            pass

    def _is_live(self, strategy: str) -> bool:
        """Check if a strategy should trade live (real money)."""
        if not self.config.is_paper_mode:
            return True  # Global live mode
        return strategy in self.config.LIVE_STRATEGIES

    async def market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                           minutes_to_start: Optional[float] = None):
        if self._is_live(strategy):
            logger.info(f"[LIVE] {strategy} {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            # Paper twin: same signal, same strategy name, idealized fill.
            # Runs as a background task so it doesn't block the live path.
            # Paper bankroll may be too low for the twin — that's fine, the
            # live path continues and the twin simply skips. We do NOT gate
            # the live order on paper bankroll: real Kalshi balance is
            # verified inside _live_market_order and _live_limit_ttl_order.
            if strategy in PAPER_TWIN_STRATEGIES:
                asyncio.create_task(self._paper_market_order(
                    ticker, side, size_usdc, price, strategy, market_id,
                    minutes_to_start=minutes_to_start,
                ))
            if strategy in LIMIT_TTL_STRATEGIES:
                result = await self._live_limit_ttl_order(
                    ticker, side, size_usdc, price, strategy, market_id,
                    minutes_to_start=minutes_to_start,
                )
            else:
                result = await self._live_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                                       minutes_to_start=minutes_to_start)
            if self._notifier and result.get("status") == "filled":
                # Build a richer msg when realized fill is available (Task 4)
                realized = None
                try:
                    import sqlite3
                    conn = sqlite3.connect(self._db.db_path, timeout=30.0)
                    row = conn.execute(
                        "SELECT realized_fill_price FROM trades WHERE id=?",
                        (result.get("trade_id"),),
                    ).fetchone()
                    conn.close()
                    if row and row[0] is not None:
                        realized = row[0]
                except Exception:
                    pass
                slip_line = ""
                if realized is not None:
                    slip_cents = (realized - price) * 100
                    slip_line = f"\nRealized: {realized:.3f} (slip {slip_cents:+.1f}¢)"
                await self._notifier.send(
                    f"📊 <b>LIVE Market Order</b>\n"
                    f"Strategy: {strategy}\n"
                    f"Market: {ticker}\n"
                    f"{side.upper()} @ {price:.3f} | ${size_usdc:.2f}{slip_line}",
                    msg_type="trade",
                )
            return result
        # Paper path: gate on paper bankroll. Mirrors limit_order's pattern
        # so paper bankroll can't constrain live orders (live uses real
        # Kalshi balance inside the live dispatchers).
        if self.bankroll < size_usdc:
            logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
            return {"status": "skipped", "reason": "insufficient_bankroll"}
        return await self._paper_market_order(ticker, side, size_usdc, price, strategy, market_id,
                                              minutes_to_start=minutes_to_start)

    async def limit_order(self, ticker, side, price, size_usdc, strategy, market_id,
                          minutes_to_start: Optional[float] = None):
        if self._is_live(strategy):
            # Check real Kalshi balance before placing live order
            real_balance = self._client.get_balance(max_age_s=60)
            if real_balance is not None and real_balance < size_usdc:
                logger.debug(f"Insufficient Kalshi balance: ${real_balance:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
                return {"status": "skipped", "reason": "insufficient_balance"}
            logger.info(f"[LIVE] {strategy} LIMIT {side} {ticker} ${size_usdc:.2f} @ {price:.3f}")
            result = await self._live_limit_order(ticker, side, price, size_usdc, strategy, market_id)
            if self._notifier and result.get("status") != "failed":
                await self._notifier.send(
                    f"📊 <b>LIVE Order</b>\n"
                    f"Strategy: {strategy}\n"
                    f"Market: {ticker}\n"
                    f"{side.upper()} @ {price:.3f} | ${size_usdc:.2f}",
                    msg_type="trade",
                )
            return result
        # Paper path: mirror market_order's bankroll guard. Without this,
        # market_maker's 30-second quoting loop drains bankroll indefinitely
        # into negative territory (observed -$817) because _paper_limit_order
        # only deducts without checking.
        if self.bankroll < size_usdc:
            logger.warning(f"Insufficient bankroll: ${self.bankroll:.2f} < ${size_usdc:.2f}, skipping {strategy} {ticker}")
            return {"status": "skipped", "reason": "insufficient_bankroll"}
        return await self._paper_limit_order(ticker, side, price, size_usdc, strategy, market_id)

    async def cancel_order(self, order_id):
        """Cancel an order. If the order was still open (not yet filled),
        restore its size+fee to bankroll. Filled orders don't refund because
        their capital is already committed to a position. Historically the
        missing refund caused requote-heavy strategies (market_maker,
        mentions_trader) to slowly drain bankroll."""
        refunded = await self._refund_if_open(order_id)
        if self.config.is_paper_mode:
            await self._db.update_order_status(order_id, "cancelled")
            return True
        ok = self._client.cancel_order(order_id)
        if not ok and refunded:
            # Kalshi refused the cancel — our refund was speculative. Roll back.
            self.bankroll -= refunded
            await self._persist_bankroll()
        return ok

    async def _refund_if_open(self, order_id: str) -> float:
        """If the order is in 'open'/'paper_open', credit size+fee back and
        return the refund amount. Otherwise return 0. No-op for unknown ids."""
        import aiosqlite
        async with aiosqlite.connect(self._db.db_path, timeout=30.0) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT status, price, size FROM orders WHERE order_id = ?", (order_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return 0.0
        if row["status"] not in ("open", "paper_open"):
            return 0.0
        price = float(row["price"])
        size = float(row["size"])
        if price <= 0:
            return 0.0
        count = max(1, int(size / price))
        fee = self.config.real_fee(count, int(price * 100))
        refund = size + fee
        self.bankroll += refund
        await self._persist_bankroll()
        return refund

    async def _paper_market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                                   minutes_to_start: Optional[float] = None):
        # DB-side dedup applies to:
        #   sports_* — single game has one outcome, re-entry is always a bug.
        #   ladder_* — single Kalshi event ladder leg has one outcome; V1
        #              shutdown_fade over-fired 13× on KXNUMSHUTDOWNS by
        #              skipping this guard. See known_bugs.md.
        #   macro_*  — single (FOMC bucket | CPI bucket) outcome — same
        #              one-shot invariant. C1 macro_event sub-strategy.
        #   crypto_* — single (crypto strike at close) outcome — same
        #              one-shot invariant. B2 crypto_fair_value sub-strategy.
        #   xvenue_* — single Kalshi market resolved by external signal — one
        #              trade per (strategy, market_id) per Polymarket alpha
        #              cycle. B1 xvenue_polymarket sub-strategy.
        # Other strategies (mentions, market_maker, spread_scanner)
        # legitimately re-enter the same ticker.
        if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
                strategy.startswith("macro_") or strategy.startswith("crypto_") or
                strategy.startswith("xvenue_")) \
                and await self._db.has_existing_trade(strategy, market_id):
            logger.warning(
                f"[PAPER] dedup skip: {strategy} {market_id} already has a trade, "
                f"in-memory dedup likely missed (restart / save race)"
            )
            return {"order_id": None, "status": "skipped",
                    "reason": "duplicate_trade", "size_usdc": size_usdc, "price": price}
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.real_fee(count, int(price * 100))
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        trade_id = await self._db.record_trade(
            strategy=strategy, market_id=market_id, direction=side,
            price=price, size=size_usdc, fee=fee, status="paper_filled",
            minutes_to_start=minutes_to_start,
        )
        logger.info(f"[PAPER] Market {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_filled", "size_usdc": size_usdc, "price": price, "trade_id": trade_id}

    # NOTE: limit orders do not currently participate in sports pre/live tagging.
    # The `record_trade` call happens later in settlement.simulate_paper_fills,
    # which has no access to commence_time. If a sports strategy ever enters via
    # limit_order, persist minutes_to_start on the orders row so settlement can
    # forward it.
    async def _paper_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        # DB-side dedup guard (sports_* + ladder_* + macro_* + crypto_* + xvenue_*) — see _paper_market_order.
        if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
                strategy.startswith("macro_") or strategy.startswith("crypto_") or
                strategy.startswith("xvenue_")) \
                and await self._db.has_existing_trade(strategy, market_id):
            logger.warning(
                f"[PAPER-LIMIT] dedup skip: {strategy} {market_id} already has a trade"
            )
            return {"order_id": None, "status": "skipped", "reason": "duplicate_trade"}
        order_id = f"paper_{uuid.uuid4().hex[:8]}"
        count = max(1, int(size_usdc / price))
        fee = self.config.real_fee(count, int(price * 100))
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        await self._db.record_order(
            order_id=order_id, market_id=market_id, strategy=strategy,
            side=side, price=price, size=size_usdc, order_type="limit", status="paper_open",
        )
        logger.info(f"[PAPER] Limit {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f} | bankroll: ${self.bankroll:.2f}")
        return {"order_id": order_id, "status": "paper_open", "price": price, "size_usdc": size_usdc}

    async def _live_market_order(self, ticker, side, size_usdc, price, strategy, market_id,
                                  minutes_to_start: Optional[float] = None):
        # DB-side dedup guard (sports_* + ladder_* + macro_* + crypto_* + xvenue_*) — see _paper_market_order.
        if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
                strategy.startswith("macro_") or strategy.startswith("crypto_") or
                strategy.startswith("xvenue_")) \
                and await self._db.has_existing_trade(strategy, market_id):
            logger.warning(
                f"[LIVE] dedup skip: {strategy} {market_id} already has a trade"
            )
            return {"status": "skipped", "reason": "duplicate_trade"}
        count = max(1, int(size_usdc / price))
        fee = self.config.real_fee(count, int(price * 100))
        # Real balance guard — prevent Kalshi from rejecting order + error spam
        balance = self._client.get_balance(max_age_s=60)
        if balance is None:
            logger.warning(f"LIVE order {strategy} {ticker}: balance check failed (API error), skipping")
            self._alert_balance_reject(
                strategy=strategy, ticker=ticker, reason="balance_check_failed",
            )
            return {"status": "failed", "reason": "balance_check_failed"}
        required = size_usdc + fee
        if balance < required:
            logger.warning(
                f"LIVE order {strategy} {ticker}: balance ${balance:.2f} < required ${required:.2f}, skipping"
            )
            self._alert_balance_reject(
                strategy=strategy, ticker=ticker, reason="insufficient_balance",
                balance=balance, required=required,
            )
            return {"status": "failed", "reason": "insufficient_balance"}
        # Kalshi market order requires yes_price. We bake 2¢ slippage tolerance
        # INTO the yes_price so a small ask drift doesn't reject the order:
        # - side=yes: signal price 33 + 2 = yes_price 35 (accept fills ≤ 35)
        # - side=no:  signal price 33 → NO cap 35 → yes_price = 100 - 35 = 65
        #   (accept fills where yes_price ≥ 65, i.e., NO cost ≤ 35)
        # buy_max_cost is left as a redundant per-contract safety cap.
        price_cents = int(round(price * 100))
        slippage = 2
        if side == "yes":
            yes_price_cents = price_cents + slippage
        else:
            yes_price_cents = 100 - (price_cents + slippage)
        # Clamp to Kalshi's valid range [1, 99]
        yes_price_cents = max(1, min(99, yes_price_cents))
        buy_max_cost = price_cents + slippage
        # P2 (2026-05-11): pre-record at status='placing' BEFORE Kalshi
        # market-order placement. If pre-record fails, no Kalshi call.
        trade_id = None
        last_db_error = None
        for attempt, delay in enumerate((0, 1, 2, 5)):
            if delay:
                await asyncio.sleep(delay)
            try:
                trade_id = await self._db.record_trade(
                    strategy=strategy, market_id=market_id, direction=side,
                    price=price, size=size_usdc, fee=fee,
                    status="placing", minutes_to_start=minutes_to_start,
                )
                if attempt > 0:
                    logger.warning(
                        f"[LIVE] pre-record (market) succeeded on retry "
                        f"#{attempt} for {strategy} {ticker}"
                    )
                break
            except Exception as e:
                last_db_error = e
                logger.warning(
                    f"[LIVE] pre-record (market) attempt #{attempt} failed "
                    f"for {strategy} {ticker}: {e}"
                )
        if trade_id is None:
            logger.error(
                f"[LIVE] pre-record (market) exhausted for {strategy} "
                f"{ticker}: {last_db_error}. Skipping Kalshi placement."
            )
            self._alert_dispatch_failure(
                strategy=strategy, ticker=ticker, side=side,
                order_id="not_placed", filled_count=0,
                size_usdc=size_usdc, price=price,
                error=f"pre-record failed: {last_db_error}",
            )
            return {"status": "failed", "reason": "pre_record_failed"}

        resp = self._client.place_market_order(
            ticker, side, count,
            yes_price_cents=yes_price_cents,
            buy_max_cost_cents=buy_max_cost,
        )
        if resp is None:
            try:
                await self._db.update_trade_post_fill(
                    trade_id, status="cancelled"
                )
            except Exception as upd_err:
                logger.warning(
                    f"[LIVE] post-reject status update failed (market) for "
                    f"{strategy} {ticker} trade={trade_id}: {upd_err}"
                )
            return {"status": "failed"}
        self._client.invalidate_balance_cache()
        order_id = resp.get("id", "unknown")
        # Extract realized fill price from Kalshi response if available
        realized = None
        fill_cost = resp.get("taker_fill_cost")  # cents (legacy market-order resp)
        fill_count = resp.get("taker_fill_count")
        if fill_cost and fill_count and fill_count > 0:
            realized = (fill_cost / fill_count) / 100.0
        # 2026-05-20: update size + fee from actual Kalshi fill (not signal).
        # Same fix as _live_limit_ttl_order line ~990 — settlement.calculate_pnl
        # needs effective stake to compute correct PnL, signal-based intended
        # stake systematically overstates losses on better-than-signal fills.
        if realized is not None and fill_count and fill_count > 0:
            actual_size = (fill_cost or 0) / 100.0  # cents → dollars
            actual_fee = self.config.real_fee(fill_count, int(round(realized * 100)))
        else:
            actual_size = size_usdc  # fallback to intended
            actual_fee = fee
        # UPDATE the pre-recorded row to 'filled' with realized data.
        last_db_error = None
        for attempt, delay in enumerate((0, 1, 2, 5)):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._db.update_trade_post_fill(
                    trade_id, status="filled",
                    size=actual_size, fee=actual_fee,
                    realized_fill_price=realized,
                )
                last_db_error = None
                if attempt > 0:
                    logger.warning(
                        f"[LIVE] post-fill update (market) succeeded on retry "
                        f"#{attempt} for {strategy} {ticker} order={order_id[:12]}"
                    )
                break
            except Exception as e:
                last_db_error = e
                logger.warning(
                    f"[LIVE] post-fill update (market) attempt #{attempt} failed "
                    f"for {strategy} {ticker} order={order_id[:12]}: {e}"
                )
        if last_db_error is not None:
            logger.critical(
                f"[LIVE] STALE PLACING (market): post-fill update failed. "
                f"strategy={strategy} ticker={ticker} side={side} "
                f"trade_id={trade_id} size=${size_usdc:.2f} "
                f"signal_price={price:.3f} order_id={order_id} error={last_db_error}"
            )
            self._alert_dispatch_failure(
                strategy=strategy, ticker=ticker, side=side,
                order_id=order_id, filled_count=count,
                size_usdc=size_usdc, price=price,
                error=str(last_db_error),
            )
            # Don't raise — row exists as 'placing', recovery via orphan check.
        # Deduct cost + fee so local bankroll stays in sync with Kalshi's real
        # balance, which already reflects both. Matches paper order semantics.
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        slip_note = f" realized={realized:.3f}" if realized is not None else ""
        logger.info(f"[LIVE] Market {side} | {strategy} | ${size_usdc:.2f} @ {price:.3f}{slip_note}")
        return {"order_id": order_id, "status": "filled", "size_usdc": size_usdc, "price": price, "trade_id": trade_id}

    async def _live_limit_ttl_order(self, ticker, side, size_usdc, price, strategy, market_id,
                                     minutes_to_start: Optional[float] = None):
        """Live limit order with a TTL — mirrors paper semantics (exact signal
        price, no slippage). Places a limit at signal price, polls fill status
        up to LIMIT_TTL_SECONDS, cancels remainder if not fully filled. Partial
        fills are recorded at actual size; unfilled orders return `skipped`
        (not `failed`, so strategy state isn't dirtied with a fake entry).
        """
        # DB-side dedup guard (sports_* + ladder_* + macro_* + crypto_* + xvenue_*) — see _paper_market_order.
        # (Currently this method is only called by sports strategies via
        # LIMIT_TTL_STRATEGIES, but the prefix gate makes the policy explicit.)
        if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
                strategy.startswith("macro_") or strategy.startswith("crypto_") or
                strategy.startswith("xvenue_")) \
                and await self._db.has_existing_trade(strategy, market_id):
            logger.warning(
                f"[LIVE-LIMIT] dedup skip: {strategy} {market_id} already has a trade"
            )
            return {"status": "skipped", "reason": "duplicate_trade"}
        count = max(1, int(size_usdc / price))
        price_cents = int(round(price * 100))
        aggression_cents = _aggression_cents_for(strategy)
        # Aggression: push yes_price so we're willing to pay up to
        # aggression_cents more per contract than signal. For YES buys
        # the yes_price IS our cost → bump up. For NO buys cost = 100 -
        # yes_price → bump yes_price DOWN to raise our NO cost cap.
        if side == "yes":
            yes_price_cents = price_cents + aggression_cents
        else:
            yes_price_cents = (100 - price_cents) - aggression_cents
        yes_price_cents = max(1, min(99, yes_price_cents))
        # Worst-case per-contract cost; used for fee/balance reservation.
        max_cost_cents = price_cents + aggression_cents
        max_cost_cents = max(1, min(99, max_cost_cents))
        expected_fee = self.config.real_fee(count, max_cost_cents)

        balance = self._client.get_balance(max_age_s=60)
        if balance is None:
            logger.warning(f"LIVE limit {strategy} {ticker}: balance check failed, skipping")
            self._alert_balance_reject(
                strategy=strategy, ticker=ticker, reason="balance_check_failed",
            )
            return {"status": "failed", "reason": "balance_check_failed"}
        # Reserve worst-case cost (signal + aggression), not signal size, so a
        # 1-2¢ taker fill near the margin doesn't overdraft Kalshi.
        max_size_usdc = count * max_cost_cents / 100.0
        required = max_size_usdc + expected_fee
        if balance < required:
            logger.warning(
                f"LIVE limit {strategy} {ticker}: balance ${balance:.2f} < required ${required:.2f}, skipping"
            )
            self._alert_balance_reject(
                strategy=strategy, ticker=ticker, reason="insufficient_balance",
                balance=balance, required=required,
            )
            return {"status": "failed", "reason": "insufficient_balance"}

        # P2 (2026-05-11): pre-record the trade at status='placing' BEFORE
        # sending to Kalshi. If the DB write fails, we never call Kalshi
        # → no orphan risk. After fill/cancel, the same row is UPDATEd to
        # its final status. has_existing_trade counts 'placing' as a live
        # trade (it excludes only 'cancelled'/'failed'), preventing double
        # fires during the brief in-flight window.
        signal_size_usdc = count * price  # signal-price size for DB
        signal_fee = self.config.real_fee(count, price_cents)
        trade_id = None
        last_db_error = None
        for attempt, delay in enumerate((0, 1, 2, 5)):
            if delay:
                await asyncio.sleep(delay)
            try:
                trade_id = await self._db.record_trade(
                    strategy=strategy, market_id=market_id, direction=side,
                    price=price, size=signal_size_usdc, fee=signal_fee,
                    status="placing", minutes_to_start=minutes_to_start,
                )
                if attempt > 0:
                    logger.warning(
                        f"[LIVE] pre-record succeeded on retry #{attempt} "
                        f"for {strategy} {ticker}"
                    )
                break
            except Exception as e:
                last_db_error = e
                logger.warning(
                    f"[LIVE] pre-record attempt #{attempt} failed for "
                    f"{strategy} {ticker}: {e}"
                )
        if trade_id is None:
            logger.error(
                f"[LIVE] pre-record exhausted for {strategy} {ticker}: "
                f"{last_db_error}. Skipping Kalshi placement to avoid orphan."
            )
            self._alert_dispatch_failure(
                strategy=strategy, ticker=ticker, side=side,
                order_id="not_placed", filled_count=0,
                size_usdc=signal_size_usdc, price=price,
                error=f"pre-record failed: {last_db_error}",
            )
            return {"status": "failed", "reason": "pre_record_failed"}

        resp = self._client.place_limit_order(ticker, side, yes_price_cents, count)
        if resp is None:
            # Kalshi rejected — mark the placeholder cancelled so dedup
            # auto-clears and the next scan can retry.
            try:
                await self._db.update_trade_post_fill(
                    trade_id, status="cancelled"
                )
            except Exception as upd_err:
                logger.warning(
                    f"[LIVE] post-reject status update failed for {strategy} "
                    f"{ticker} trade={trade_id}: {upd_err}"
                )
            return {"status": "failed"}
        self._client.invalidate_balance_cache()
        order_id = resp.get("order_id") or resp.get("id") or "unknown"
        ttl_seconds = _ttl_seconds_for(strategy)
        logger.info(
            f"[LIVE] Limit placed {side} {count}x {ticker} @ {yes_price_cents}¢ "
            f"(signal {price:.3f}) order={order_id[:12]} TTL={ttl_seconds}s "
            f"trade={trade_id}"
        )

        filled_count = 0
        realized = None
        elapsed = 0
        final_status = None
        while elapsed < ttl_seconds:
            await asyncio.sleep(LIMIT_TTL_POLL_INTERVAL)
            elapsed += LIMIT_TTL_POLL_INTERVAL
            info = self._client.get_order(order_id)
            if info is None:
                continue
            try:
                filled_count = int(float(info.get("fill_count_fp", 0) or 0))
            except (TypeError, ValueError):
                filled_count = 0
            remaining = None
            try:
                remaining = int(float(info.get("remaining_count_fp", 0) or 0))
            except (TypeError, ValueError):
                pass
            final_status = info.get("status")
            # Total fill cost = taker + maker. A limit that rests and gets
            # hit is booked as a maker fill with taker_fill_cost_dollars=0,
            # so reading only taker yielded realized=0.000 for resting fills.
            try:
                taker_cost = float(info.get("taker_fill_cost_dollars") or 0)
                maker_cost = float(info.get("maker_fill_cost_dollars") or 0)
            except (TypeError, ValueError):
                taker_cost = maker_cost = 0.0
            total_cost = taker_cost + maker_cost
            if total_cost > 0 and filled_count > 0:
                try:
                    realized = total_cost / filled_count
                except ZeroDivisionError:
                    realized = None
            if filled_count >= count or remaining == 0 or final_status in ("executed", "canceled"):
                break

        if filled_count < count:
            # Either TTL expired with resting qty, or Kalshi already finalized
            # (executed/canceled) but remaining_count is nonzero → best-effort cancel.
            cancelled = self._client.cancel_order(order_id)
            if not cancelled:
                # Re-read to see if a late fill landed during the cancel race
                info = self._client.get_order(order_id)
                if info is not None:
                    try:
                        filled_count = max(filled_count, int(float(info.get("fill_count_fp", 0) or 0)))
                    except (TypeError, ValueError):
                        pass
                    try:
                        tc = float(info.get("taker_fill_cost_dollars") or 0)
                        mc = float(info.get("maker_fill_cost_dollars") or 0)
                        total = tc + mc
                        if total > 0 and filled_count > 0:
                            realized = total / filled_count
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass

        if filled_count == 0:
            # TTL expired unfilled — mark the pre-recorded trade row as
            # cancelled so dedup auto-clears and the next scan can retry
            # the signal at a fresh price.
            try:
                await self._db.update_trade_post_fill(
                    trade_id, status="cancelled"
                )
            except Exception as upd_err:
                logger.warning(
                    f"[LIVE] cancelled-status update failed for {strategy} "
                    f"{ticker} trade={trade_id}: {upd_err} (row stays at 'placing')"
                )
            logger.info(
                f"[LIVE] Limit TTL expired unfilled: {strategy} {ticker} {side} @ {price:.3f} (status={final_status})"
            )
            return {"status": "skipped", "reason": "limit_ttl_unfilled", "order_id": order_id}

        # Size recorded in DB uses signal price so CLV and paper/live
        # comparison share the same reference frame; realized_fill_price
        # captures the real entry. Fees also use signal price for the same
        # reason (paper twin uses signal-based estimate).
        # 2026-05-20: use realized fill price for actual_size, not signal.
        # Kalshi limit orders can fill significantly below cap (08:58 atm_hold
        # got 40 contracts at realized $0.187 avg vs signal $0.50 — actual
        # paid $7.48, not the $20 intended). Recording intended stake left
        # settlement.calculate_pnl computing -$20 loss when actual was -$7.47.
        effective_price = realized if (realized is not None and realized > 0) else price
        effective_price_cents = int(round(effective_price * 100))
        actual_size = filled_count * effective_price
        actual_fee = self.config.real_fee(filled_count, effective_price_cents)
        # P2 (2026-05-11): pre-record row already exists at status='placing'
        # — now UPDATE it to 'filled' with realized data. If this fails, the
        # row stays at 'placing' (still a valid record), orphan_order_check
        # cron + alert will catch the stale state. Retry the UPDATE since
        # the row is small and the DB lock window should be brief.
        last_db_error = None
        for attempt, delay in enumerate((0, 1, 2, 5)):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._db.update_trade_post_fill(
                    trade_id, status="filled",
                    size=actual_size, fee=actual_fee,
                    realized_fill_price=realized,
                )
                last_db_error = None
                if attempt > 0:
                    logger.warning(
                        f"[LIVE] post-fill update succeeded on retry #{attempt} "
                        f"for {strategy} {ticker} order={order_id[:12]}"
                    )
                break
            except Exception as e:
                last_db_error = e
                logger.warning(
                    f"[LIVE] post-fill update attempt #{attempt} failed for "
                    f"{strategy} {ticker} order={order_id[:12]}: {e}"
                )
        if last_db_error is not None:
            # Row stuck at 'placing' but Kalshi has our fill. Orphan check
            # cron will flag this; manual reconciliation needed.
            logger.critical(
                f"[LIVE] STALE PLACING: post-fill update failed after Kalshi fill. "
                f"strategy={strategy} ticker={ticker} side={side} "
                f"trade_id={trade_id} filled_count={filled_count} "
                f"signal_price={price:.3f} order_id={order_id} error={last_db_error}"
            )
            self._alert_dispatch_failure(
                strategy=strategy, ticker=ticker, side=side,
                order_id=order_id, filled_count=filled_count,
                size_usdc=actual_size, price=price,
                error=f"post-fill UPDATE failed: {last_db_error}",
            )
            # Do NOT raise — Kalshi has the fill and we have a 'placing' row.
            # Recovery path: orphan check cron + manual UPDATE.
        # Bankroll mirrors Kalshi's real debit. With marketable-limit
        # aggression, realized can exceed signal by up to LIMIT_AGGRESSION_CENTS;
        # deducting signal-based size here would drift bankroll above Kalshi.
        kalshi_debit = filled_count * realized if realized is not None else actual_size
        self.bankroll -= (kalshi_debit + actual_fee)
        await self._persist_bankroll()
        slip_note = f" realized={realized:.3f}" if realized is not None else ""
        partial_note = f" (partial {filled_count}/{count})" if filled_count < count else ""
        logger.info(
            f"[LIVE] Limit filled {side} | {strategy} | ${actual_size:.2f} @ {price:.3f}{slip_note}{partial_note}"
        )
        return {
            "order_id": order_id, "status": "filled",
            "size_usdc": actual_size, "price": price,
            "trade_id": trade_id, "filled_count": filled_count,
        }

    async def _live_limit_order(self, ticker, side, price, size_usdc, strategy, market_id):
        # DB-side dedup guard (sports_* + ladder_* + macro_*) — see _paper_market_order.
        # IMPORTANT: this method is shared by mentions_trader, econ_trader,
        # market_maker, spread_scanner — those strategies legitimately
        # re-enter the same ticker. The prefix gate ensures only one-shot
        # strategies (sports games + ladder events + macro events + cross-venue) get the dedup guard.
        if (strategy.startswith("sports_") or strategy.startswith("ladder_") or
                strategy.startswith("macro_") or strategy.startswith("crypto_") or
                strategy.startswith("xvenue_")) \
                and await self._db.has_existing_trade(strategy, market_id):
            logger.warning(
                f"[LIVE-LIMIT] dedup skip: {strategy} {market_id} already has a trade"
            )
            return {"status": "skipped", "reason": "duplicate_trade"}
        count = max(1, int(size_usdc / price))
        # Kalshi API: yes_price is ALWAYS the YES contract price in cents.
        # When buying NO at price X, yes_price = 100 - X (since NO cost = 100 - yes_price).
        price_cents = int(price * 100)
        yes_price_cents = price_cents if side == "yes" else (100 - price_cents)
        resp = self._client.place_limit_order(ticker, side, yes_price_cents, count)
        if resp is None:
            return {"status": "failed"}
        self._client.invalidate_balance_cache()
        order_id = resp.get("id", "unknown")
        fee = self.config.real_fee(count, yes_price_cents)
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
        try:
            await self._db.record_order(
                order_id=order_id, market_id=market_id, strategy=strategy,
                side=side, price=price, size=size_usdc, order_type="limit", status="open",
            )
        except Exception as e:
            logger.warning(f"DB record_order failed (order already placed on Kalshi): {e}")
        return {"order_id": order_id, "status": "open", "price": price, "size_usdc": size_usdc}
