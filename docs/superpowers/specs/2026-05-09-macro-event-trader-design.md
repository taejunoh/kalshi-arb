# Macro Event Trader (C1) — Design Spec

**날짜**: 2026-05-09
**상태**: 설계 (사용자 검토 대기)
**Sub-project of**: [Kalshi Non-Sports 수익원 포트폴리오](2026-05-09-kalshi-non-sports-strategy-portfolio.md)
**참조**:
- A1 spec: `2026-05-09-ladder-consistency-arb-design.md` (KXFEDDECISION 와의 cross-strategy 관계)
- 메모리: `feedback_always_paper_first.md`, `sport_promotion_thresholds.md`
- 코드 패턴: `strategies/sports_underdog_v32.py` (edge-scaled sizing), `strategies/ladder_fed.py` (KXFEDDECISION 인접 전략)

---

## Context

shutdown_fade V1 폐기 prereq 분석 (2026-05-09) 의 핵심 결함이 "**시간확정성 결여**"
였음 — 정부-전체 funding deadline 이벤트가 listing 자체 안 됨, 결과 표본 0. C1 은
이 문제를 정확히 보완: **시간확정 macro release** (FOMC 14:00 ET / CPI 8:30 ET)
의 pre-event 윈도우에서 trade.

C1 universe probe 결과 (2026-05-09):

| Series | open | settled | 비고 |
|---|---|---|---|
| KXCPI | 68 | 21 | bucketed (T-0.3 ~ T0.5 등 value bucket) |
| KXFEDDECISION | 70 | 10 | bucketed (C25=cut 25bps, H0=hold 등) — A1.ladder_fed 도 target |
| KXJOBS / KXNFP / KXUNEMPLOY | 0 | 0 | Kalshi 가 listing 안 함 |

**원안 spec 의 Jobs 영역은 universe 부재**. C1 v0 = KXCPI + KXFEDDECISION 한정.

KXFEDDECISION 은 **즉시-해결형** (FOMC 결정 발표 즉시 settle) → "post-event reaction
fade" 적용 안 됨. **Pre-event consensus deviation** 만 유효 edge.

## Goals

1. CME FedWatch + Cleveland Fed Inflation Nowcast 를 외부 consensus anchor 로 활용
2. Kalshi implied prob vs consensus deviation 이 fee budget (~7c) + cushion 초과 시 단방향 directional fire
3. ladder_fed (A1) 와 cross-strategy 노출 캡으로 같은 ticker 양쪽 동시 fire 의 bankroll 위험 제어
4. 8 FOMC + 12 CPI = 20 events/yr 의 thin universe 를 paper 우선으로 검증 후 promotion

## Non-Goals

- Real-time CME futures direct calculation: 5-7일 추가 cost, 이벤트 빈도 낮아 ROI 낮음 → Phase 2 이상에서 고려
- BBG/Reuters paid consensus feed: paywall, MVP 부적합
- Post-event reaction fade: 즉시-해결형 universe 라 적용 불가
- KXJOBS / KXNFP: Kalshi 미상장
- KXFED (109 open exact-rate 마켓): 대부분 장기 close (2027/2028) → 단기 이벤트 trade 부적합. C1 v1 범위 외
- Multi-fire ladder thresholds (Approach 2): 복잡, dedup 우회 필요. v0 단일 fire

## Architecture

### 모듈 layout

```
core/
  macro_consensus.py            [NEW]
    FedWatchScraper                 # CME FedWatch API/HTML scrape
    CPINowcastScraper               # Cleveland Fed Nowcast scrape
    bucket_prob_from_normal()       # nowcast point + std → bucket prob (정규분포 적분)
    get_consensus(event_key, bucket) -> float | None

core/database.py                # 기존 + 신규 2 table
  macro_consensus
  macro_event_signals

core/order_manager.py
  has_existing_trade gate prefix 가드 확장: + "macro_*"
  신규 helper: _total_open_exposure(market_id) -> float (cross-strategy 합산)

core/config.py                  # 신규 env 5개
  MACRO_EVENT_MIN_DEVIATION                (default 0.10)
  MACRO_EVENT_MIN_DEPTH_USDC               (default 20)
  MACRO_EVENT_BASE_SIZE_USDC               (default 20)
  MACRO_EVENT_MAX_POSITION_PER_TICKER      (default 30)
  MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE    (default 80)
  MACRO_EVENT_WINDOW_MAX_HOURS             (default 336 = 14d)
  MACRO_EVENT_WINDOW_MIN_MINUTES           (default 30)
  MACRO_CONSENSUS_STALE_HOURS              (default 24)

strategies/
  macro_event.py                [NEW]
    MacroEventStrategy
      STRATEGY_NAME = "macro_event"
      Universe: KXCPI + KXFEDDECISION
      Window check + EV gate + edge-scaled sizing + dedup

scripts/
  macro_consensus_cron.py       [NEW]
    4시간 cron — FedWatch + CPI Nowcast scrape → DB upsert
  research/
    macro_event_signal_scanner.py  [NEW]
      Phase 0 dry-run: detect signals without firing, log to macro_event_signals
```

### 핵심 경계

- `core/macro_consensus.py` = **stateless scrapers + thin DB cache helper**. 단위
  테스트 쉽게.
- `strategies/macro_event.py` = **단일 sub-strategy** (`STRATEGY_NAME = "macro_event"`).
  ladder_fed 와 별도 strategy → 독립 paper/live 토글, 독립 dedup, 독립 promotion_eval.
- Cron 은 별개 프로세스 (main bot 와 격리, scrape 실패가 main loop 에 영향 안 줌).
- ladder_fed (A1) 와의 관계: 같은 ticker 에 양쪽 모두 fire 가능 (다른 strategy_name)
  → cross-strategy 노출 캡으로만 제어, 코드 충돌 없음.

## Edge thesis

각 KXCPI / KXFEDDECISION 마켓의 각 bucket leg 별로 Kalshi implied prob
((yes_ask + yes_bid) / 2) 를 외부 consensus prob 와 비교.

- Consensus 가 Kalshi 보다 큼 (Kalshi underprices YES) → BUY YES
- Consensus 가 Kalshi 보다 작음 (Kalshi overprices YES) → BUY NO

**EV 양수 조건** (per contract):

```
Buy YES at price X (ask) — fee = 0.07
Win prob = C (consensus), payoff = 1 - X - fee
Lose prob = 1 - C, payoff = -X - fee

EV = C·(1 - X - fee) + (1-C)·(-X - fee) = C - X - fee

EV > 0 ⟺ (C - X) > fee = 0.07
```

따라서 **MIN_DEVIATION 은 fee + cushion** 합쳐 default `0.10` (3c cushion). 5c
deviation 은 평균 음수 EV 라 paper 라도 의미 없음.

Direction 별 (BUY NO 시) 동일 math 대칭:
```
Buy NO at price (1 - X), implied prob NO wins = 1 - C
EV = (1-C) - (1-X) - fee = X - C - fee  > 0 ⟺ (X - C) > fee
```

즉 절대값 deviation > fee 인 동일 조건.

## Consensus pipeline

### FedWatch (CME)

URL pattern (비공식 백엔드):
```
https://www.cmegroup.com/CmeWS/mvc/tools/quoteboard/iframe/cmeFedWatchTool/probabilities/{date}
```
응답 (예시):
```json
{
  "meetings": [{"date": "2026-06-17", "probabilities": {
    "C50": 0.05, "C25": 0.65, "H0": 0.28, "H25": 0.02
  }}]
}
```

Scraper 책임:
- 4시간 cron → 모든 알려진 FOMC date 의 prob fetch
- HTTP 200 + JSON parse 성공 → DB `macro_consensus` upsert
- 실패 시 stale 유지 (이전 fetch 가 strategy 에서 staleness check 후 사용/거부)
- 24h 이상 stale 시 Telegram alert
- HTML scrape fallback 준비 (JSON endpoint 변경 시 — Phase 2)

### Cleveland Fed Inflation Nowcasting

URL:
```
https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting
```
HTML 테이블 (고정 구조). 추출:
- next-month CPI nowcast point estimate (e.g., +0.3% MoM)
- 표준편차 (페이지에 명시 또는 모델 fallback ≈ 0.10%)

Bucket prob 변환 (Kalshi T-0.3, T-0.2, ..., T0.5 등 0.1% 간격 bucket 가정):
- 정규분포 N(point, std) 가정
- bucket [a, b] 의 prob = Φ((b - point)/std) - Φ((a - point)/std)
- KXCPI 마켓 spec 의 bucket 경계 (open/closed) 정확히 맞춰야 함 — open question

### macro_consensus DB schema

```sql
CREATE TABLE macro_consensus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL,        -- e.g., "FOMC-26JUN", "CPI-26JUN"
    bucket TEXT NOT NULL,           -- e.g., "C25", "T0.3"
    prob REAL NOT NULL,
    source TEXT NOT NULL,           -- 'fedwatch' | 'clevelandfed'
    fetched_at TEXT NOT NULL,
    UNIQUE(event_key, bucket, source)
);
CREATE INDEX idx_mc_event_ts ON macro_consensus(event_key, fetched_at);
```

## Sizing

Edge-scaled (Approach 3, v32 underdog 검증된 패턴):
```
base = MACRO_EVENT_BASE_SIZE_USDC = $20
fire_size = base × clamp(|deviation| / MIN_DEVIATION, 0.5, 3.0)
         = $10 (dev=5c floor) ~ $60 (dev=30c+)
fire_size = min(fire_size, MACRO_EVENT_MAX_POSITION_PER_TICKER = $30)
```

**Cross-strategy 노출 캡**:
```
total_open_exposure = sum(open trade sizes for ticker, all strategies)
if total_open_exposure + fire_size > MACRO_EVENT_MAX_TICKER_TOTAL_EXPOSURE = $80:
    skip (ladder_fed 우선)
```

## Risk gates (fire 직전 순서대로)

1. **Window check** — `MACRO_EVENT_WINDOW_MIN_MINUTES (30) ≤ time_to_event ≤ MACRO_EVENT_WINDOW_MAX_HOURS (14d)`
2. **Consensus staleness** — `consensus.fetched_at` 가 `MACRO_CONSENSUS_STALE_HOURS (24)` 이상 오래되면 skip + Telegram alert
3. **Min deviation** — `|deviation| ≥ MACRO_EVENT_MIN_DEVIATION (0.10)`
4. **EV 양수** — `consensus_yes - entry_price - fee > 0` (또는 NO 방향 대칭). MIN_DEVIATION 통과해도 마지막 안전망
5. **Min depth** — Kalshi orderbook depth ≥ `MACRO_EVENT_MIN_DEPTH_USDC ($20)`
6. **Per-strategy dedup** — has_existing_trade(strategy="macro_event", market_id) 가드
7. **Cross-strategy 노출 캡** — 위 sizing 섹션
8. **Per-event circuit breaker** — 같은 event_key 누적 손실 > $50 → 추가 fire 차단
9. **Bankroll daily loss cap** — OrderManager 통합 (기존)

## Validation roadmap

### Phase 0 — Dry-run prevalence (paper 이전 필수 gate)

`scripts/research/macro_event_signal_scanner.py` (NEW):
- 1시간 cadence (event 빈도 낮으니 cadence 낮음)
- consensus 와 Kalshi implied 비교만, 실제 trade 안 침
- 신규 table `macro_event_signals` 에 logging
- **2주간 운영 후 분석**: 시그널 빈도, deviation 분포, 가상 EV
- consensus scrape 안정성 측정 (실패율 ≤ 1% 목표)

**Phase 0 통과 기준**:
- 일주일 평균 ≥ 2 signals
- 가상 EV (after fee) ≥ +$5 / week
- consensus scrape 실패율 ≤ 1%
- 미달 시 paper 보류 (consensus pipeline 손볼지, 전략 retire 할지 user 결정)

### Phase 1 — Paper deployment

- TRADING_MODE=paper, 1-2 cycle 의 FOMC + CPI event 운영 (~6주)
- Telegram daily summary
- 매 settle 후 ROI 누적 집계

### Phase 2 — Promotion criteria

| 지표 | 기준 |
|---|---|
| n_signals (settled) | ≥ 8 (1 cycle: 2 FOMC + 6 CPI) |
| ROI (after fee) | ≥ +5% |
| Consensus scrape uptime | ≥ 95% |
| Worst single trade loss | ≤ -$15 |
| Per-event drawdown | ≤ 30% of strategy bankroll allocation |

`scripts/promotion_eval.py` 에 `macro_event` entry 추가.

## Testing

### Unit (`tests/test_macro_consensus.py`)
- FedWatchScraper: mocked HTTP response → DB upsert 검증
- CPINowcastScraper: HTML fixture → bucket prob 변환 (정규분포 적분) 검증
- Edge case: empty response, malformed JSON, stale data, std=0 (deterministic)
- bucket 경계 inclusive/exclusive 처리

### Unit (`tests/test_macro_event_strategy.py`)
- Universe filter (KXCPI / KXFEDDECISION 만 통과, KXFED / KXBTC 차단)
- Window check (T-30min skip, T-2주 outside skip, T-1day fire)
- Edge math: deviation calc, EV gate (consensus stale → skip)
- Direction: consensus > implied → BUY YES, consensus < implied → BUY NO
- Sizing: edge-scaled clamp + per-ticker cap + cross-strategy 노출 캡
- Per-event circuit breaker (누적 손실 > $50 → 추가 fire 차단)

### Integration (`tests/test_macro_event_dedup.py`)
- macro_event 재진입 차단 (Task 7 패턴 + macro_* prefix 추가 검증)
- ladder_fed 와 cross-strategy 시나리오: 같은 ticker 에 ladder_fed 기존 $50 + macro_event 추가 $30 → 총 $80 도달 시 macro_event skip

### Manual paper smoke (배포 전)
- 실제 운영 5분 `macro_event_signal_scanner.py` 결과 확인
- 첫 paper fire 시 Telegram alert + DB row 검증

## Instrumentation

### macro_event_signals table

```sql
CREATE TABLE macro_event_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_key TEXT NOT NULL,
    bucket TEXT NOT NULL,
    kalshi_implied REAL NOT NULL,
    consensus REAL NOT NULL,
    deviation REAL NOT NULL,
    side TEXT,                 -- 'yes' | 'no' | NULL (skipped)
    expected_size_usdc REAL,
    action_taken TEXT NOT NULL  -- 'fired' | 'skip_ev' | 'skip_window' | 'skip_dedup' | 'skip_depth' | 'skip_exposure'
);
CREATE INDEX idx_mes_ticker_ts ON macro_event_signals(ticker, ts);
```

### Telegram alerts
- consensus scrape 24h+ stale → 즉시 alert
- 큰 deviation (>20c) 감지 시 → 즉시 alert (signal 강도 인지용)
- 첫 fire 즉시 alert
- 일일 daily summary (n_signals, n_fires, ROI, open positions)

### Dashboard
- macro_event sub-strategy panel (n_signals, n_fires, ROI, open positions)
- Consensus freshness indicator (마지막 scrape ts, age, 24h stale 시 빨간색)
- Upcoming events calendar (다음 FOMC + CPI 일정)

## Kill / rollback criteria

Live 배포 후:
- 일주일 누적 -$30 → 즉시 paper 강등 + 분석
- consensus scrape 24h+ downtime → 즉시 strategy halt
- DB dedup 사고 (같은 ticker 2건 이상 동일 시각 fire from macro_event) → 즉시 halt + post-mortem
- ladder_fed 와 cross-strategy 시나리오에서 총 노출 $80 cap 초과 사례 → 즉시 halt + cap 재설계

## Estimated effort

| Phase | Task | 예상 |
|---|---|---|
| 0a | core/macro_consensus.py + scrapers + unit tests | 1-1.5일 |
| 0b | DB schemas (macro_consensus + macro_event_signals) + helpers | 0.5일 |
| 0c | core/config.py knobs + dedup gate prefix 확장 | 0.5일 |
| 0d | strategies/macro_event.py + integration tests | 1일 |
| 0e | scripts/macro_consensus_cron.py + scripts/research/macro_event_signal_scanner.py | 0.5일 |
| 0f | main.py 등록 + smoke | 0.5일 |
| 0g | Phase 0 prevalence 운영 | 2주 (대기) |
| 1 | Phase 0 분석 + paper 배포 | 0.5일 + 6주 (대기) |
| 2 | Promotion eval 연결 + live ramp | 0.5일 + 점진 |

**총 active 작업**: ~5일. **총 elapsed**: ~8-10주 (FOMC + CPI cycle 검증 포함).

## Open questions

1. **CPI bucket 경계 정확한 spec** — Kalshi 의 T0.3 의 inclusive/exclusive 경계?
   마켓 페이지 확인 필요. 정규분포 적분 시 잘못된 가정 → consensus prob 가 systematically off.
2. **CME FedWatch JSON endpoint 안정성** — 비공식 endpoint 라 변경 가능. v0 는 JSON
   시도 후 실패 시 HTML scrape fallback 추가 (Phase 2 확장).
3. **Cleveland Fed Nowcast std dev source** — 페이지에 명시되어 있나? 없으면 historical
   error 분포로 fallback 필요. paper 검증 중 실측.
4. **Cross-strategy 노출 캡 enforcement 위치** — order_manager 에 helper 추가
   (선호) vs strategy 본체에서 query? 전자가 일관성 + 향후 다른 strategy 확장 시 재사용
   가능.

## Decision log

- **2026-05-09**: A1 ladder consistency arb 머지 (PR #1) → C1 brainstorm 시작.
- **2026-05-09**: Universe scope = "KXCPI + KXFEDDECISION (FOMC angle 차별화)" (user 선택).
- **2026-05-09**: Edge thesis = "External consensus anchor (FedWatch / Cleveland Fed)" (user 선택).
- **2026-05-09**: Consensus data acquisition = "Auto-scrape v0 부터" (user 선택, 1-2일 추가 투자).
- **2026-05-09**: Execution window = "T-2주 장기 윈도우" (user 선택).
- **2026-05-09**: Approach = "Approach 3 — Single fire + edge-scaled sizing" (user 선택).
- **2026-05-09**: MIN_DEVIATION = 0.10 (fee 0.07 + 3c cushion, EV 양수 조건 확보).
- **2026-05-09**: 14-task implementation 완료 (commits e1c7b4c..e496e1e on
  branch feat/macro-event-trader). 33 macro unit/integration tests green,
  V1 dedup integration test pass for macro_*, scanner --once dry run successful.

## Implementation deferrals (post-final-review)

본 v0 구현은 다음 항목을 deferred — Phase 0 prevalence 결과 분석 후
우선순위에 따라 후속 commit 으로 처리:

1. **Gate 8 — Per-event circuit breaker (필수, live 전)**: spec 의 "같은
   `event_key` 누적 손실 > $50 → 추가 fire 차단" 미구현. 현재 global
   `MAX_DAILY_LOSS` 만 보호. paper-only 에선 안전하나 live promotion 전
   `MacroEventStrategy.__init__` 에 settled-trade 누적 손실 캐시 + scan 시
   loss check 필요.
2. **Stale consensus Telegram alert**: `macro_event.py:190` 의 logger.warning
   은 있으나 `_order_manager._notifier.send(...)` 호출 없음. cron 실패 시
   silent — Phase 1 시작 전 wiring 권장.
3. **CPI bucket name verification (T0.0 vs T0)**: cron 이 `f"T{value}"`
   포맷 (예: `T0.0`) 사용하나 Kalshi 가 `T0` 로 listing 할 가능성. 첫 KXCPI
   실제 마켓 매칭 검증 필요. 불일치 시 normalize 로직 추가.
4. **`paper_open` exposure 포함 vs 제외**: 현재 limit order pending 도 노출
   에 합산 (보수적). spec intent 는 "committed capital" 만이라 약간 보수적
   bias. live 로 전환 시 재검토.
5. **Auto-discovery FOMC/CPI dates**: 현재 cron 의 `FOMC_DATES` /
   `CPI_DATES` 하드코딩. Kalshi /series API parse 로 자동화 가능 (Phase 2).
