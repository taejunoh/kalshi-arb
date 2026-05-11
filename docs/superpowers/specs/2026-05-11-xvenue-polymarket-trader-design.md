# Cross-Venue Polymarket Trader (B1) — Design Spec

**날짜**: 2026-05-11
**상태**: 설계 (사용자 검토 대기)
**Sub-project of**: [Kalshi Non-Sports 수익원 포트폴리오](2026-05-09-kalshi-non-sports-strategy-portfolio.md)
**참조**:
- C1 spec: `2026-05-09-macro-event-trader-design.md` (9-gate 패턴 mirror)
- B2 spec: `2026-05-10-crypto-fair-value-trader-design.md` (외부 signal source → Kalshi 단방향 trade 패턴)
- 메모리: `feedback_always_paper_first.md`

---

## Context

C2 (SCOTUS opinion-day parser) 2026-05-11 폐기 — universe 부재. 5번째
sub-project 부산에서 **원안 portfolio 의 B1 Polymarket cross-venue arb** 선택.

B1 의 핵심 차별점:
1. A1/C1/B2 는 모두 Kalshi-internal 또는 single 외부 signal anchor (Coinbase WS,
   FedWatch, Cleveland Fed). B1 은 **다른 prediction market venue (Polymarket)**
   에서 alpha 시그널 추출 — 완전히 다른 alpha source.
2. Polymarket 은 Polygon 블록체인 + USDC + 다른 사용자 base → Kalshi 와
   가격 불일치 지속 가능.

**사용자 거주지 확인 (2026-05-11)**: New York 거주. Polymarket trading 은
2022 CFTC settlement 로 US 거주자 차단. 2024-2025 Polymarket 의 QCEX 인수로
US 합법 진입 시도 중이나 NY 특정 rollout 시점 불확실. → B1 v0 = **Polymarket
read-only signal source, Kalshi 에서만 trade**. trading integration 미고려.

## Goals

1. Polymarket public gamma-api 에서 마켓 가격 polling
2. Manual JSON mapping 으로 Polymarket slug ↔ Kalshi ticker prefix 매핑 (v0 시드 20개)
3. Polymarket implied prob ≠ Kalshi implied prob deviation ≥ MIN_DEVIATION (0.10) → Kalshi 단방향 directional fire
4. 9-gate pipeline (B2 1:1 mirror) + cross-strategy 노출 캡 with A1/C1
5. paper-first 운영 (LIVE_STRATEGIES 미등록)

## Non-Goals

- Polymarket 에서 실제 trading (US 거주 + USDC 운용 + KYC 부담)
- 자동 마켓 매칭 (LLM / fuzzy match): v0 manual JSON 만, 검증 후 Phase 2
- Polymarket WebSocket: gamma-api REST 1-min poll 로 충분 (Polymarket
  political/macro 마켓은 slow-moving)
- Reverse outcome 자동 검출: mapping JSON 에 hint 수동 명시
- 다른 prediction venues (Manifold, Kalshi 외): 별도 sub-project

## Architecture

### 모듈 layout

```
core/
  polymarket_scraper.py        [NEW]
    PolymarketScraper             # gamma-api.polymarket.com REST
    fetch_markets(slugs: list[str]) -> dict[slug, dict]
    fetch_single(slug: str) -> dict | None

  polymarket_mapping.py        [NEW]
    PolymarketKalshiMapping       # data/polymarket_kalshi_mapping.json 로드
    get_kalshi_prefix(slug) -> str | None
    get_outcome_match(slug) -> str  # 'direct' | 'reversed'
    get_all_slugs() -> list[str]

core/database.py
  polymarket_quotes table       # (slug, yes/no_price, volume, fetched_at)
  cross_venue_signals table     # (ticker, slug, kalshi/polymarket_implied, deviation, ...)
  4 helpers (record/fetch 양쪽)

core/order_manager.py
  has_existing_trade gate prefix 추가: + "xvenue_*"
  (5 sites + canonical 코멘트 갱신)

core/config.py
  XVENUE_MIN_DEVIATION              (default 0.10)
  XVENUE_MIN_DEPTH_USDC             (default 30)
  XVENUE_BASE_SIZE_USDC             (default 20)
  XVENUE_MAX_POSITION_PER_TICKER    (default 30)
  XVENUE_MAX_TICKER_TOTAL_EXPOSURE  (default 80)
  XVENUE_QUOTE_STALE_MINS           (default 15)
  XVENUE_MIN_POLYMARKET_VOLUME      (default 5000)
  XVENUE_RECENTLY_FIRED_SECS        (default 300)  # 5분 cooldown

data/
  polymarket_kalshi_mapping.json  [NEW, seed]
    [{
      "polymarket_slug": "...",
      "kalshi_ticker_prefix": "...",
      "outcome_match": "direct" | "reversed",
      "notes": "..."
    }, ...]

strategies/
  xvenue_polymarket.py         [NEW]
    XVenuePolymarketStrategy
      STRATEGY_NAME = "xvenue_polymarket"
      scan_and_execute (main strategy_loop 의 60-sec cadence 활용)

scripts/
  polymarket_quote_cron.py     [NEW]
    1-min cron — mapping 의 모든 slug fetch → DB upsert
  research/
    xvenue_signal_scanner.py   [NEW]
      Phase 0 dry-run logger (no-fire)
```

### 핵심 경계

- `core/polymarket_scraper.py` = **stateless REST client**, batch + single fetch
- `core/polymarket_mapping.py` = **JSON 로드 + lookup**, no DB
- `strategies/xvenue_polymarket.py` = **single sub-strategy** (`xvenue_polymarket`)
  → A1/C1/B2 와 다른 strategy_name. main 의 `_strategy_loop` 안에서 동작
  (B2 와 달리 별도 fast-scan asyncio task 불요, 60-sec poll 충분)
- A1/C1/B2 와 cross-strategy exposure cap 자연 공유 (`db.total_open_exposure`)

### B2 와 다른 점

B2 (crypto_fair_value) 는 1초 fast-scan asyncio task 별도 운영. B1 은
Polymarket 가 sub-second tick 없는 slow-moving → 기존 `_strategy_loop`
(60-sec cadence) 안에서 동작. 인프라 절약.

## Edge thesis

각 매핑된 (slug, kalshi_ticker_prefix) 쌍별로:

```
poly_quote = db.fetch_polymarket_quote(slug)  # 1-min cron 캐시
if poly_quote is None or stale(quote) > 15min: skip
if poly_quote.volume_24h < 5000: skip  # noise filter

# Polymarket implied YES probability
poly_yes_raw = poly_quote.yes_price
poly_yes = poly_yes_raw if outcome_match == "direct" else (1.0 - poly_yes_raw)

for kalshi_ticker in open_markets_matching(prefix):
    kalshi_yes_mid = (yes_ask + (1 - no_ask)) / 2
    deviation = poly_yes - kalshi_yes_mid  # signed

    if |deviation| < MIN_DEVIATION (0.10): skip
    if deviation > 0:
        side = "yes"; entry = yes_ask
        ev = poly_yes - entry - fee   # Polymarket prob 을 "consensus" 로 취급
    else:
        side = "no"; entry = no_ask
        ev = (1 - poly_yes) - entry - fee
    if ev <= 0: skip
```

**EV 양수 조건** (C1/B2 와 동일): `|deviation| > fee = 0.07` → MIN_DEVIATION
= 0.10 (3c cushion).

## Matching JSON schema

```jsonc
// data/polymarket_kalshi_mapping.json — v0 seed (20개)
[
  {
    "polymarket_slug": "will-trump-win-2028",
    "kalshi_ticker_prefix": "KXPRES-2028-T",
    "outcome_match": "direct",
    "notes": "Trump = YES on both venues"
  },
  {
    "polymarket_slug": "will-trump-win-2028",
    "kalshi_ticker_prefix": "KXPRES-2028-DEM",
    "outcome_match": "reversed",
    "notes": "Trump=YES (Poly) → Dem=NO (Kalshi)"
  },
  {
    "polymarket_slug": "fed-decision-june-2026",
    "kalshi_ticker_prefix": "KXFEDDECISION-26JUN-",
    "outcome_match": "direct",
    "notes": "FOMC decision buckets, both venues 25bp granularity"
  }
  // ...17 more mappings
]
```

**v0 seed 매핑 선정**: Phase 0 시작 전 user 가 다음 카테고리 손수 매핑:
- 미국 대선 (2028) Trump / Dem
- FOMC decision (직접 KXFEDDECISION 와 매칭)
- CPI release (Polymarket 이 listing 했다면)
- 의회 다수당 / Speaker 등 정치적 long-horizon
- Crypto price-target (Polymarket BTC week-end, Kalshi KXBTC 와 매칭)

**Hot-reload**: mapping JSON 변경은 다음 scan iteration 시 반영. 봇 재시작 불요.

## 9-gate decision pipeline

`scan_and_execute` 안 each (slug, kalshi_ticker) 별 순서:

1. **Polymarket quote freshness** — `fetched_at` ≤ XVENUE_QUOTE_STALE_MINS (15분)
2. **Polymarket volume gate** — volume_24h ≥ XVENUE_MIN_POLYMARKET_VOLUME ($5,000)
3. **Mapping resolved + Kalshi market open**
4. **Min deviation** — |dev| ≥ XVENUE_MIN_DEVIATION (0.10)
5. **EV positive** — `target_prob - entry - fee > 0`
6. **Min depth** — Kalshi orderbook depth (해당 side) ≥ XVENUE_MIN_DEPTH_USDC ($30)
7. **Per-strategy dedup** — has_existing_trade via OrderManager (`xvenue_*` prefix)
8. **Cross-strategy 노출 캡** — `total_open_exposure(ticker) + size ≤ XVENUE_MAX_TICKER_TOTAL_EXPOSURE` ($80)
9. **Recently-fired guard** — 같은 ticker 5분 내 fire 안 함

## Sizing

```
base = XVENUE_BASE_SIZE_USDC = $20
fire_size = base × clamp(|deviation| / MIN_DEVIATION, 0.5, 3.0)
         = $10 (dev=5c floor) ~ $60 (dev=30c+)
fire_size = min(fire_size, XVENUE_MAX_POSITION_PER_TICKER = $30)
```

## Polymarket pipeline

### gamma-api.polymarket.com 호출

```
URL: https://gamma-api.polymarket.com/markets?slug={slug}
```

Batch (한 번에 여러 slug):
```
https://gamma-api.polymarket.com/markets?slugs={s1},{s2},{s3}
```

응답 (single market):
```json
{
  "id": "...",
  "question": "Will Trump win 2028?",
  "slug": "will-trump-win-2028",
  "outcomes": ["Yes", "No"],
  "outcomePrices": ["0.42", "0.58"],
  "volume24hr": "125000",
  "active": true, "closed": false
}
```

**Scraper 책임**:
- 1-min cron → mapping JSON 의 모든 slug fetch
- `active=true AND closed=false` 인 마켓만 DB upsert
- `outcomes[0]=="Yes"` 보장 + yes_price = `outcomePrices[0]`
- HTTP 200 + JSON parse 성공 시 upsert, 실패 시 stale 유지
- 24h+ stale 시 Telegram alert

### polymarket_quotes DB schema

```sql
CREATE TABLE polymarket_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    yes_price REAL NOT NULL,
    no_price REAL NOT NULL,
    volume_24h REAL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX idx_pmq_slug_ts ON polymarket_quotes(slug, fetched_at);
```

## Over-fire 방지

`recently_fired: dict[ticker, last_fire_ts]` (in-memory):
- 같은 ticker 5분 이내 (XVENUE_RECENTLY_FIRED_SECS=300) 재진입 차단
- DB has_existing_trade (settled 까지 영구) 보다 보조 가드
- 5분 cooldown 은 60-sec cadence + Polymarket noise 잡기에 충분

## Validation roadmap (A1/C1/B2 패턴)

### Phase 0 — Dry-run prevalence (paper 이전 필수)

`scripts/research/xvenue_signal_scanner.py` (NEW):
- 60-sec cadence (별도 프로세스)
- 모든 매핑된 slug × kalshi_ticker 비교 → `cross_venue_signals` 로깅 (no-fire)
- **1주일 운영 후 분석**:
  - 시그널 빈도 per mapping
  - deviation 분포 (5c, 10c, 20c+ buckets)
  - 가상 EV (after fee, settled outcome backtest)
  - mapping 별 fire rate (잘못된 매핑 즉시 disable)

**Phase 0 통과 기준**:
- 일평균 ≥ 3 시그널 (mapping 20개 기준)
- 가상 EV (after fee) ≥ +$10 / week
- mapping 정확도 ≥ 90% (manual spot-check, 잘못된 매핑 인한 시그널 ≤ 10%)
- Polymarket scrape uptime ≥ 99%

### Phase 1 — Paper deployment

- TRADING_MODE=paper, 2주 운영
- 매일 Telegram daily summary
- mapping 별 ROI tracking → 음수 ROI mapping retire

### Phase 2 — Promotion criteria

| 지표 | 기준 |
|---|---|
| n_signals (settled) | ≥ 20 |
| ROI (after fee) | ≥ +5% |
| Mapping 정확도 (spot-check) | ≥ 95% |
| Polymarket scrape uptime | ≥ 99% |
| Worst single trade loss | ≤ -$15 |

`scripts/promotion_eval.py` 에 `xvenue_polymarket` entry 추가.

## Testing

### Unit (`tests/test_polymarket_scraper.py`)
- Mocked HTTP response → parse single + batch markets
- HTTP 404 / 500 / malformed JSON → None
- `closed=true` 또는 `active=false` → 제외
- outcomes 배열이 ["Yes", "No"] 아닌 경우 (e.g., multi-outcome) → 제외

### Unit (`tests/test_polymarket_mapping.py`)
- JSON 로드 → slug → kalshi_prefix lookup
- direct vs reversed outcome_match 동작
- hot-reload 시 새 mapping 즉시 반영

### Unit (`tests/test_xvenue_strategy.py`)
- Universe filter (mapping 안의 slug 만 통과)
- Edge math: deviation 양/음 방향 + EV gate
- Reverse outcome_match: poly Trump=YES 0.42 → kalshi Dem=YES 0.58 매핑 시
- Sizing clamp + per-ticker cap + cross-strategy cap
- recently_fired 5min guard

### Integration (`tests/test_xvenue_dedup.py`)
- xvenue_polymarket 재진입 차단 (dedup gate `xvenue_*` prefix 확장)

### Integration (`tests/test_xvenue_e2e.py`)
- Mocked Polymarket cron + Kalshi orderbook + DB cache
- direct + reversed outcome_match 양쪽 fire path 검증

### Manual paper smoke (배포 전)
- 5분 `xvenue_signal_scanner.py --once-batch` → DB row 검증
- 첫 paper fire 시 Telegram alert + DB 확인

## Instrumentation

신규 DB tables:
```sql
CREATE TABLE polymarket_quotes (...);  -- 위 정의

CREATE TABLE cross_venue_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    slug TEXT NOT NULL,
    kalshi_implied REAL NOT NULL,
    polymarket_implied REAL NOT NULL,
    deviation REAL NOT NULL,
    side TEXT,
    expected_size_usdc REAL,
    action_taken TEXT NOT NULL  -- 'fired' | 'skip_*'
);
CREATE INDEX idx_cvs_ticker_ts ON cross_venue_signals(ticker, ts);
```

Telegram alerts:
- Polymarket cron 24h+ stale → 즉시 alert
- 큰 deviation (>20c) → 즉시 alert
- 첫 fire 즉시 alert
- 일일 daily summary (mapping 별 fire count + ROI)

## Kill / rollback criteria

- 일주일 누적 -$30 → 즉시 paper 강등
- Polymarket scrape 24h+ downtime → strategy halt
- mapping 별 ROI -50% 이하 → 해당 mapping 자동 disable
- DB dedup 사고 → 즉시 halt + post-mortem

## Estimated effort

| Phase | Task | 예상 |
|---|---|---|
| 0a | core/polymarket_scraper.py + tests | 0.5일 |
| 0b | core/polymarket_mapping.py + JSON seed (20 mappings) | 0.5일 + 0.5일 |
| 0c | DB schemas + dedup gate prefix + config knobs | 0.5일 |
| 0d | strategies/xvenue_polymarket.py + tests | 1.5일 |
| 0e | scripts/polymarket_quote_cron.py + scripts/research/xvenue_signal_scanner.py | 0.5일 |
| 0f | main.py 등록 + smoke | 0.5일 |
| 0g | Phase 0 prevalence 운영 | 1주 (대기) |
| 1 | Phase 1 paper | 2주 (대기) |
| 2 | Promotion eval + live ramp | 0.5일 + 점진 |

**총 active 작업**: ~4.5일. **총 elapsed**: ~3-4주.

## Open questions

1. **Manual mapping 초기 시드 20개 선정**: user 가 polymarket.com 인기 마켓
   list + kalshi 매칭 후 추가. Phase 0 데이터 보면서 ROI 좋은 영역에 추가 매핑.
2. **gamma-api rate limit**: 명시 안 됨. 1-min cron + batch 호출이면 안전.
   실측 후 cadence 조정.
3. **Reverse outcome 정확도**: "Trump=YES on Poly = Dem=NO on Kalshi" 같은
   매핑은 수동 검증. Phase 0 manual spot-check 필요.
4. **Polymarket settlement timing 차이**: 두 venue 가 같은 결과로 settle 하지
   않을 수 있음. 단방향 거래라 영향 적지만 mapping note 에 명시 권장.
5. **3-way cross-strategy 노출** (A1.ladder_fed × C1.macro_event × B1.xvenue):
   $80 cap 이 적정한지 paper 검증.
6. **Polymarket trading integration (Phase 2)**: NY 거주자에게 Polymarket US
   합법 trading 시점 (QCEX rollout) 까지 대기. v0 결과 좋고 + US trading
   가능 시 two-venue arb 검토.

## Decision log

- **2026-05-11**: C2 SCOTUS 폐기 (universe 0) → 5번째 sub-project 부산.
- **2026-05-11**: B1 (원안 portfolio Polymarket cross-venue arb) 선택.
- **2026-05-11**: Trade venue = Kalshi 단방향 only (NY 거주자, Polymarket
  trading 차단). v2 trigger: Polymarket US 합법 trading 정식 launch.
- **2026-05-11**: Matching strategy = manual JSON (v0 정확도 최우선,
  fuzzy/LLM 은 Phase 2).
- **2026-05-11**: Cadence = 60-sec poll (기존 _strategy_loop 활용,
  Polymarket slow-moving political).
- **2026-05-11**: MIN_DEVIATION = 0.10 (A1/C1/B2 와 동일).
