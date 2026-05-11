# Crypto Fair-Value Trader (B2) — Design Spec

**날짜**: 2026-05-10
**상태**: 설계 (사용자 검토 대기)
**Sub-project of**: [Kalshi Non-Sports 수익원 포트폴리오](2026-05-09-kalshi-non-sports-strategy-portfolio.md)
**참조**:
- A1 spec: `2026-05-09-ladder-consistency-arb-design.md` (KXBTC/KXETH cumulative ladder, 같은 universe)
- C1 spec: `2026-05-09-macro-event-trader-design.md` (consensus pipeline + 9-gate + dedup gate prefix 패턴)
- 코드: `core/price_feed.py` (Binance WS BTC/ETH/SOL — B2 가 재사용), `core/macro_consensus.py:normal_cdf` (재사용)

---

## Context

A1.ladder_crypto 가 KXBTC/KXETH 의 internal monotonicity (cumulative ladder
consistency) 만 cover. B2 는 별도 angle — **외부 spot price (Coinbase/Binance
WS) 와 vol model 로 binary bucket 의 fair prob 를 직접 계산** → Kalshi
implied 와 비교 → directional trade. A1 spec open question 4 의 spot anchor
부분이 본격 sub-project 로 승격.

B2 universe probe 결과 (2026-05-10):

| Series | open | settled | 비고 |
|---|---|---|---|
| KXBTC | 200 | 200 | T<float> 형식, 시간별 close |
| KXETH | 165 | 200 | 동일 |
| KXXRP | 165 | 200 | 동일 (A1 미커버) |
| KXBTCD | 200 | 200 | daily 변종 |
| KXETHD | 165 | 200 | daily 변종 |

**중요 컨텍스트**:
1. **현 ticker 형식 = `T<float>` (예: T89749.99)** — A1.ladder_crypto 의 `_parse_suffix`
   가 `B<int>` 만 처리. **A1 가 prod 에서 모든 크립토 마켓 silent skip 중**.
   B2 spec 외 별도 cleanup commit 으로 A1 parser 수정 필요 (deferral 5번).
2. `core/price_feed.py` 가 이미 Binance WS BTC/ETH/SOL 구독 + realized vol
   계산 완비. B2 는 **PriceFeed 재사용** — 신규 WS 구현 불요.

## Goals

1. 5 series (KXBTC + KXETH + KXXRP + KXBTCD + KXETHD) 의 ATM ±N strikes 에
   대해 Kalshi implied vs lognormal-model fair prob deviation 감지
2. Vol source: Deribit ATM IV (BTC/ETH) → PriceFeed realized vol (XRP fallback)
3. Event-driven 1초 cadence — fast-scan task 별도 운영 (main strategy_loop 와
   asyncio.gather 로 병렬)
4. 9-stage risk gate (over-fire 방지 포함) + cross-strategy 노출 캡 (A1 와
   bankroll 충돌 제어)

## Non-Goals

- KXSOL: Kalshi 미상장 (현재 0 open). 추후 listing 시 universe 추가
- IV term structure interpolation (multi-expiry blend): v0 nearest-expiry
  단일 IV 만 사용 → Phase 2
- Smile / skew: ATM IV 만, OTM/ITM strike 별 IV 차이 무시
- Coinbase WS 직접: PriceFeed 의 Binance 인프라 재사용 (Phase 2 에서 Coinbase
  cross-check 가능)
- Multi-fire ladder thresholds: v0 단일 fire per (ticker, 60sec window)

## Architecture

### 모듈 layout

```
core/
  price_feed.py               # EXISTS — 변경 최소 (BINANCE_WS_URLS 에 XRP 추가)
  fair_value_math.py          [NEW]
    lognormal_cdf_above(spot, vol, strike, tau_years) -> float
    (core/macro_consensus.py:normal_cdf 재사용)
  deribit_iv.py               [NEW]
    DeribitIVScraper            # public REST → BTC + ETH ATM IV per expiry
    extract_atm_iv(chain, spot) -> dict[expiry, atm_iv]
  kalshi_orderbook_cache.py   [NEW]
    OrderbookCache              # 5-sec TTL dict[ticker, (yes_ask, ..., ts)]
    refresh_loop(tickers)       # 5-sec cadence concurrent fetch

core/database.py
  crypto_iv table              # (asset, expiry_date, atm_iv, fetched_at)
  crypto_fair_value_signals    # signal log (Phase 0 + live strategy)
  3 helpers (record/fetch IV, record signal)

core/order_manager.py
  has_existing_trade gate prefix 추가: + "crypto_*"
  (5 sites + canonical 코멘트 갱신)

core/config.py
  CRYPTO_FAIR_MIN_DEVIATION             (default 0.10)
  CRYPTO_FAIR_MIN_DEPTH_USDC            (default 30)
  CRYPTO_FAIR_BASE_SIZE_USDC            (default 20)
  CRYPTO_FAIR_MAX_POSITION_PER_TICKER   (default 30)
  CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE (default 80)
  CRYPTO_FAIR_RECENTLY_FIRED_SECS       (default 60)
  CRYPTO_FAIR_ATM_WINDOW_N              (default 5)
  CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS    (default 60)

strategies/
  crypto_fair_value.py        [NEW]
    CryptoFairValueStrategy
      STRATEGY_NAME = "crypto_fair_value"
      Universe: KXBTC, KXETH, KXXRP, KXBTCD, KXETHD
      Fast-scan asyncio task (1초 cadence, main.py 의 _strategy_loop 와 별도)

scripts/
  deribit_iv_cron.py          [NEW]
    4시간 cron — Deribit chain → ATM IV per expiry → DB upsert
  research/
    crypto_fair_value_signal_scanner.py [NEW]
      Phase 0 dry-run logger (1초 cadence, no fire)

main.py
  config.BINANCE_WS_URLS 에 XRP 추가 (SOL 유지 — 향후 KXSOL 대비)
  CryptoFairValueStrategy instance 생성
  _strategy_loop 에 등록 안 함 — 별도 task 로 실행
  _bot_tasks 에 self.crypto_fair_value.scan_loop() 추가
```

### 핵심 경계

- `core/price_feed.py`: 변경 최소 (URLs dict 에 XRP 한 줄 추가)
- `core/fair_value_math.py` = stateless 함수 only
- `core/deribit_iv.py` = stateless scraper + DB cache (C1 macro_consensus 패턴)
- `core/kalshi_orderbook_cache.py` = stateful (in-memory dict + asyncio refresh
  task)
- `strategies/crypto_fair_value.py` = 자체 fast-scan asyncio task. 기존
  strategy_loop 와 다른 cadence 라 별도 task 로 운영. main.py 가 둘 다 await
  asyncio.gather 로 관리.
- A1.ladder_crypto 와는 다른 STRATEGY_NAME → 같은 ticker 에 양쪽 fire 가능 →
  `db.total_open_exposure` 로 cross-strategy 노출 캡 적용 (C1 에서 이미 구현)

## Edge thesis

각 ticker `KX{asset}-{date}-T{strike}` 마켓의 fair prob:

```
S = current spot (PriceFeed.get_price(asset))
K = strike (ticker T<float> 파싱)
σ = annualized vol (Deribit IV or PriceFeed realized)
τ = (close_time - now) / (365.25 × 86400)  [years, drift r=0]

if τ ≤ MIN_TIME_TO_CLOSE_SECS / (365.25*86400): skip (close 임박)
if σ ≤ 0: skip (vol 부재)

fair_prob = Φ((ln(S/K) - 0.5·σ²·τ) / (σ·√τ))
```

여기서 Φ = `core/macro_consensus.py:normal_cdf`. KXBTCD/KXETHD (daily) 도
같은 공식, τ 가 더 큼.

**EV 양수 조건** (per contract, C1 와 동일):
```
EV = consensus_prob - entry_price - fee  (BUY YES 시)
EV = (1 - consensus_prob) - entry_price - fee  (BUY NO 시)

EV > 0 ⟺ |deviation| > fee = 0.07
→ MIN_DEVIATION = 0.10 (fee 0.07 + 3c cushion)
```

## Vol source pipeline

```python
def get_vol(asset: str, time_to_close_secs: int) -> tuple[float, str] | None:
    """Returns (vol, source) or None.

    Source priority:
    1. Deribit ATM IV (BTC/ETH only, fresh ≤ 6h)
    2. PriceFeed.get_volatility (XRP, or Deribit stale)
    """
    if asset in ("BTC", "ETH"):
        iv = await db.fetch_crypto_iv(asset)  # nearest expiry
        if iv and (now - iv.fetched_at).hours < 6:
            return iv.atm_iv, "deribit"
    rv = price_feed.get_volatility(asset)
    if rv:
        return rv, "realized"
    return None
```

### Deribit IV scraper

```
URL: https://www.deribit.com/api/v2/public/get_book_summary_by_currency
     ?currency=BTC&kind=option

응답: array of {instrument_name, mark_iv, ...}
parse: "BTC-13MAY26-93000-C" → asset=BTC, expiry=2026-05-13, strike=93000, type=C

ATM extraction per expiry:
  for each (asset, expiry):
    closest_call = min(by |strike - spot|) where type=C
    closest_put = min(by |strike - spot|) where type=P
    atm_iv = (closest_call.mark_iv + closest_put.mark_iv) / 2
```

### crypto_iv DB schema

```sql
CREATE TABLE crypto_iv (
    id INTEGER PRIMARY KEY,
    asset TEXT NOT NULL,            -- 'BTC' | 'ETH'
    expiry_date TEXT NOT NULL,      -- ISO date
    atm_iv REAL NOT NULL,           -- annualized
    fetched_at TEXT NOT NULL,
    UNIQUE(asset, expiry_date)
);
CREATE INDEX idx_civ_asset_ts ON crypto_iv(asset, fetched_at);
```

v0 simplification: nearest-expiry 단일 IV 사용. fetch_crypto_iv(asset) →
가장 빠른 미래 expiry 의 atm_iv 반환.

## Kalshi orderbook cache

이벤트 드리븐 1초 cadence + ATM ±N tickers 로 부담:
- N=5 strikes × 5 series = ~25 tickers monitor
- 5초마다 fetch = 5 req/sec, Kalshi rate limit (10 req/sec) 내

```python
class OrderbookCache:
    REFRESH_INTERVAL = 5.0
    STALE_THRESHOLD = 10.0

    cache: dict[ticker, OrderbookEntry(yes_ask, yes_bid, no_ask, no_bid,
                                        yes_depth, no_depth, ts)]

    async def refresh_loop(self, get_tickers: Callable[[], list[str]]):
        while running:
            tickers = get_tickers()  # dynamic list (ATM ±N can shift)
            for batch of tickers (concurrent ≤10):
                ob = await client.get_orderbook(ticker)
                cache[ticker] = OrderbookEntry(..., now)
            await asyncio.sleep(REFRESH_INTERVAL)

    def get(self, ticker) -> OrderbookEntry | None:
        e = cache.get(ticker)
        if not e or (now - e.ts) > STALE_THRESHOLD: return None
        return e
```

## 9-gate decision pipeline

Fast-scan task (1초 cadence) 안에서 each (asset, ticker) 별로:

1. **spot freshness** — `price_feed.is_stale(asset)` (5sec threshold) → skip
2. **vol availability** — get_vol() returns None → skip
3. **window** — τ_secs ≥ MIN_TIME_TO_CLOSE_SECS (default 60) → 통과
4. **depth** — orderbook entry depth (해당 side) ≥ MIN_DEPTH_USDC ($30)
5. **min deviation** — |fair - implied| ≥ MIN_DEVIATION (0.10)
6. **EV positive** — `consensus - entry - fee > 0` (마지막 안전망)
7. **recently_fired** — `not recently_fired(ticker, secs=60)` — over-fire 방지
8. **cross-strategy 노출** — `total_open_exposure(ticker) + size ≤ MAX_TICKER_TOTAL_EXPOSURE`
9. **per-strategy dedup** — has_existing_trade via OrderManager (crypto_* prefix)

## Sizing

C1 와 동일 edge-scaled:
```
base = CRYPTO_FAIR_BASE_SIZE_USDC = $20
fire_size = base × clamp(|deviation| / MIN_DEVIATION, 0.5, 3.0)
         = $10 (dev=5c floor) ~ $60 (dev=30c+)
fire_size = min(fire_size, MAX_POSITION_PER_TICKER = $30)
```

## Over-fire 방지

`recently_fired: dict[ticker, last_fire_ts]` (in-memory):
```
if now - last_fire_ts.get(ticker, 0) < CRYPTO_FAIR_RECENTLY_FIRED_SECS (60):
    skip (already fired in last minute)
```

Gate 9 의 DB has_existing_trade 는 settled 까지 영구 차단 (보다 엄격) 이라
recently_fired 는 1분 race condition 잡는 보조 가드. 같은 ticker 에 1분 내
양면 (long YES + short NO) 시그널이 동시 fire 하는 위험 차단.

## Validation roadmap

### Phase 0 — Dry-run prevalence (paper 이전 필수)

`scripts/research/crypto_fair_value_signal_scanner.py` (NEW):
- 1초 cadence (event-driven 동일) 별도 프로세스
- 모든 fair vs implied 비교 → `crypto_fair_value_signals` table 로깅, no fire
- **1주일 운영 후 분석**:
  - 시그널 빈도 per asset
  - deviation 분포 (5c, 10c, 20c+ buckets)
  - vol source breakdown (deribit vs realized)
  - 가상 EV (after fee) — settled 마켓 outcome 으로 backtest

**Phase 0 통과 기준** (asset 별):
- 일평균 ≥ 5 signals (event-driven 라 분당 다수 가능)
- 가상 EV (after fee) ≥ +$10 / week
- Deribit scrape 실패율 ≤ 5%
- 미달 asset 은 paper 보류 (per-asset toggle)

### Phase 1 — Paper deployment

- TRADING_MODE=paper, 2주 운영
- 매일 Telegram daily summary
- vol source split 별 ROI tracking (Deribit vs realized 가설 검증)

### Phase 2 — Promotion criteria

| 지표 | 기준 |
|---|---|
| n_signals (settled) | ≥ 50 (event-driven 빈도 높음) |
| ROI (after fee) | ≥ +5% |
| Per-asset breakdown | BTC/ETH/XRP 모두 +ROI 권장 (XRP 미달 시 retire) |
| Deribit scrape uptime | ≥ 95% |
| Worst single trade loss | ≤ -$15 |
| Max concurrent open (cross-strategy) | ≤ $200 |

`scripts/promotion_eval.py` 에 `crypto_fair_value` entry 추가.

## Testing

### Unit (`tests/test_fair_value_math.py`)
- lognormal_cdf_above: golden values from textbook (S=100 K=100 σ=0.2 τ=0.25
  → ~0.4602; S=110 K=100 σ=0.2 τ=0.25 → ~0.6515)
- Edge case: τ → 0 (deterministic), σ = 0 (zero vol), S = K (ATM)

### Unit (`tests/test_deribit_iv.py`)
- Mocked HTTP response → ATM IV extraction per expiry
- Edge case: empty chain, malformed instrument_name, missing mark_iv
- Multi-expiry chain → multiple (asset, expiry, atm_iv) rows

### Unit (`tests/test_orderbook_cache.py`)
- TTL behavior: fresh fetch → cache hit; > STALE_THRESHOLD → cache miss
- Refresh_loop runs given tickers list (mocked client)

### Unit (`tests/test_crypto_fair_value_strategy.py`)
- `_extract_strike` from ticker (T89749.99 → 89749.99)
- `_extract_atm_strikes` filter (current spot $90k → strikes $89.5k-$90.5k)
- `_compute_edge`: deviation > 0 → BUY YES, < 0 → BUY NO
- Sizing clamp (per-ticker cap)
- recently_fired guard

### Integration (`tests/test_crypto_fair_value_dedup.py`)
- crypto_fair_value 재진입 차단 (Task: dedup gate prefix → crypto_*)

### Integration (`tests/test_crypto_fair_value_e2e.py`)
- Mock PriceFeed + Deribit + Kalshi orderbook → strategy fires correctly
- Cross-strategy: A1.ladder_crypto 가 같은 ticker 에 $50 open + crypto_fair_value
  $30 추가 시도 → 노출 합산 80 도달 시 skip

### Manual paper smoke (배포 전)
- 5분 간 `crypto_fair_value_signal_scanner.py --once-batch` → DB 시그널 row 검증
- 첫 paper fire 시 Telegram alert + DB row 검증

## Instrumentation

신규 DB tables:
```sql
CREATE TABLE crypto_iv (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    atm_iv REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(asset, expiry_date)
);
CREATE INDEX idx_civ_asset_ts ON crypto_iv(asset, fetched_at);

CREATE TABLE crypto_fair_value_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    asset TEXT NOT NULL,
    spot REAL NOT NULL,
    strike REAL NOT NULL,
    vol REAL NOT NULL,
    vol_source TEXT NOT NULL,         -- 'deribit' | 'realized'
    time_to_close_secs INTEGER NOT NULL,
    fair_prob REAL NOT NULL,
    kalshi_implied REAL NOT NULL,
    deviation REAL NOT NULL,
    side TEXT,                         -- 'yes' | 'no' | NULL
    expected_size_usdc REAL,
    action_taken TEXT NOT NULL         -- 'fired' | 'skip_*'
);
CREATE INDEX idx_cfvs_ticker_ts ON crypto_fair_value_signals(ticker, ts);
```

Telegram alerts:
- Deribit scrape 24h+ stale → 즉시 alert
- 큰 deviation (>20c) 감지 시 → 즉시 alert
- 첫 fire 즉시 alert
- 일일 daily summary (n_signals, n_fires, ROI per asset)

Dashboard:
- crypto_fair_value sub-strategy panel (per-asset n_signals, n_fires, ROI,
  open positions)
- Vol source freshness indicator (Deribit last fetch + age)
- Spot freshness per asset (PriceFeed.is_stale 가시화)

## Kill / rollback criteria

Live 배포 후:
- 일주일 누적 -$30 → 즉시 paper 강등 + 분석
- Deribit scrape 24h+ downtime → 해당 asset (BTC/ETH) skip 자동 fallback to
  realized vol; downtime 일주일 지속 시 strategy halt
- Spot WS 24h+ disconnect → strategy halt (PriceFeed STALE_THRESHOLD 자동 차단)
- DB dedup 사고 (같은 ticker 2건 이상 동일 시각 fire from crypto_fair_value)
  → 즉시 halt + post-mortem
- A1.ladder_crypto 와 cross-strategy 시나리오에서 총 노출 $80 cap 초과 →
  즉시 halt + cap 재설계

## Estimated effort

| Phase | Task | 예상 |
|---|---|---|
| 0a | core/fair_value_math.py + tests | 0.5일 |
| 0b | core/deribit_iv.py + tests | 1일 |
| 0c | core/kalshi_orderbook_cache.py + tests | 0.5일 |
| 0d | DB schemas + dedup gate prefix 확장 + config knobs + main.py BINANCE XRP | 0.5일 |
| 0e | strategies/crypto_fair_value.py + tests | 1.5일 |
| 0f | scripts/deribit_iv_cron.py + scripts/research/crypto_fair_value_signal_scanner.py | 0.5일 |
| 0g | main.py 등록 (asyncio task wiring) + smoke | 0.5일 |
| 0h | Phase 0 prevalence 운영 | 1주 (대기) |
| 1 | Phase 1 paper | 2주 (대기) |
| 2 | Promotion eval 연결 + live ramp | 0.5일 + 점진 |

**총 active 작업**: ~5일. **총 elapsed**: ~3-4주.

## Open questions

1. **A1.ladder_crypto parser 이슈** (URGENT): `_parse_suffix` 가 `B<int>`
   만 처리하나 현 prod 가 `T<float>`. **B2 외 별도 commit** 으로 즉시
   수정 + A1 spec deferral 5번에 추가 기록. (1-line fix + parser test 1개,
   ~30분).
2. **ATM ±N 의 N 값**: default 5. paper 검증 후 BTC/ETH 별 조정 가능.
3. **Daily 변종 (KXBTCD/KXETHD)**: τ 가 24h+ 라 IV term structure 영향 ↑.
   v0 nearest-expiry IV 사용 → Phase 2 multi-expiry interpolation.
4. **Coinbase vs Binance WS 신뢰성**: 현 PriceFeed 가 Binance. 미국 USD
   reference 는 Coinbase 가 가까움. paper 운영 중 두 source spot 차이
   monitor 후 Coinbase 추가 검토.
5. **PriceFeed STALE_THRESHOLD = 5sec**: Binance WS 가 보통 sub-second tick →
   5sec stale 은 disconnect 의미. 현 default 유지.

## Decision log

- **2026-05-09**: A1 ladder consistency arb 머지 (PR #1) → A1.ladder_crypto 가
  `B<int>` parser 라 현 prod ticker (`T<float>`) 와 mismatch 발견.
- **2026-05-10**: C1 macro event trader 머지 (PR #2) → C1 패턴 (consensus
  pipeline + 9-gate + dedup gate prefix + cross-strategy 노출 캡) 을 B2 에
  재사용 결정.
- **2026-05-10**: B2 universe scope = "KXBTC/KXETH/KXXRP + KXBTCD/KXETHD"
  (user 선택 max coverage).
- **2026-05-10**: Vol model = "Deribit IV (BTC/ETH) + XRP realized fallback"
  (user 선택). v0 nearest-expiry 단일 IV.
- **2026-05-10**: Cadence = "Pure event-driven (1초 cadence fast-scan)" (user
  선택). Coinbase WS 자체 구현 대신 기존 PriceFeed (Binance) 재사용.
- **2026-05-10**: Architecture = Approach 2 (event-driven, 별도 asyncio
  task) (user 선택).
- **2026-05-10**: MIN_DEVIATION = 0.10 (C1 와 동일, fee 0.07 + 3c cushion).
- **2026-05-10**: 13-task implementation 완료 (commits 7828eec..608b4ee on
  branch feat/crypto-fair-value). 44 crypto unit/integration tests green.
  Phase 0 scanner 첫 dry run 에서 25 fired signals 즉시 감지 (Deribit IV BTC=12
  + ETH=12 expiries 정상 populated).

## Implementation deferrals (post-final-review)

본 v0 구현은 다음 항목을 deferred — Phase 0 prevalence 결과 분석 후 우선순위:

1. **Cold-start race minor remainder**: scan_loop 의 첫 ~5-10 sec 는 orderbook
   cache 가 비어있어 `_handle_market` 가 depth gate 에서 모두 skip. 두 번째
   iteration 부터 정상화. 별도 fix 불요 (operationally minor).
2. **High fire rate (25 fires/scan in dev smoke)**: lognormal 모델이 drift=0
   가정하나 실제 단기 BTC 마켓은 funding/futures basis 로 약간 drift. Phase 0
   2주 운영 후 (asset, time_to_close, deviation) bucket 별 settled outcome
   backtest. 실 EV 가 음수 시 MIN_DEVIATION 상향 또는 drift term 추가 검토.
3. **Live strategy → crypto_fair_value_signals 미기록**: A1/C1 동일 패턴 —
   현재는 Phase 0 scanner 에서만 logging. live strategy 는 trade row 만 기록.
   dashboard 의 skip funnel 분석 필요 시 _handle_market 에 record 추가.
4. **`expected_size_usdc` cap 차이**: log line 에 ev 값 미포함 (M1).
5. **Multi-expiry IV interpolation**: spec OQ3 — daily 변종 (KXBTCD/KXETHD)
   horizon 이 단기 IV 와 mismatch. 1-day IV 에 weekly IV 보간 (Phase 2).
6. **Deribit cron 의 spot reference fallback**: 현재 BTC=$90k, ETH=$3k 하드코딩
   (M3). cron 내부에서 Coinbase REST 호출하여 동적 fetch (Phase 2).
7. **Scanner action="fired" misnomer**: read-only scanner 가 "fired" 로 기록.
   "would_fire" 로 변경 더 명확 (M2).
